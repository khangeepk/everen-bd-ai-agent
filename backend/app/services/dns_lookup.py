"""DNS TXT/CNAME lookups for the deliverability checker, via DNS-over-HTTPS.

The network-touching half of the deliverability checklist (the pure record
parsing lives in :mod:`app.services.deliverability`) -- split the same way
:mod:`app.services.email_discovery` is split from
:mod:`app.services.email_enrichment`, so the parsing logic stays importable
and testable without a network call.

Uses Cloudflare's DNS-over-HTTPS JSON API rather than a raw DNS client or a
new resolver dependency (e.g. dnspython): it's a plain HTTPS GET returning
JSON, so it reuses httpx -- already a dependency of this codebase -- instead
of adding a new one, and needs no UDP port 53 egress, which is often blocked
outbound from containerized environments in a way HTTPS on 443 is not.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

#: Cloudflare's DNS-over-HTTPS JSON endpoint (RFC 8484-adjacent JSON API,
#: not the binary wire format). No API key required; a well-known public
#: resolver, same trust model as any DNS resolution this server would
#: otherwise do via its OS resolver.
DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"

DEFAULT_TIMEOUT_SECONDS = 10.0

#: DNS RCODE 0 = NOERROR. A query can succeed (this status) with zero
#: answers, which simply means the record doesn't exist -- not an error.
_DNS_RCODE_NOERROR = 0


class DnsLookupError(RuntimeError):
    """Raised when the DoH resolver itself could not be reached or errored.

    Never raised for a normal "no such record" result -- that is a valid,
    expected outcome (an absent SPF/DMARC/DKIM record) represented as an
    empty list, not an exception.
    """


async def _doh_query(
    name: str, record_type: str, *, timeout_seconds: float
) -> list[dict[str, object]]:
    """Run one DNS-over-HTTPS query and return its raw answer records.

    Args:
        name: The DNS name to query.
        record_type: The record type, e.g. "TXT" or "CNAME".
        timeout_seconds: Request timeout.

    Returns:
        The raw ``Answer`` entries from the JSON response, or an empty list
        if the name has no records of this type (NXDOMAIN or NOERROR with
        zero answers -- both are normal, valid "not configured" outcomes).

    Raises:
        DnsLookupError: If the resolver could not be reached, returned a
            non-2xx response, or the response could not be parsed.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(
                DOH_ENDPOINT,
                params={"name": name, "type": record_type},
                headers={"Accept": "application/dns-json"},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        msg = f"DNS-over-HTTPS query for {record_type} {name} failed: {exc}"
        raise DnsLookupError(msg) from exc
    except ValueError as exc:
        msg = (
            f"DNS-over-HTTPS response for {record_type} {name} "
            "was not valid JSON"
        )
        raise DnsLookupError(msg) from exc

    status = payload.get("Status")
    if status is not None and status != _DNS_RCODE_NOERROR:
        # Non-zero, non-NXDOMAIN-shaped statuses (NXDOMAIN is still status 3
        # in this API and simply yields no Answer entries below, so it falls
        # through to the empty-list path rather than being special-cased
        # here) are logged for visibility but still treated as "no records"
        # rather than a hard error -- a malformed zone should surface as
        # MISSING/FAIL from the parser, not crash the check.
        logger.info(
            "DNS query returned a non-NOERROR status",
            extra={"name": name, "type": record_type, "status": status},
        )

    answers = payload.get("Answer")
    if not isinstance(answers, list):
        return []
    return answers


def _unquote_txt(raw_data: str) -> str:
    """Join and unquote a DoH JSON TXT record's data field.

    A TXT record longer than 255 bytes is split into multiple
    quoted-string segments by the resolver's JSON encoding, e.g.
    ``"\"v=spf1 include:sendgrid.n\" \"et -all\""``. The split point is an
    arbitrary byte-length boundary, not a word boundary, so the segments
    must be concatenated directly with no separator inserted between them
    -- joining with a space would silently corrupt any record that happens
    to split mid-word (this was caught during review: an earlier version of
    this function joined multi-segment records with a space).

    Args:
        raw_data: The raw ``data`` field from one DoH JSON Answer entry.

    Returns:
        The unquoted, concatenated record text.
    """
    segments = [seg for seg in raw_data.split('" "') if seg]
    joined = "".join(seg.strip('"') for seg in segments) if len(segments) > 1 else raw_data
    return joined.strip().strip('"')


async def resolve_txt_records(
    name: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> list[str]:
    """Resolve every TXT record at a DNS name.

    Args:
        name: The DNS name to query, e.g. "example.com" or
            "_dmarc.example.com".
        timeout_seconds: Request timeout.

    Returns:
        The decoded record strings, in the order returned by the resolver.
        Empty if the name has no TXT records.

    Raises:
        DnsLookupError: If the resolver itself could not be reached.
    """
    answers = await _doh_query(name, "TXT", timeout_seconds=timeout_seconds)
    records = [_unquote_txt(str(answer["data"])) for answer in answers if "data" in answer]
    logger.info("TXT lookup complete", extra={"name": name, "record_count": len(records)})
    return records


async def resolve_cname(
    name: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> str | None:
    """Resolve the CNAME target at a DNS name, if any.

    Args:
        name: The DNS name to query.
        timeout_seconds: Request timeout.

    Returns:
        The CNAME target with its trailing dot stripped, or None if the
        name has no CNAME record.

    Raises:
        DnsLookupError: If the resolver itself could not be reached.
    """
    answers = await _doh_query(name, "CNAME", timeout_seconds=timeout_seconds)
    if not answers:
        return None
    target = str(answers[0].get("data", "")).strip()
    return target.rstrip(".") or None
