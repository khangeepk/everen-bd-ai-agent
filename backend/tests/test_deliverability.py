"""Tests for :mod:`app.services.deliverability`."""

from __future__ import annotations

import pytest

from app.services.deliverability import (
    CheckStatus,
    combine_dkim_results,
    combine_statuses,
    parse_dkim_selector,
    parse_dmarc_record,
    parse_spf_record,
)


# ---------------------------------------------------------------------------
# combine_statuses
# ---------------------------------------------------------------------------


def test_combine_statuses_empty_is_missing() -> None:
    """No sections to combine means nothing to report a pass on."""
    assert combine_statuses([]) is CheckStatus.MISSING


def test_combine_statuses_all_pass_is_pass() -> None:
    """Every section clean means an overall pass."""
    assert combine_statuses([CheckStatus.PASS, CheckStatus.PASS]) is CheckStatus.PASS


def test_combine_statuses_fail_beats_missing() -> None:
    """A broken record is more urgent than one that's simply absent."""
    assert combine_statuses([CheckStatus.MISSING, CheckStatus.FAIL]) is CheckStatus.FAIL


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ([CheckStatus.PASS, CheckStatus.WARN], CheckStatus.WARN),
        ([CheckStatus.WARN, CheckStatus.MISSING], CheckStatus.MISSING),
        ([CheckStatus.PASS, CheckStatus.MISSING, CheckStatus.FAIL], CheckStatus.FAIL),
    ],
)
def test_combine_statuses_severity_order(
    statuses: list[CheckStatus], expected: CheckStatus
) -> None:
    """FAIL > MISSING > WARN > PASS, regardless of input order."""
    assert combine_statuses(statuses) is expected


# ---------------------------------------------------------------------------
# parse_spf_record
# ---------------------------------------------------------------------------


def test_spf_missing_when_no_txt_records() -> None:
    """No 'v=spf1' TXT record at all is MISSING, not FAIL."""
    result = parse_spf_record(["some-other-txt-record"])
    assert result.status is CheckStatus.MISSING
    assert result.record is None


def test_spf_multiple_records_is_fail() -> None:
    """RFC 7208: more than one SPF record is a PermError, not a mergeable set."""
    result = parse_spf_record(
        ["v=spf1 include:_spf.example.com -all", "v=spf1 include:other.com -all"]
    )
    assert result.status is CheckStatus.FAIL
    assert result.record is None
    assert "2 SPF records" in result.messages[0]


def test_spf_hard_fail_all_is_pass() -> None:
    """'-all' is the strict, recommended terminal mechanism."""
    result = parse_spf_record(["v=spf1 include:_spf.example.com -all"])
    assert result.status is CheckStatus.PASS
    assert result.record == "v=spf1 include:_spf.example.com -all"


def test_spf_soft_fail_all_is_warn() -> None:
    """'~all' is weaker than '-all' but not broken."""
    result = parse_spf_record(["v=spf1 include:_spf.example.com ~all"])
    assert result.status is CheckStatus.WARN


def test_spf_neutral_all_is_warn() -> None:
    """'?all' provides no real protection but isn't an open relay either."""
    result = parse_spf_record(["v=spf1 include:_spf.example.com ?all"])
    assert result.status is CheckStatus.WARN


def test_spf_plus_all_is_fail() -> None:
    """'+all' lets anyone spoof the domain -- effectively no SPF."""
    result = parse_spf_record(["v=spf1 +all"])
    assert result.status is CheckStatus.FAIL


def test_spf_implicit_plus_all_is_fail() -> None:
    """A bare 'all' with no qualifier defaults to '+all' per RFC 7208."""
    result = parse_spf_record(["v=spf1 include:_spf.example.com all"])
    assert result.status is CheckStatus.FAIL


def test_spf_no_all_mechanism_is_warn() -> None:
    """No terminal 'all' mechanism falls through to an unprotected default."""
    result = parse_spf_record(["v=spf1 include:_spf.example.com"])
    assert result.status is CheckStatus.WARN


def test_spf_ignores_non_spf_txt_records() -> None:
    """Other TXT records at the domain (e.g. site verification) are ignored."""
    result = parse_spf_record(
        ["google-site-verification=abc123", "v=spf1 include:_spf.example.com -all"]
    )
    assert result.status is CheckStatus.PASS


# ---------------------------------------------------------------------------
# parse_dmarc_record
# ---------------------------------------------------------------------------


def test_dmarc_missing_when_no_record() -> None:
    """No '_dmarc' TXT record at all is MISSING."""
    result = parse_dmarc_record([])
    assert result.status is CheckStatus.MISSING
    assert result.policy is None


