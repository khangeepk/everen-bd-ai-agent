#!/usr/bin/env python3
"""Soft-launch funnel simulation — CODE-LOGIC SIMULATION, NOT REAL SENDS.

WHAT THIS IS
------------
A reusable, dependency-free harness that drives synthetic leads through the
full BD funnel::

    discover -> enrich -> audit -> score -> draft -> approve -> send

It makes **no external API calls** (no Google Places, PageSpeed, OpenAI, or
SendGrid) and touches **no database**. Every "send" is simulated. Use it to
measure where the funnel's *gating logic* drops leads, at a batch size big
enough to be meaningful, without spending API credit or sending real email.

WHY IT'S FAITHFUL
-----------------
The stage gates below are copied from the real modules so the drop-off
mirrors production behavior. When those modules change, update the mirrored
constants here (each is cited inline):

* Score weights + bands  -> app/services/lead_scoring.py:44-58, 208-213
* Email channel eligibility -> app/services/outreach_policy.py:219-267
* Send gate (approved + suppression + quota + CAN-SPAM)
                          -> app/services/email_sender.py, send_limits.py, canspam.py

RUN
---
    python backend/scripts/soft_launch_sim.py                 # default 220 leads
    python backend/scripts/soft_launch_sim.py --leads 300 --seed 7
    python backend/scripts/soft_launch_sim.py --daily-send-limit 50

Deterministic for a given --seed, so results are reproducible across runs and
comparable after code changes.
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass, field

# --- Mirrored production constants (keep in sync with the cited modules) ------

# app/services/lead_scoring.py:44-49
WEIGHTS = {
    "need": 0.30,
    "fit": 0.25,
    "contactability": 0.20,
    "revenue": 0.15,
    "compliance": 0.10,
}
# app/services/lead_scoring.py:57-58, 208-213
HOT_THRESHOLD = 0.75
WARM_THRESHOLD = 0.50

# app/services/send_limits.py — default daily send quota (config: outreach_daily_send_limit)
DEFAULT_DAILY_SEND_LIMIT = 50

# Simulated human approval reject rate (reviewers reject ~1 in 6 drafts).
DEFAULT_APPROVAL_REJECT_RATE = 0.18


@dataclass
class SyntheticLead:
    """One synthetic business lead with a deliberately non-uniform profile."""

    lead_id: int
    has_website: bool
    contact_email: bool
    email_verified: bool
    do_not_contact: bool
    email_suppressed: bool
    strict_jurisdiction: bool
    consent_basis: bool
    # component inputs in [0,1]
    need: float = 0.0
    fit: float = 0.0
    contactability: float = 0.0
    revenue: float = 0.0
    compliance: float = 0.0
    # derived
    audit_health: float | None = None
    total_score: float = 0.0
    label: str = ""
    email_eligible: bool = False
    drafted: bool = False
    approved: bool = False
    sent: bool = False


@dataclass
class StageCounts:
    """Absolute counts at each funnel stage (never percentages of the batch)."""

    discovered: int = 0
    usable_email: int = 0
    audited: int = 0
    scored_contactable: int = 0  # not DO_NOT_CONTACT
    reached_draft: int = 0
    approved: int = 0
    sent: int = 0
    notes: list[str] = field(default_factory=list)


def make_leads(n: int, rng: random.Random) -> list[SyntheticLead]:
    """Generate ``n`` realistic, non-uniform synthetic leads.

    The distribution reflects the target combos (independent trades /
    restaurants in weak-online-presence zips): many lack a real website or a
    findable, verified email; audit health skews poor (which raises "need").

    Args:
        n: How many leads to synthesize.
        rng: Seeded RNG for reproducibility.

    Returns:
        A list of ``SyntheticLead`` with varied profiles.
    """
    leads: list[SyntheticLead] = []
    for i in range(n):
        has_website = rng.random() < 0.70          # 70% have some website
        contact_email = rng.random() < 0.55         # 55% expose an email
        # Verified only if present; crawler-guessed emails are often unverified.
        email_verified = contact_email and rng.random() < 0.70
        do_not_contact = rng.random() < 0.04         # 4% hard opt-out / DNC
        email_suppressed = rng.random() < 0.03       # 3% already suppressed
        strict_jurisdiction = rng.random() < 0.12    # 12% strict (needs consent)
        consent_basis = strict_jurisdiction and rng.random() < 0.25

        # Audit health: no website -> very poor; else skewed toward poor.
        if has_website:
            audit_health = max(0.0, min(1.0, rng.gauss(0.45, 0.20)))
        else:
            audit_health = 0.05

        lead = SyntheticLead(
            lead_id=1000 + i,
            has_website=has_website,
            contact_email=contact_email,
            email_verified=email_verified,
            do_not_contact=do_not_contact,
            email_suppressed=email_suppressed,
            strict_jurisdiction=strict_jurisdiction,
            consent_basis=consent_basis,
            audit_health=audit_health if has_website else None,
        )
        # Component scores (mirror lead_scoring semantics: higher = better;
        # for "need", worse online presence => higher need).
        lead.need = 1.0 - audit_health                     # poor site => high need
        lead.fit = max(0.0, min(1.0, rng.gauss(0.55, 0.22)))  # some match KB, some not
        # Contactability driven by verified email presence.
        if lead.email_verified and not lead.email_suppressed:
            lead.contactability = max(0.0, min(1.0, rng.gauss(0.75, 0.15)))
        elif lead.contact_email:
            lead.contactability = max(0.0, min(1.0, rng.gauss(0.40, 0.15)))
        else:
            lead.contactability = max(0.0, min(1.0, rng.gauss(0.15, 0.10)))
        lead.revenue = max(0.0, min(1.0, rng.gauss(0.50, 0.25)))
        # Compliance component: higher = lower risk (per lead_scoring docstring).
        lead.compliance = 0.2 if (do_not_contact or email_suppressed) else \
            max(0.0, min(1.0, rng.gauss(0.85, 0.10)))
        leads.append(lead)
    return leads


def score_label(lead: SyntheticLead) -> str:
    """Replicate app/services/lead_scoring.py banding + compliance gate."""
    gate_triggered = lead.do_not_contact or lead.email_suppressed
    total = (
        WEIGHTS["need"] * lead.need
        + WEIGHTS["fit"] * lead.fit
        + WEIGHTS["contactability"] * lead.contactability
        + WEIGHTS["revenue"] * lead.revenue
        + WEIGHTS["compliance"] * lead.compliance
    )
    lead.total_score = round(total, 4)
    if gate_triggered:
        return "do_not_contact"
    if total >= HOT_THRESHOLD:
        return "hot"
    if total >= WARM_THRESHOLD:
        return "warm"
    return "cold"


def email_channel_eligible(lead: SyntheticLead) -> bool:
    """Replicate app/services/outreach_policy.py:219-267 (assess_email)."""
    if lead.do_not_contact:
        return False
    if not lead.contact_email:
        return False
    if not lead.email_verified:
        return False
    if lead.email_suppressed:
        return False
    if lead.strict_jurisdiction and not lead.consent_basis:
        return False
    return True


def run_funnel(
    leads: list[SyntheticLead],
    rng: random.Random,
    daily_send_limit: int,
    reject_rate: float,
) -> StageCounts:
    """Drive the full funnel and return absolute per-stage counts."""
    c = StageCounts()
    c.discovered = len(leads)

    for lead in leads:
        # STAGE: enrich -> "usable email" = present + verified + not suppressed
        if lead.contact_email and lead.email_verified and not lead.email_suppressed:
            c.usable_email += 1

        # STAGE: audit -> only leads with a website get an audit
        if lead.has_website:
            c.audited += 1

        # STAGE: score -> label; DO_NOT_CONTACT is a hard drop
        lead.label = score_label(lead)
        if lead.label != "do_not_contact":
            c.scored_contactable += 1

        # STAGE: draft -> worth drafting only if HOT/WARM AND email-eligible
        lead.email_eligible = email_channel_eligible(lead)
        if lead.label in ("hot", "warm") and lead.email_eligible:
            lead.drafted = True
            c.reached_draft += 1

        # STAGE: approve -> human review; some rejected
        if lead.drafted and rng.random() >= reject_rate:
            lead.approved = True
            c.approved += 1

        # STAGE: send -> approved + not suppressed + CAN-SPAM ok (sandbox).
        # (Physical address / footer assumed configured in prod env.)
        if lead.approved and not lead.email_suppressed:
            lead.sent = True
            c.sent += 1

    # Daily-quota reality: sends are capped at daily_send_limit/day.
    if c.sent > daily_send_limit:
        days = math.ceil(c.sent / daily_send_limit)
        c.notes.append(
            f"Daily quota {daily_send_limit}/day: {c.sent} sends would spread "
            f"over ~{days} days (day 1 delivers {daily_send_limit})."
        )
    return c


def largest_dropoff(c: StageCounts) -> tuple[str, int]:
    """Return the (label, absolute drop) of the single biggest stage-to-stage loss."""
    stages = [
        ("discover -> usable email", c.discovered - c.usable_email),
        ("usable email -> reached draft", c.usable_email - c.reached_draft),
        ("reached draft -> approved", c.reached_draft - c.approved),
        ("approved -> sent", c.approved - c.sent),
    ]
    return max(stages, key=lambda s: s[1])


def report(c: StageCounts, n: int, seed: int, daily_send_limit: int, reject_rate: float) -> None:
    """Print the labeled simulation report with absolute counts."""
    bar = "=" * 68
    print(bar)
    print("  SOFT-LAUNCH FUNNEL — CODE-LOGIC SIMULATION (NOT REAL SENDS)")
    print(f"  synthetic leads={n}  seed={seed}  daily_send_limit={daily_send_limit}"
          f"  reject_rate={reject_rate:.0%}")
    print(bar)
    rows = [
        ("Discovered (synthetic)", c.discovered),
        ("Reached usable email (present+verified+not suppressed)", c.usable_email),
        ("Audited (has website)", c.audited),
        ("Scored & contactable (not DO_NOT_CONTACT)", c.scored_contactable),
        ("Reached draft (HOT/WARM + email-eligible)", c.reached_draft),
        ("Approved by review", c.approved),
        ("Sent (SANDBOX — no real delivery)", c.sent),
    ]
    for label, val in rows:
        print(f"  {label:<52} {val:>5}")
    print("-" * 68)

    # Derived rates on the *funnel-entered* base, reported alongside absolutes.
    approve_reject = (c.reached_draft - c.approved)
    print(f"  Approval rejects (absolute)                          {approve_reject:>5}")
    print(f"  Send successes (absolute, sandbox)                   {c.sent:>5}")
    stage, drop = largest_dropoff(c)
    print("-" * 68)
    print(f"  BIGGEST DROP-OFF: {stage}  (-{drop} leads)")
    for note in c.notes:
        print(f"  NOTE: {note}")
    print(bar)
    print("  Reminder: simulation only. No Google/OpenAI/SendGrid calls, no DB,")
    print("  no email delivered. Mirrors real gates in lead_scoring / outreach_policy /")
    print("  email_sender. Re-run after code changes to compare drop-off.")
    print(bar)


def main() -> None:
    """CLI entrypoint."""
    p = argparse.ArgumentParser(description="Soft-launch funnel simulation (no external calls).")
    p.add_argument("--leads", type=int, default=220, help="synthetic lead count (150-300 recommended)")
    p.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    p.add_argument("--daily-send-limit", type=int, default=DEFAULT_DAILY_SEND_LIMIT)
    p.add_argument("--reject-rate", type=float, default=DEFAULT_APPROVAL_REJECT_RATE)
    args = p.parse_args()

    rng = random.Random(args.seed)
    leads = make_leads(args.leads, rng)
    counts = run_funnel(leads, rng, args.daily_send_limit, args.reject_rate)
    report(counts, args.leads, args.seed, args.daily_send_limit, args.reject_rate)


if __name__ == "__main__":
    main()
