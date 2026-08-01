"""Pure aggregation math for the analytics dashboard.

Standard library only. The DB-aware query layer lives in
`app/services/analytics.py`; this module holds the arithmetic that turns raw
counts into rates and rankings, so it can be tested without a database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def safe_rate(numerator: int, denominator: int) -> float:
    """Compute ``numerator / denominator``, treating a zero denominator as 0.0.

    A rate with no opportunities to occur (e.g. an open rate with zero sends)
    is reported as 0.0 rather than raising or returning NaN -- a dashboard
    should show "0%", not crash or show a blank.

    Args:
        numerator: The count of occurrences.
        denominator: The count of opportunities.

    Returns:
        The rate, in ``[0.0, ...]``. Not clamped above 1.0 -- a caller
        counting distinct opens against distinct sends should never exceed
        1.0, but this function does not assume that invariant holds.

    Raises:
        ValueError: If either argument is negative.
    """
    if numerator < 0 or denominator < 0:
        raise ValueError("numerator and denominator must not be negative")
    if denominator == 0:
        return 0.0
    return numerator / denominator


@dataclass(frozen=True)
class RankedItem:
    """One entry in a top-N ranking.

    Attributes:
        label: The category label, e.g. an industry name or service name.
        count: How many times it occurred.
    """

    label: str
    count: int


def top_n(counts: dict[str, int], n: int = 5, *, exclude_blank: bool = True) -> list[RankedItem]:
    """Rank labeled counts, highest first, breaking ties alphabetically.

    Deterministic tie-breaking matters here -- two industries tied for #5
    should not flicker between requests depending on dict iteration order.

    Args:
        counts: A mapping of label to occurrence count.
        n: How many entries to return.
        exclude_blank: Drop entries whose label is empty or ``None``-like
            (already coerced to ``""`` by the caller) -- an uncategorized
            lead should not show up as a fake "top industry".

    Returns:
        Up to ``n`` ranked items, highest count first.

    Raises:
        ValueError: If ``n`` is not positive.
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")

    items = [
        RankedItem(label=label, count=count)
        for label, count in counts.items()
        if not (exclude_blank and not label.strip())
    ]
    items.sort(key=lambda item: (-item.count, item.label))
    return items[:n]


@dataclass(frozen=True)
class VariantPerformance:
    """Rolled-up performance for one prompt version or A/B variant.

    Attributes:
        variant_id: The prompt version or variant identifier.
        label: Human-readable label.
        sent: Drafts sent under this variant.
        opened: Distinct sends that were opened at least once.
        replied: Distinct sends that got a reply.
        meetings_booked: Distinct sends whose lead subsequently booked a call.
        deals_won: Distinct sends whose lead subsequently converted.
    """

    variant_id: str
    label: str
    sent: int
    opened: int
    replied: int
    meetings_booked: int
    deals_won: int

    @property
    def open_rate(self) -> float:
        """Open rate for this variant.

        Returns:
            Opens divided by sends.
        """
        return safe_rate(self.opened, self.sent)

    @property
    def reply_rate(self) -> float:
        """Reply rate for this variant.

        Returns:
            Replies divided by sends.
        """
        return safe_rate(self.replied, self.sent)

    @property
    def meeting_rate(self) -> float:
        """Meeting-booked rate for this variant.

        Returns:
            Meetings booked divided by sends.
        """
        return safe_rate(self.meetings_booked, self.sent)

    @property
    def win_rate(self) -> float:
        """Deal-won rate for this variant.

        Returns:
            Deals won divided by sends.
        """
        return safe_rate(self.deals_won, self.sent)


def rank_variants_by_reply_rate(
    variants: list[VariantPerformance], *, min_sent: int = 1
) -> list[VariantPerformance]:
    """Order variants by reply rate, highest first.

    Args:
        variants: The variants to rank.
        min_sent: Exclude variants with fewer sends than this -- a variant
            with 1 send and 1 reply has a 100% reply rate that means
            nothing; this keeps a tiny sample from topping the leaderboard.

    Returns:
        The qualifying variants, ranked by reply rate.
    """
    qualifying = [v for v in variants if v.sent >= min_sent]
    qualifying.sort(key=lambda v: (-v.reply_rate, -v.sent, v.label))
    return qualifying
