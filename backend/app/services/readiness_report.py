"""Pre-launch readiness report: ties the deliverability checklist together.

Combines a fresh SPF/DKIM/DMARC check (app.services.deliverability_checker),
the active warmup schedule's standing (app.services.warmup_tracker), whether
the CAN-SPAM sender identity is still using placeholder configuration, and
whether SendGrid sandbox mode is on, into one overall verdict -- the
"can we actually flip this on" question the soft-launch report and the
SendGrid-sandbox-mode work earlier in this project both approached by hand.

Scoped to the EMAIL channel: it is the only channel this system can
actually dispatch (POST /outreach/drafts/{id}/send rejects WhatsApp/call-
script drafts outright, since those are documents for a human to use, not
something this system transmits -- see app/api/v1/outreach.py), so it is
the only channel a "ready to launch" question is really asking about.

This is computed live on every call, not persisted as its own row -- a
stale readiness report would be actively misleading right before flipping
a real switch. The DeliverabilityCheck it runs along the way IS persisted
(by deliverability_checker.run_deliverability_check), so check history
still accumulates even though the combined report itself does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.deliverability import DeliverabilityCheck
from app.services.deliverability import CheckStatus, combine_statuses
from app.services.deliverability_checker import run_deliverability_check
from app.services.outreach_policy import OutreachChannel
from app.services.warmup_tracker import WarmupStatusReport, build_warmup_status_report

#: Default/placeholder values that mean "not yet configured for real
#: sending." Mirrors the sentinels already used throughout settings/.env.example
#: (e.g. app.services.canspam rejects OUTREACH_PHYSICAL_ADDRESS's default the
#: same way).
_PLACEHOLDER_VALUES: dict[str, str] = {
    "sendgrid_api_key": "SG.REPLACE_ME",
    "outreach_physical_address": "REPLACE_ME",
    "outreach_public_base_url": "https://REPLACE_ME",
    "outreach_from_email": "bd@yourdomain.com",
}


@dataclass(frozen=True)
class SectionResult:
    """One section of the readiness report.

    Attributes:
        status: This section's result.
        messages: Human-readable findings.
    """

    status: CheckStatus
    messages: tuple[str, ...] = field(default_factory=tuple)


def _check_sender_identity() -> SectionResult:
    """Check whether CAN-SPAM sender identity settings are still placeholders.

    Returns:
        FAIL listing every setting still at its placeholder value (email
        drafting is already blocked by app.services.canspam in this case,
        so this mirrors that gate rather than introducing a new one); PASS
        if all are configured.
    """
    unset = [
        name for name, placeholder in _PLACEHOLDER_VALUES.items()
        if getattr(settings, name) == placeholder
    ]
    if unset:
        return SectionResult(
            status=CheckStatus.FAIL,
            messages=tuple(
                f"{name.upper()} is still the placeholder value." for name in unset
            ),
        )
    return SectionResult(status=CheckStatus.PASS, messages=("Sender identity is configured.",))


def _check_sandbox_mode() -> SectionResult:
    """Check SendGrid sandbox mode -- expected off for a real launch.

    Returns:
        WARN if sandbox mode is on (correct for testing, wrong for a real
        launch); PASS if off.
    """
    if settings.sendgrid_sandbox_mode:
        return SectionResult(
            status=CheckStatus.WARN,
            messages=(
                "SendGrid sandbox mode is ON: no real emails will be delivered. "
                "Correct for a test window, but must be turned off before a real "
                "launch.",
            ),
        )
    return SectionResult(status=CheckStatus.PASS, messages=("Sandbox mode is off.",))


def _check_warmup(warmup: WarmupStatusReport) -> SectionResult:
    """Check whether a warmup schedule is configured and on track.

    Args:
        warmup: The warmup status report for the email channel.

    Returns:
        MISSING if no schedule is configured -- sending at full volume from
        a domain/IP with no sending history is a known deliverability risk,
        so an unconfigured warmup is itself a pre-launch finding, not a
        neutral default. WARN if today's actual sends exceeded the planned
        cap (only possible for history predating this schedule, or a
        schedule created after some sends already happened today). PASS
        otherwise.
    """
    if warmup.schedule is None:
        return SectionResult(
            status=CheckStatus.MISSING,
            messages=(
                "No warmup schedule is configured for email. Sending at full "
                "volume from a domain/IP with no sending history risks being "
                "filtered as spam -- configure one via POST /warmup/plans before "
                "a real launch.",
            ),
        )
    if warmup.today is not None and not warmup.today.within_cap:
        return SectionResult(
            status=CheckStatus.WARN,
            messages=(
                f"Today's sends ({warmup.today.actual_sent}) have exceeded the "
                f"warmup plan's cap for today ({warmup.today.planned_cap}).",
            ),
        )
    if warmup.ramp_complete:
        return SectionResult(
            status=CheckStatus.PASS,
            messages=("Warmup ramp is complete; sending at full configured volume.",),
        )
    return SectionResult(
        status=CheckStatus.PASS,
        messages=(
            f"Warmup ramp in progress -- today's cap is "
            f"{warmup.today.planned_cap if warmup.today else 'not yet started'}.",
        ),
    )


@dataclass
class ReadinessReport:
    """The combined pre-launch readiness report.

    Attributes:
        domain: The domain checked.
        deliverability: The fresh SPF/DKIM/DMARC check just run.
        warmup: The email channel's warmup schedule standing.
        sender_identity: Whether CAN-SPAM sender identity is configured.
        sandbox_mode: Whether SendGrid sandbox mode is on.
        overall_status: The worst status across every section.
    """

    domain: str
    deliverability: DeliverabilityCheck
    warmup: WarmupStatusReport
    sender_identity: SectionResult
    sandbox_mode: SectionResult
    overall_status: CheckStatus


async def build_readiness_report(
    db: AsyncSession, domain: str | None = None
) -> ReadinessReport:
    """Assemble the pre-launch readiness report.

    Args:
        db: Active database session. Caller is responsible for committing
            (the deliverability check this runs is persisted via flush,
            same convention as every other service in this codebase).
        domain: The domain to check. Defaults to
            app.services.deliverability_checker.resolve_check_domain().

    Returns:
        The combined report.

    Raises:
        ValueError: If no domain could be determined (propagated from
            run_deliverability_check).
    """
    deliverability = await run_deliverability_check(db, domain)
    warmup = await build_warmup_status_report(db, OutreachChannel.EMAIL)
    sender_identity = _check_sender_identity()
    sandbox_mode = _check_sandbox_mode()
    warmup_section = _check_warmup(warmup)

    overall_status = combine_statuses(
        [
            deliverability.overall_status,
            warmup_section.status,
            sender_identity.status,
            sandbox_mode.status,
        ]
    )

    return ReadinessReport(
        domain=deliverability.domain,
        deliverability=deliverability,
        warmup=warmup,
        sender_identity=sender_identity,
        sandbox_mode=sandbox_mode,
        overall_status=overall_status,
    )