def test_dmarc_no_policy_tag_is_fail() -> None:
    """A DMARC-looking record with no 'p=' tag is broken, not absent."""
    result = parse_dmarc_record(["v=DMARC1; rua=mailto:dmarc@example.com"])
    assert result.status is CheckStatus.FAIL
    assert result.policy is None


def test_dmarc_reject_with_reporting_is_pass() -> None:
    """'p=reject' plus a report address is the fully-enforced, best-practice setup."""
    result = parse_dmarc_record(["v=DMARC1; p=reject; rua=mailto:dmarc@example.com"])
    assert result.status is CheckStatus.PASS
    assert result.policy == "reject"
    assert not any("rua" in m.lower() and "no" in m.lower() for m in result.messages)


def test_dmarc_quarantine_is_warn() -> None:
    """'p=quarantine' is a real policy but weaker than 'reject'."""
    result = parse_dmarc_record(["v=DMARC1; p=quarantine; rua=mailto:dmarc@example.com"])
    assert result.status is CheckStatus.WARN
    assert result.policy == "quarantine"


def test_dmarc_none_is_warn() -> None:
    """'p=none' is monitoring-only -- not actually protecting the domain yet."""
    result = parse_dmarc_record(["v=DMARC1; p=none"])
    assert result.status is CheckStatus.WARN
    assert result.policy == "none"


def test_dmarc_missing_rua_is_flagged_even_when_policy_passes() -> None:
    """A 'p=reject' record with no reporting address still gets a note about it."""
    result = parse_dmarc_record(["v=DMARC1; p=reject"])
    assert result.status is CheckStatus.PASS
    assert any("rua=" in m for m in result.messages)


def test_dmarc_policy_match_is_case_insensitive() -> None:
    """DMARC tags are case-insensitive per the spec."""
    result = parse_dmarc_record(["V=DMARC1; P=REJECT; RUA=mailto:dmarc@example.com"])
    assert result.status is CheckStatus.PASS
    assert result.policy == "reject"


# ---------------------------------------------------------------------------
# parse_dkim_selector / combine_dkim_results
# ---------------------------------------------------------------------------


def test_dkim_missing_when_nothing_found() -> None:
    """No TXT and no CNAME at the selector name means nothing configured."""
    result = parse_dkim_selector("s1", [], None)
    assert result.status is CheckStatus.MISSING


def test_dkim_txt_without_public_key_is_fail() -> None:
    """A DKIM-looking TXT record missing the 'p=' key tag is broken."""
    result = parse_dkim_selector("s1", ["v=DKIM1; k=rsa"], None)
    assert result.status is CheckStatus.FAIL


def test_dkim_txt_with_public_key_is_pass() -> None:
    """A direct TXT key record is the straightforward passing case."""
    result = parse_dkim_selector("s1", ["v=DKIM1; k=rsa; p=MIGfMA0..."], None)
    assert result.status is CheckStatus.PASS
    assert result.detail is not None and "p=" in result.detail


def test_dkim_cname_delegation_is_pass() -> None:
    """SendGrid's automated domain auth delegates via CNAME -- a valid setup."""
    result = parse_dkim_selector("s1", [], "s1.domainkey.u1234.wl.sendgrid.net")
    assert result.status is CheckStatus.PASS
    assert result.detail == "s1.domainkey.u1234.wl.sendgrid.net"


def test_dkim_txt_takes_priority_over_cname() -> None:
    """If both a TXT key and a CNAME somehow exist, the direct key wins."""
    result = parse_dkim_selector("s1", ["v=DKIM1; k=rsa; p=MIGfMA0..."], "some.cname.target")
    assert result.status is CheckStatus.PASS
    assert result.detail is not None and "p=" in result.detail


def test_combine_dkim_results_empty_raises() -> None:
    """Combining zero attempted selectors is a programming error, not a valid result."""
    with pytest.raises(ValueError):
        combine_dkim_results([])


def test_combine_dkim_results_any_pass_wins() -> None:
    """Only one working selector is needed -- a guessed-wrong selector shouldn't fail the check."""
    results = [
        parse_dkim_selector("s1", [], None),
        parse_dkim_selector("s2", ["v=DKIM1; k=rsa; p=MIGfMA0..."], None),
    ]
    combined = combine_dkim_results(results)
    assert combined.status is CheckStatus.PASS
    assert combined.selector == "s2"


def test_combine_dkim_results_prefers_fail_over_missing_when_none_pass() -> None:
    """A broken record found under one selector is more actionable than a bare miss."""
    results = [
        parse_dkim_selector("s1", [], None),  # MISSING
        parse_dkim_selector("s2", ["v=DKIM1; k=rsa"], None),  # FAIL (no p=)
    ]
    combined = combine_dkim_results(results)
    assert combined.status is CheckStatus.FAIL
    assert combined.selector == "s2"
