"""CRM pipeline stage machine.

Standard library only, so the transition rules are testable without a
database or an LLM. This module defines *what stage a lead may move to next*;
the DB-aware orchestration (recording history, syncing ``Lead.status``,
triggering card generation) lives in :mod:`app.services.pipeline_transitions`.

Naming note -- ``PipelineStage.HOT`` is not the same concept as
``ScoreLabel.HOT`` from :mod:`app.services.lead_scoring`. The score label
answers "does this lead look like a good prospect" (need, fit, budget,
compliance). The pipeline stage answers "where is this lead in the
conversation" (has replied, wants a call, closed). A lead can score Cold on
quality while sitting in pipeline stage Hot because they explicitly asked for
a call, and vice versa -- a lead can score Hot while still sitting in New
because nobody has reached out yet. Both fields are shown together
deliberately rather than merged, so a rep never has to guess which "hot"
a report means.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field

from app.services.reply_classification import ReplyIntent

logger = logging.getLogger(__name__)


class PipelineStage(str, enum.Enum):
    """Where a lead sits in the outreach conversation pipeline."""

    NEW = "new"
    CONTACTED = "contacted"
    INTERESTED = "interested"
    HOT = "hot"
    #: A calendar meeting has actually been booked via the booking link (see
    #: app.api.v1.booking, app.services.pipeline_transitions.
    #: advance_on_meeting_booked). Certain ground truth from a real external
    #: event, not a reply-classification inference -- see the module note on
    #: _ALLOWED_TRANSITIONS below for why it's reachable directly from every
    #: open stage rather than only from HOT.
    MEETING_BOOKED = "meeting_booked"
    CONVERTED = "converted"
    LOST = "lost"


class PipelineTransitionReason(str, enum.Enum):
    """Why a pipeline stage changed."""

    MANUAL = "manual"
    OUTREACH_SENT = "outreach_sent"
    REPLY_CLASSIFIED = "reply_classified"
    SUPPRESSED = "suppressed"
    FORCED = "forced"
    #: A calendar meeting was actually booked via the booking link.
    MEETING_BOOKED = "meeting_booked"


class InvalidTransitionError(ValueError):
    """Raised when a stage transition is not permitted.

    Deliberately an error, not a silent clamp -- a rejected transition should
    surface to the caller so a rep sees why a lead didn't move rather than the
    request quietly no-op'ing.
    """


#: The pipeline is directed and mostly forward-only. LOST is reachable from
#: every open stage (a lead can decline at any point) but is terminal -- it
#: does not flow back into the open pipeline. CONVERTED is likewise terminal.
#: This is the ordinary path only; see FORCED for the escape hatch below.
#:
#: MEETING_BOOKED is reachable directly from CONTACTED, INTERESTED, and HOT
#: -- not only from HOT. (Not from NEW: booking is always triggered by a
#: reply, per app.services.booking_link_scanner, and a lead cannot reply
#: before being contacted, so NEW -> MEETING_BOOKED never legitimately
#: arises.) A real calendar booking is certain ground truth from an
#: external event (the prospect actually scheduled a call),
#: unlike the reply-classification-driven transitions elsewhere in this
#: graph, which are probabilistic inferences. Requiring a lead to first pass
#: through HOT (or relying on next_stage_towards's multi-hop BFS stepping,
#: designed for exactly that probabilistic case) would let a lead's stage
#: contradict a fact that has already happened, or force
#: advance_on_meeting_booked to use force=True -- reserved for
#: approver-level manual correction, not routine bookkeeping. So a booking
#: is always a single, direct, force=False transition regardless of where
#: the lead started.
_ALLOWED_TRANSITIONS: dict[PipelineStage, frozenset[PipelineStage]] = {
    PipelineStage.NEW: frozenset({PipelineStage.CONTACTED, PipelineStage.LOST}),
    PipelineStage.CONTACTED: frozenset(
        {
            PipelineStage.INTERESTED,
            PipelineStage.HOT,
            PipelineStage.MEETING_BOOKED,
            PipelineStage.LOST,
        }
    ),
    PipelineStage.INTERESTED: frozenset(
        {PipelineStage.HOT, PipelineStage.MEETING_BOOKED, PipelineStage.LOST}
    ),
    PipelineStage.HOT: frozenset(
        {PipelineStage.MEETING_BOOKED, PipelineStage.CONVERTED, PipelineStage.LOST}
    ),
    PipelineStage.MEETING_BOOKED: frozenset({PipelineStage.CONVERTED, PipelineStage.LOST}),
    PipelineStage.CONVERTED: frozenset(),
    PipelineStage.LOST: frozenset(),
}

#: Stages from which the pipeline is still open (not yet won or lost).
OPEN_STAGES: frozenset[PipelineStage] = frozenset(
    {
        PipelineStage.NEW,
        PipelineStage.CONTACTED,
        PipelineStage.INTERESTED,
        PipelineStage.HOT,
        PipelineStage.MEETING_BOOKED,
    }
)

#: Terminal stages. Once here, only a forced override moves the lead again.
TERMINAL_STAGES: frozenset[PipelineStage] = frozenset(
    {PipelineStage.CONVERTED, PipelineStage.LOST}
)


@dataclass(frozen=True)
class TransitionResult:
    """The outcome of validating a proposed stage transition.

    Attributes:
        from_stage: The stage before the transition.
        to_stage: The stage after the transition.
        reason: Why the transition is happening.
        entered_hot: True when this transition is what puts the lead into
            HOT -- the signal the call-center card generator watches for.
    """

    from_stage: PipelineStage
    to_stage: PipelineStage
    reason: PipelineTransitionReason
    entered_hot: bool = field(default=False)


def is_valid_transition(from_stage: PipelineStage, to_stage: PipelineStage) -> bool:
    """Whether a direct transition between two stages is permitted.

    Args:
        from_stage: Current stage.
        to_stage: Proposed stage.

    Returns:
        True if the transition follows the pipeline graph, or is a no-op
        (staying in the same stage).
    """
    if from_stage == to_stage:
        return True
    return to_stage in _ALLOWED_TRANSITIONS.get(from_stage, frozenset())


def validate_transition(
    from_stage: PipelineStage,
    to_stage: PipelineStage,
    reason: PipelineTransitionReason,
    *,
    force: bool = False,
) -> TransitionResult:
    """Validate and describe a proposed stage transition.

    Args:
        from_stage: Current stage.
        to_stage: Proposed stage.
        reason: Why the transition is happening.
        force: Bypass the transition graph. Reserved for approver-level manual
            correction (e.g. undoing a mis-classification); every forced
            transition is still logged with ``reason=FORCED`` by the caller.

    Returns:
        The transition result.

    Raises:
        InvalidTransitionError: If the transition is not permitted and
            ``force`` is False.
    """
    if not force and not is_valid_transition(from_stage, to_stage):
        raise InvalidTransitionError(
            f"Cannot move a lead from '{from_stage.value}' to '{to_stage.value}'. "
            f"Valid next stages from '{from_stage.value}': "
            f"{sorted(s.value for s in _ALLOWED_TRANSITIONS.get(from_stage, frozenset()))}"
        )

    entered_hot = to_stage is PipelineStage.HOT and from_stage is not PipelineStage.HOT

    logger.info(
        "Pipeline transition validated",
        extra={
            "from_stage": from_stage.value,
            "to_stage": to_stage.value,
            "reason": reason.value,
            "forced": force,
        },
    )
    return TransitionResult(
        from_stage=from_stage, to_stage=to_stage, reason=reason, entered_hot=entered_hot
    )


#: What a classified reply implies for the pipeline. BOOK_CALL is the only
#: reply-driven route into HOT: it is the one reply category that means "this
#: person wants to speak to a human now," which is exactly what the
#: call-center card exists for. PRICING and INTERESTED both signal engagement
#: without that explicit ask, so they land in INTERESTED -- still warrants BD
#: follow-up, not yet a call-center handoff.
_INTENT_TARGET_STAGE: dict[ReplyIntent, PipelineStage] = {
    ReplyIntent.BOOK_CALL: PipelineStage.HOT,
    ReplyIntent.PRICING: PipelineStage.INTERESTED,
    ReplyIntent.INTERESTED: PipelineStage.INTERESTED,
    ReplyIntent.NOT_INTERESTED: PipelineStage.LOST,
}


def target_stage_for_intent(intent: ReplyIntent) -> PipelineStage | None:
    """Determine which stage a classified reply should advance a lead to.

    Args:
        intent: The classified reply intent.

    Returns:
        The target stage, or None for :attr:`ReplyIntent.UNCLEAR` -- an
        unclear reply is a signal to route to a human, not to move the
        pipeline automatically.
    """
    return _INTENT_TARGET_STAGE.get(intent)


def next_stage_towards(
    current: PipelineStage, target: PipelineStage
) -> PipelineStage | None:
    """Find the next stage on the path from ``current`` toward ``target``.

    Reply classification can imply a stage further along than a direct
    transition permits -- e.g. a lead still in NEW (never contacted) receives
    a reply classified BOOK_CALL, implying HOT, but NEW cannot jump straight
    to HOT. Rather than reject that outright, this walks the pipeline one
    valid step at a time toward the implied target, so the lead still
    advances instead of getting stuck on a technicality. Terminal stages
    (CONVERTED, LOST) are never routed through -- a lead cannot be walked
    through LOST just because that happened to be adjacent.

    Args:
        current: The lead's current stage.
        target: The stage implied by the classified reply.

    Returns:
        The next stage to move to, or None if ``current`` already meets or
        exceeds ``target``, or if ``current`` is terminal.
    """
    if current == target or current in TERMINAL_STAGES:
        return None
    if is_valid_transition(current, target):
        return target

    # BFS over the small fixed graph for the shortest open path.
    visited = {current}
    queue: list[tuple[PipelineStage, list[PipelineStage]]] = [(current, [])]
    while queue:
        stage, path = queue.pop(0)
        for neighbor in _ALLOWED_TRANSITIONS.get(stage, frozenset()):
            if neighbor in visited or neighbor in TERMINAL_STAGES:
                continue
            new_path = path + [neighbor]
            if neighbor == target:
                return new_path[0]
            visited.add(neighbor)
            queue.append((neighbor, new_path))

    logger.warning(
        "No open path found toward target stage",
        extra={"current": current.value, "target": target.value},
    )
    return None
