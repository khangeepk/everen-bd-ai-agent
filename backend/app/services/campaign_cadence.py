"""Pure follow-up cadence rules per campaign type.

Standard library only, so the cadence math is testable without a database.
The DB-aware scanner that walks real leads and decides which ones are
actually due -- checking suppression, replies, and existing pending drafts
along the way -- lives in :mod:`app.services.campaign_followup_scanner`.

Why cadence varies by :class:`app.services.outreach_policy.CampaignType`:
a cold prospect has no relationship yet, so the cadence is patient and
spread out, giving a fair chance to notice and respond to each touch before
the next one lands. A warm lead (referral, inbound, already-engaged) already
has some context, so a shorter gap reads as attentive rather than pushy. A
re-engagement lead was already contacted once (or lost) before -- following
up too soon repeats whatever didn't work the first time, so the cadence is
the slowest and lowest-frequency of the three.

None of this ever sends anything. It only answers "is the next follow-up
due yet" -- generating and queuing the draft for human review is the
scanner's job, exactly like every other channel/eligibility rule in this
codebase (see app.services.outreach_policy's own module docstring).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.services.outreach_policy import CampaignType

#: Day-offsets from the previous send at which the next follow-up becomes
#: due, per campaign type. Index 0 is when follow-up #1 is due (measured
#: from the initial send), index 1 is when follow-up #2 is due (measured
#: from follow-up #1's send), and so on -- each offset is relative to the
#: *immediately preceding* send, not the original send date.
#:
#: COLD (3, 7, 14): a standard three-touch cold cadence -- a quick nudge,
#: then two longer gaps, tapering off rather than escalating.
#: WARM (2, 5): shorter and fewer touches -- there's already some context or
#: rapport, so a long silence would read as having lost interest, not as
#: respectful patience.
#: RE_ENGAGEMENT (7, 21): the slowest cadence -- this lead already went
#: quiet or was lost once, so repeating a fast cadence would repeat whatever
#: didn't land the first time. Two touches, spaced well apart, then stop.
CADENCE_SCHEDULES: dict[CampaignType, tuple[int, ...]] = {
    CampaignType.COLD: (3, 7, 14),
    CampaignType.WARM: (2, 5),
    CampaignType.RE_ENGAGEMENT: (7, 21),
}


def max_follow_ups(campaign_type: CampaignType) -> int:
    """How many follow-ups a campaign type's cadence includes in total.

    Args:
        campaign_type: The campaign type.

    Returns:
        The number of scheduled follow-up touches (not counting the initial
        send) before the cadence is exhausted.
    """
    return len(CADENCE_SCHEDULES[campaign_type])


def is_cadence_exhausted(campaign_type: CampaignType, follow_up_sequence: int) -> bool:
    """Whether every scheduled follow-up for this campaign type has been sent.

    Args:
        campaign_type: The campaign type.
        follow_up_sequence: The sequence number of the most recently sent
            draft for this lead (0 = the initial send, 1 = after the first
            follow-up was sent, and so on).

    Returns:
        True if no more follow-ups remain -- the scanner must stop touching
        this lead under the current campaign type rather than looping the
        last offset forever.
    """
    return follow_up_sequence >= max_follow_ups(campaign_type)


def next_follow_up_due_at(
    campaign_type: CampaignType, follow_up_sequence: int, last_sent_at: datetime
) -> datetime | None:
    """When the next follow-up becomes due, if the cadence has one left.

    Args:
        campaign_type: The campaign type.
        follow_up_sequence: The sequence number of the most recently sent
            draft for this lead (0 = initial send).
        last_sent_at: When that most recent draft was sent.

    Returns:
        The datetime the next follow-up is due, or None if the cadence for
        this campaign type is already exhausted.
    """
    if is_cadence_exhausted(campaign_type, follow_up_sequence):
        return None
    offset_days = CADENCE_SCHEDULES[campaign_type][follow_up_sequence]
    return last_sent_at + timedelta(days=offset_days)


def is_follow_up_due(
    campaign_type: CampaignType,
    follow_up_sequence: int,
    last_sent_at: datetime,
    now: datetime,
) -> bool:
    """Whether the next follow-up in the cadence is due as of ``now``.

    Args:
        campaign_type: The campaign type.
        follow_up_sequence: The sequence number of the most recently sent
            draft for this lead (0 = initial send).
        last_sent_at: When that most recent draft was sent.
        now: The instant to evaluate at.

    Returns:
        False if the cadence is already exhausted for this campaign type;
        otherwise True once ``now`` reaches the due date.
    """
    due_at = next_follow_up_due_at(campaign_type, follow_up_sequence, last_sent_at)
    return due_at is not None and now >= due_at
