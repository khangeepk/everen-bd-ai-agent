"""DB-aware orchestration for the SPF/DKIM/DMARC deliverability checker.

Runs the DNS lookups (app.services.dns_lookup), hands the raw records to the
pure parsers (app.services.deliverability), and persists one
DeliverabilityCheck row per run -- rep-triggered (via
POST /deliverability/checks) or called internally by
app.services.readiness_report, never on a schedule, consistent with every
other on-demand check in this codebase (audits, signal scans, email
enrichment).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import utcnow
from app.db.models.deliverability import DeliverabilityCheck
from app.services.deliverability import (
    CheckStatus,
    DkimResult,
    DmarcResult,
    SpfResult,
    combine_dkim_results,
    combine_statuses,
    parse_dkim_selector,
    parse_dmarc_record,
    parse_spf_record,
)
from app.services.dns_lookup import DnsLookupError, resolve_cname, resolve_txt_records

logger = logging.getLogger(__name__)

AGENT_NAME = "deliverability-checker-v1"


def domain_from_email(email: str) -> str | None:
    """Extract the domain half of an email address.

    Args:
        email: An email address, e.g. "bd@yourdomain.com".

    Returns:
        The domain, or None if ``email`` has no recognizable domain part.
    """
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    return domain or None


def resolve_check_domain() -> str | None:
    """Determine which domain to check, from settings.

    Args:
        None.

    Returns:
        ``settings.deliverability_check_domain`` if set, else the domain
        half of ``settings.outreach_from_email``, else None if neither
        yields a usable domain.
    """
    if settings.deliverability_check_domain:
        return settings.deliverability_check_domain.strip().lower()
    return domain_from_email(settings.outreach_from_email)


async def _fetch_txt_or_empty(name: str) -> tuple[list[str], bool]:
    """Fetch TXT records, treating a lookup failure as "could not verify".

    Args:
        name: The DNS name to query.

    Returns:
        A ``(records, lookup_failed)`` pair. ``lookup_failed`` is True only
        when the resolver itself could not be reached -- distinct from a
        successful query returning zero records, which means the record
        genuinely does not exist.
    """
    try:
        return await resolve_txt_records(name), False
    except DnsLookupError:
        logger.exception("TXT lookup failed", extra={"name": name})
        return [], True


async def run_deliverability_check(
    db: AsyncSession, domain: str | None = None, dkim_selectors: list[str] | None = None
) -> DeliverabilityCheck:
    """Run a fresh SPF/DKIM/DMARC check and persist the result.

    Args:
        db: Active database session. Caller is responsible for committing
            (this only flushes, per this codebase's service-layer convention).
        domain: The domain to check. Defaults to
            :func:`resolve_check_domain`.
        dkim_selectors: DKIM selectors to try. Defaults to
            ``settings.sendgrid_dkim_selectors``.

    Returns:
        The persisted check.

    Raises:
        ValueError: If no domain could be determined (neither ``domain``
            nor any settings-derived default is usable).
    """
    resolved_domain = domain or resolve_check_domain()
    if not resolved_domain:
        raise ValueError(
            "No domain to check: set DELIVERABILITY_CHECK_DOMAIN or a real "
            "OUTREACH_FROM_EMAIL."
        )
    selectors = dkim_selectors or list(settings.sendgrid_dkim_selectors)

    spf_txt, spf_lookup_failed = await _fetch_txt_or_empty(resolved_domain)
    spf_result = (
        SpfResult(
            status=CheckStatus.FAIL,
            record=None,
            messages=("Could not query DNS for this domain's SPF record.",),
        )
        if spf_lookup_failed
        else parse_spf_record(spf_txt)
    )

    dmarc_txt, dmarc_lookup_failed = await _fetch_txt_or_empty(f"_dmarc.{resolved_domain}")
    dmarc_result = (
        DmarcResult(
            status=CheckStatus.FAIL,
            record=None,
            policy=None,
            messages=("Could not query DNS for this domain's DMARC record.",),
        )
        if dmarc_lookup_failed
        else parse_dmarc_record(dmarc_txt)
    )

    dkim_results: list[DkimResult] = []
    for selector in selectors:
        name = f"{selector}._domainkey.{resolved_domain}"
        dkim_txt, dkim_lookup_failed = await _fetch_txt_or_empty(name)
        if dkim_lookup_failed:
            dkim_results.append(
                DkimResult(
                    status=CheckStatus.FAIL,
                    selector=selector,
                    detail=None,
                    messages=(f"Could not query DNS for selector '{selector}'.",),
                )
            )
            continue

        cname_target: str | None = None
        if not dkim_txt:
            try:
                cname_target = await resolve_cname(name)
            except DnsLookupError:
                logger.exception("CNAME lookup failed", extra={"name": name})
        dkim_results.append(parse_dkim_selector(selector, dkim_txt, cname_target))
    dkim_combined = combine_dkim_results(dkim_results)

    overall_status = combine_statuses(
        [spf_result.status, dmarc_result.status, dkim_combined.status]
    )

    check = DeliverabilityCheck(
        domain=resolved_domain,
        spf_status=spf_result.status,
        spf_record=spf_result.record,
        spf_detail="\n".join(spf_result.messages) or None,
        dmarc_status=dmarc_result.status,
        dmarc_record=dmarc_result.record,
        dmarc_detail="\n".join(dmarc_result.messages) or None,
        dkim_status=dkim_combined.status,
        dkim_selectors_checked=",".join(selectors),
        dkim_detail="\n".join(
            f"[{r.selector}] {msg}" for r in dkim_results for msg in r.messages
        )
        or None,
        overall_status=overall_status,
        checked_by_agent=AGENT_NAME,
        checked_at=utcnow(),
    )
    db.add(check)
    await db.flush()

    logger.info(
        "Deliverability check complete",
        extra={
            "domain": resolved_domain,
            "check_id": str(check.id),
            "spf_status": spf_result.status.value,
            "dmarc_status": dmarc_result.status.value,
            "dkim_status": dkim_combined.status.value,
            "overall_status": overall_status.value,
        },
    )
    return check
