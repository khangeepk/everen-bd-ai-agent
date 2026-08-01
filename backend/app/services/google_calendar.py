"""Google Calendar API client for the single shared sales calendar.

Talks to Google's OAuth token endpoint and the Calendar v3 REST API
directly over ``httpx`` -- already a dependency of this codebase -- rather
than adding ``google-api-python-client``/``google-auth``. Mirrors the
DNS-over-HTTPS precedent in :mod:`app.services.dns_lookup`: both are a
plain HTTPS JSON API, so a full client SDK buys nothing here that a couple
of typed wrapper methods don't.

This is the *one shared sales calendar* model (see the ``google_calendar_*``
settings' docstrings on :class:`app.core.config.Settings`): a single
Google account's OAuth refresh token, obtained once by an admin outside
this application, not a per-rep "connect your calendar" flow. Every
booking link this app generates checks and books against this one
calendar, identified by ``settings.google_calendar_id``.

Access tokens are fetched fresh on every call rather than cached in
memory or the database. This is a deliberate simplicity/latency tradeoff:
booking-flow calls (checking free/busy, creating one event) are low
volume and not latency-sensitive enough to justify the added state of a
token cache with its own expiry bookkeeping, and it means the client
holds no long-lived secret in memory beyond the refresh token itself. If
call volume grows, add caching keyed on the returned ``expires_in`` --
this is a documented tradeoff, not an oversight.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.core.config import settings
from app.services.booking_slots import BusyInterval

logger = logging.getLogger(__name__)

OAUTH_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

DEFAULT_TIMEOUT_SECONDS = 15.0

#: The value every google_calendar_* secret setting defaults to before an
#: admin configures a real one -- see Settings.google_calendar_client_id
#: and friends.
_UNCONFIGURED_PLACEHOLDER = "REPLACE_ME"


class GoogleCalendarError(RuntimeError):
    """Raised when a Google Calendar API call fails."""


class GoogleCalendarNotConfiguredError(GoogleCalendarError):
    """Raised when the shared calendar's credentials haven't been set up.

    Distinct from :class:`GoogleCalendarError` so callers (the booking API
    routes) can return a clear "booking isn't available yet" response
    rather than a generic 500, without string-matching an error message.
    """


@dataclass(frozen=True)
class CalendarEventResult:
    """The outcome of creating an event on the shared calendar.

    Attributes:
        event_id: Google Calendar's id for the created event.
        html_link: A human-viewable URL to the event, for internal
            reference (e.g. in the Meeting record) -- not sent to the
            prospect, who gets Google's own invite email.
    """

    event_id: str
    html_link: str


def is_configured() -> bool:
    """Whether the shared calendar's credentials have been set up.

    A cheap, side-effect-free check callers (e.g.
    app.services.booking_link_scanner) can use to decide whether it's worth
    generating a booking link at all, without instantiating a client or
    making a network call.

    Returns:
        True if none of the ``google_calendar_*`` secret settings are still
        the default ``REPLACE_ME`` placeholder.
    """
    return not any(
        value == _UNCONFIGURED_PLACEHOLDER
        for value in (
            settings.google_calendar_client_id,
            settings.google_calendar_client_secret,
            settings.google_calendar_refresh_token,
        )
    )


class GoogleCalendarClient:
    """Client for the one shared sales calendar."""

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        calendar_id: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the client.

        Args:
            client_id: OAuth client id. Defaults to settings.
            client_secret: OAuth client secret. Defaults to settings.
            refresh_token: OAuth refresh token for the shared calendar's
                Google account. Defaults to settings.
            calendar_id: Which calendar on that account to use. Defaults
                to settings.
            timeout_seconds: Per-request timeout.
        """
        self._client_id = client_id or settings.google_calendar_client_id
        self._client_secret = client_secret or settings.google_calendar_client_secret
        self._refresh_token = refresh_token or settings.google_calendar_refresh_token
        self._calendar_id = calendar_id or settings.google_calendar_id
        self._timeout = timeout_seconds

    def _require_configured(self) -> None:
        """Check the shared calendar's credentials have been set up.

        Raises:
            GoogleCalendarNotConfiguredError: If any credential is still
                the default placeholder.
        """
        # Checks this instance's own resolved credentials (which may have been
        # passed explicitly to __init__ rather than taken from settings),
        # not the module-level is_configured() helper -- that one only ever
        # reflects settings, so it wouldn't catch a caller-supplied
        # placeholder value.
        unconfigured = {
            name
            for name, value in (
                ("google_calendar_client_id", self._client_id),
                ("google_calendar_client_secret", self._client_secret),
                ("google_calendar_refresh_token", self._refresh_token),
            )
            if value == _UNCONFIGURED_PLACEHOLDER
        }
        if unconfigured:
            raise GoogleCalendarNotConfiguredError(
                "Google Calendar is not configured -- the following settings are "
                f"still REPLACE_ME: {', '.join(sorted(unconfigured))}. An admin must "
                "obtain a refresh token for the shared sales calendar's Google "
                "account before booking links can work."
            )

    async def _get_access_token(self) -> str:
        """Exchange the shared refresh token for a fresh access token.

        Returns:
            A short-lived OAuth access token.

        Raises:
            GoogleCalendarNotConfiguredError: If credentials aren't set up.
            GoogleCalendarError: If the token exchange fails.
        """
        self._require_configured()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    OAUTH_TOKEN_ENDPOINT,
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": self._refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.exception("Google OAuth token refresh failed")
            raise GoogleCalendarError(f"Google OAuth token refresh failed: {exc}") from exc

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GoogleCalendarError("Google OAuth token response had no access_token")
        return access_token

    async def get_busy_intervals(
        self, time_min: datetime, time_max: datetime
    ) -> list[BusyInterval]:
        """Fetch the shared calendar's busy intervals in a time window.

        Args:
            time_min: Start of the window to check, timezone-aware.
            time_max: End of the window to check, timezone-aware.

        Returns:
            The calendar's busy intervals within the window, as
            :class:`app.services.booking_slots.BusyInterval` so the result
            feeds directly into
            :func:`app.services.booking_slots.compute_available_slots`.

        Raises:
            GoogleCalendarNotConfiguredError: If credentials aren't set up.
            GoogleCalendarError: If the free/busy query fails.
        """
        access_token = await self._get_access_token()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{CALENDAR_API_BASE}/freeBusy",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "timeMin": time_min.isoformat(),
                        "timeMax": time_max.isoformat(),
                        "items": [{"id": self._calendar_id}],
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.exception("Google Calendar freeBusy query failed")
            raise GoogleCalendarError(f"Google Calendar freeBusy query failed: {exc}") from exc

        calendars = payload.get("calendars", {})
        calendar_entry = calendars.get(self._calendar_id, {})
        error = calendar_entry.get("errors")
        if error:
            msg = (
                f"Google Calendar freeBusy error for {self._calendar_id}: "
                f"{error}"
            )
            raise GoogleCalendarError(msg)

        raw_busy = calendar_entry.get("busy", [])
        intervals = [
            BusyInterval(
                start=datetime.fromisoformat(entry["start"]),
                end=datetime.fromisoformat(entry["end"]),
            )
            for entry in raw_busy
        ]
        logger.info(
            "Fetched shared calendar busy intervals",
            extra={"calendar_id": self._calendar_id, "interval_count": len(intervals)},
        )
        return intervals

    async def create_event(
        self,
        *,
        summary: str,
        description: str,
        start: datetime,
        end: datetime,
        attendee_email: str,
    ) -> CalendarEventResult:
        """Create a booked meeting on the shared calendar.

        Sends Google's own invite email to ``attendee_email`` via
        ``sendUpdates=all`` -- this is the mechanism that actually notifies
        the prospect their meeting is booked. See the booking confirm route
        (app.api.v1.booking) for why this transactional invite is not
        subject to AGENTS.md section 8's human-approval gate: it is
        triggered by the prospect's own affirmative action on a
        token-scoped confirmation endpoint, not agent-generated outreach
        content.

        Args:
            summary: Event title.
            description: Event body text.
            start: Meeting start, timezone-aware.
            end: Meeting end, timezone-aware.
            attendee_email: The prospect's email address, invited as an
                attendee.

        Returns:
            The created event's id and viewable link.

        Raises:
            GoogleCalendarNotConfiguredError: If credentials aren't set up.
            GoogleCalendarError: If event creation fails.
        """
        access_token = await self._get_access_token()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{CALENDAR_API_BASE}/calendars/{self._calendar_id}/events",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"sendUpdates": "all"},
                    json={
                        "summary": summary,
                        "description": description,
                        "start": {"dateTime": start.isoformat()},
                        "end": {"dateTime": end.isoformat()},
                        "attendees": [{"email": attendee_email}],
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.exception("Google Calendar event creation failed")
            raise GoogleCalendarError(f"Google Calendar event creation failed: {exc}") from exc

        event_id = payload.get("id")
        html_link = payload.get("htmlLink", "")
        if not isinstance(event_id, str) or not event_id:
            raise GoogleCalendarError("Google Calendar event creation response had no id")

        logger.info(
            "Created shared calendar event",
            extra={"calendar_id": self._calendar_id, "event_id": event_id},
        )
        return CalendarEventResult(event_id=event_id, html_link=html_link)
