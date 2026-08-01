"""Deterministic A/B variant assignment for outreach prompt experiments.

Standard library only (uses `hashlib` for deterministic bucketing), so
assignment logic is unit-testable without a database. The DB-aware caller in
`app/agents/outreach.py` converts `PromptVersion` rows sharing an
`experiment_group` into `PromptVariant` objects and calls `choose_variant`.

Bucketing is keyed by a stable identifier (a lead's ID) rather than randomized
per call, so the same lead always sees the same variant across multiple
outreach touches within one experiment -- a lead should not get an email
written by prompt A and a follow-up written by prompt B just because two
requests happened to land on either side of a coin flip.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptVariant:
    """One candidate prompt version in an A/B split.

    Attributes:
        variant_id: Stable identifier for this variant (the `PromptVersion`
            row's ID, as a string).
        label: Human-readable label, e.g. "A" or "v2-shorter-subject".
        weight: Relative traffic share. Weights are normalized against each
            other, so [1.0, 1.0] and [3.0, 3.0] both mean an even 50/50 split.
    """

    variant_id: str
    label: str
    weight: float = field(default=1.0)

    def __post_init__(self) -> None:
        """Validate the weight is usable.

        Raises:
            ValueError: If ``weight`` is not positive.
        """
        if self.weight <= 0:
            raise ValueError(f"weight must be positive, got {self.weight}")


class NoVariantsError(ValueError):
    """Raised when asked to choose among an empty variant list."""


def deterministic_fraction(key: str) -> float:
    """Map a string key to a stable value in ``[0.0, 1.0)``.

    Args:
        key: The bucketing key, e.g. a lead's UUID as a string.

    Returns:
        A deterministic pseudo-random fraction. The same key always produces
        the same fraction, and distinct keys are spread uniformly.
    """
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    # 8 hex chars = 32 bits, plenty of resolution for a traffic split and
    # avoids pulling in the full 256-bit int for no benefit.
    as_int = int(digest[:8], 16)
    return as_int / 0x1_0000_0000


def choose_variant(key: str, variants: list[PromptVariant]) -> PromptVariant:
    """Deterministically assign a bucketing key to one of several variants.

    Args:
        key: The stable identifier to bucket on (typically a lead's ID).
        variants: The candidate variants. A single-element list always
            returns that element -- no split occurs unless there are at
            least two active variants configured.

    Returns:
        The assigned variant.

    Raises:
        NoVariantsError: If ``variants`` is empty.
    """
    if not variants:
        raise NoVariantsError("Cannot choose a variant from an empty list")
    if len(variants) == 1:
        return variants[0]

    total_weight = sum(v.weight for v in variants)
    fraction = deterministic_fraction(key)
    target = fraction * total_weight

    cumulative = 0.0
    for variant in variants:
        cumulative += variant.weight
        if target < cumulative:
            return variant

    # Floating point rounding at the very top edge of the range -- fall back
    # to the last variant rather than raising over an epsilon.
    return variants[-1]
