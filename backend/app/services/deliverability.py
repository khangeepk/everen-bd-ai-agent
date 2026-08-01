"""Pure SPF/DKIM/DMARC record parsing and validation.

Standard library only. Given the raw TXT (and, for DKIM, optionally CNAME)
records already fetched from DNS, this module decides whether each record is
present, well-formed, and configured the way real mailbox providers expect
before trusting a sending domain -- so that logic is testable without a
network call. The actual DNS lookups live in :mod:`app.services.dns_lookup`;
the DB-aware orchestration that calls both and persists a result lives in
:mod:`app.services.deliverability_checker`.

None of this ever sends anything or changes what is allowed to send -- it
only answers "is this domain's authentication configured correctly," the
same read-only relationship :mod:`app.services.canspam` has to sending.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field


class CheckStatus(str, enum.Enum):
    """Result of one deliverability check.

    Ordered worst-to-best is FAIL > MISSING > WARN > PASS for the purposes
    of :func:`combine_statuses` -- a record that exists but is misconfigured
    (FAIL) is treated as more urgent than one that simply hasn't been added
    yet (MISSING), since a broken record can actively cause deliverability
    problems whereas an absent one usually just means "not set up yet."
    """

    PASS = "pass"
    WARN = "warn"
    MISSING = "missing"
    FAIL = "fail"


#: Combination priority, most severe first. See CheckStatus's docstring.
_SEVERITY_ORDER: tuple[CheckStatus, ...] = (
    CheckStatus.FAIL,
    CheckStatus.MISSING,
    CheckStatus.WARN,
    CheckStatus.PASS,
)


def combine_statuses(statuses: list[CheckStatus]) -> CheckStatus:
    """Combine several check results into one overall status.

    Args:
        statuses: The statuses to combine. Empty is treated as MISSING --
            there is nothing to report a pass on.

    Returns:
        The most severe status present.
    """
    if not statuses:
        return CheckStatus.MISSING
    present = set(statuses)
    for candidate in _SEVERITY_ORDER:
        if candidate in present:
            return candidate
    return CheckStatus.MISSING  # pragma: no cover -- unreachable, CheckStatus is exhaustive


@dataclass(frozen=True)
class SpfResult:
    """Outcome of checking a domain's SPF record.

    Attributes:
        status: Overall result.
        record: The raw SPF record used, if exactly one valid one was found.
        messages: Human-readable findings, worst-relevant first.
    """

    status: CheckStatus
    record: str | None
    messages: tuple[str, ...] = field(default_factory=tuple)


#: A syntactically plausible SPF record starts with this, per RFC 7208.
_SPF_PREFIX = "v=spf1"

#: The terminal "all" mechanism and its qualifier, if present.
_ALL_MECHANISM = re.compile(r"([+\-~?])?all\b")

_ALL_QUALIFIER_MEANING: dict[str, str] = {
    "-": "hard fail (recommended)",
    "~": "soft fail",
    "?": "neutral (provides no protection)",
    "+": "pass -- allows anyone to spoof this domain, effectively no SPF at all",
}


def parse_spf_record(txt_records: list[str]) -> SpfResult:
    """Validate a domain's SPF configuration from its TXT records.

    Args:
        txt_records: Every TXT record found at the domain's apex (not just
            the SPF-looking ones -- SPF records are simply the ones
            starting with "v=spf1" among however many TXT records a domain
            has).

    Returns:
        The parsed result.
    """
    spf_records = [r for r in txt_records if r.strip().lower().startswith(_SPF_PREFIX)]

    if not spf_records:
        return SpfResult(
            status=CheckStatus.MISSING,
            record=None,
            messages=("No SPF record found. Add a TXT record starting with 'v=spf1'.",),
        )

    if len(spf_records) > 1:
        # RFC 7208 section 4.5: more than one SPF record is a PermError --
        # receiving servers must fail SPF entirely, not merge or pick one.
        return SpfResult(
            status=CheckStatus.FAIL,
            record=None,
            messages=(
                f"{len(spf_records)} SPF records found at this domain. RFC 7208 "
                "requires exactly one -- receiving mail servers will treat this as "
                "a permanent SPF failure (PermError), not merge them. Remove all "
                "but one.",
            ),
        )

    record = spf_records[0].strip()
    messages: list[str] = []

    match = _ALL_MECHANISM.search(record)
    if match is None:
        return SpfResult(
            status=CheckStatus.WARN,
            record=record,
            messages=(
                "SPF record has no 'all' mechanism at the end. Without one, "
                "receiving servers fall through to a default that provides no "
                "real protection.",
            ),
        )

    qualifier = match.group(1) or "+"
    meaning = _ALL_QUALIFIER_MEANING[qualifier]
    if qualifier == "+":
        return SpfResult(
            status=CheckStatus.FAIL,
            record=record,
            messages=(f"SPF record ends in '+all' ({meaning}).",),
        )
    if qualifier == "?":
        return SpfResult(
            status=CheckStatus.WARN,
            record=record,
            messages=(f"SPF record ends in '?all' ({meaning}).",),
        )
    if qualifier == "~":
        messages.append(f"SPF record ends in '~all' ({meaning}). '-all' is stricter.")
        return SpfResult(status=CheckStatus.WARN, record=record, messages=tuple(messages))

    messages.append(f"SPF record ends in '-all' ({meaning}).")
    return SpfResult(status=CheckStatus.PASS, record=record, messages=tuple(messages))


@dataclass(frozen=True)
class DmarcResult:
    """Outcome of checking a domain's DMARC record.

    Attributes:
        status: Overall result.
        record: The raw DMARC record used, if found.
        policy: The parsed ``p=`` policy tag (none/quarantine/reject), if any.
        messages: Human-readable findings.
    """

    status: CheckStatus
    record: str | None
    policy: str | None
    messages: tuple[str, ...] = field(default_factory=tuple)


_DMARC_PREFIX = "v=dmarc1"
_DMARC_POLICY = re.compile(r"\bp=(none|quarantine|reject)\b", re.IGNORECASE)
_DMARC_RUA = re.compile(r"\brua=([^;]+)", re.IGNORECASE)


def parse_dmarc_record(txt_records: list[str]) -> DmarcResult:
    """Validate a domain's DMARC configuration from its ``_dmarc`` TXT records.

    Args:
        txt_records: TXT records found at ``_dmarc.<domain>``.

    Returns:
        The parsed result.
    """
    dmarc_records = [r for r in txt_records if r.strip().lower().startswith(_DMARC_PREFIX)]

    if not dmarc_records:
        return DmarcResult(
            status=CheckStatus.MISSING,
            record=None,
            policy=None,
            messages=(
                "No DMARC record found. Add a TXT record at _dmarc.<domain> "
                "starting with 'v=DMARC1; p=...'.",
            ),
        )

    record = dmarc_records[0].strip()
    policy_match = _DMARC_POLICY.search(record)
    if policy_match is None:
        return DmarcResult(
            status=CheckStatus.FAIL,
            record=record,
            policy=None,
            messages=("DMARC record found but has no valid 'p=' policy tag.",),
        )

    policy = policy_match.group(1).lower()
    has_reporting = bool(_DMARC_RUA.search(record))
    messages: list[str] = []
    if not has_reporting:
        messages.append(
            "No 'rua=' aggregate-report address set -- you won't receive DMARC "
            "reports showing who is sending as this domain."
        )

    if policy == "reject":
        return DmarcResult(
            status=CheckStatus.PASS, record=record, policy=policy, messages=tuple(messages)
        )
    if policy == "quarantine":
        messages.insert(0, "Policy is 'quarantine'. 'reject' is the strictest, fully-enforced setting.")
        return DmarcResult(
            status=CheckStatus.WARN, record=record, policy=policy, messages=tuple(messages)
        )

    messages.insert(
        0,
        "Policy is 'p=none' -- monitoring only. DMARC is not actually protecting "
        "this domain yet; move to 'quarantine' then 'reject' once reports look clean.",
    )
    return DmarcResult(status=CheckStatus.WARN, record=record, policy=policy, messages=tuple(messages))


@dataclass(frozen=True)
class DkimResult:
    """Outcome of checking a domain's DKIM configuration for one selector.

    Attributes:
        status: Overall result.
        selector: The selector checked.
        detail: What was found (a TXT key record, a CNAME delegation target,
            or nothing), for the reviewer to see exactly what this checked.
        messages: Human-readable findings.
    """

    status: CheckStatus
    selector: str
    detail: str | None
    messages: tuple[str, ...] = field(default_factory=tuple)


_DKIM_TXT_PREFIX = "v=dkim1"


def parse_dkim_selector(
    selector: str, txt_records: list[str], cname_target: str | None
) -> DkimResult:
    """Validate one DKIM selector's configuration.

    Args:
        selector: The selector checked (e.g. "s1").
        txt_records: TXT records found at ``<selector>._domainkey.<domain>``.
        cname_target: The CNAME target found at the same name, if the
            selector is delegated rather than holding the key directly --
            SendGrid's automated domain authentication does this by default,
            pointing at SendGrid's own infrastructure, which is a valid and
            common setup, not a misconfiguration.

    Returns:
        The parsed result.
    """
    dkim_txt = [r for r in txt_records if r.strip().lower().startswith(_DKIM_TXT_PREFIX)]

    if dkim_txt:
        record = dkim_txt[0].strip()
        if "p=" not in record.lower():
            return DkimResult(
                status=CheckStatus.FAIL,
                selector=selector,
                detail=record,
                messages=("DKIM record found but has no 'p=' public key tag.",),
            )
        return DkimResult(
            status=CheckStatus.PASS,
            selector=selector,
            detail=record,
            messages=("DKIM key record found directly at this selector.",),
        )

    if cname_target:
        return DkimResult(
            status=CheckStatus.PASS,
            selector=selector,
            detail=cname_target,
            messages=(
                f"DKIM delegated via CNAME to {cname_target} -- common for SendGrid's "
                "automated domain authentication. The key itself lives on the "
                "provider's infrastructure and isn't independently verifiable here.",
            ),
        )

    return DkimResult(
        status=CheckStatus.MISSING,
        selector=selector,
        detail=None,
        messages=(f"No DKIM record (TXT or CNAME) found for selector '{selector}'.",),
    )


def combine_dkim_results(results: list[DkimResult]) -> DkimResult:
    """Combine multiple selector attempts into one overall DKIM result.

    Args:
        results: One result per selector tried.

    Returns:
        The best result found -- DKIM only needs one working selector, so
        this reports success if any selector passed, rather than penalizing
        a domain for guessed selectors that don't happen to be the real one.
        If none passed, returns the least-bad individual result.

    Raises:
        ValueError: If ``results`` is empty.
    """
    if not results:
        raise ValueError("results must not be empty")

    passing = [r for r in results if r.status is CheckStatus.PASS]
    if passing:
        return passing[0]

    # No selector worked -- report the most informative failure: prefer a
    # FAIL (something was found but broken) over a plain MISSING (nothing
    # found at all), since a broken record is more actionable to surface.
    for status in (CheckStatus.FAIL, CheckStatus.WARN, CheckStatus.MISSING):
        for result in results:
            if result.status is status:
                return result
    return results[0]  # pragma: no cover -- unreachable, every status is covered above
