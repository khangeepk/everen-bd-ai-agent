"""Tests for :mod:`app.services.pipeline`."""

from __future__ import annotations

import pytest

from app.services.pipeline import (
    OPEN_STAGES,
    TERMINAL_STAGES,
    InvalidTransitionError,
    PipelineStage,
    PipelineTransitionReason,
    is_valid_transition,
    next_stage_towards,
    target_stage_for_intent,
    validate_transition,
)
from app.services.reply_classification import ReplyIntent


def test_new_can_move_to_contacted() -> None:
    """The ordinary first step of the pipeline."""
    assert is_valid_transition(PipelineStage.NEW, PipelineStage.CONTACTED) is True


def test_new_cannot_jump_to_hot() -> None:
    """A direct jump skipping intermediate stages is not a valid transition."""
    assert is_valid_transition(PipelineStage.NEW, PipelineStage.HOT) is False


def test_same_stage_is_always_a_valid_no_op() -> None:
    """Staying put is never rejected."""
    for stage in PipelineStage:
        assert is_valid_transition(stage, stage) is True


@pytest.mark.parametrize("stage", sorted(OPEN_STAGES, key=lambda s: s.value))
def test_every_open_stage_can_reach_lost(stage: PipelineStage) -> None:
    """A lead can decline at any point before conversion."""
    assert is_valid_transition(stage, PipelineStage.LOST) is True


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STAGES, key=lambda s: s.value))
def test_terminal_stages_have_no_outgoing_transitions(terminal: PipelineStage) -> None:
    """Converted and Lost do not flow back into the open pipeline."""
    for target in PipelineStage:
        if target is terminal:
            continue
        assert is_valid_transition(terminal, target) is False


def test_validate_transition_returns_entered_hot_true_on_arrival() -> None:
    """Moving into Hot from elsewhere is flagged for card generation."""
    result = validate_transition(
        PipelineStage.INTERESTED, PipelineStage.HOT, PipelineTransitionReason.REPLY_CLASSIFIED
    )
    assert result.entered_hot is True


def test_validate_transition_does_not_flag_already_hot() -> None:
    """Staying in Hot is not a fresh arrival."""
    result = validate_transition(
        PipelineStage.HOT, PipelineStage.HOT, PipelineTransitionReason.MANUAL
    )
    assert result.entered_hot is False


def test_validate_transition_rejects_invalid_moves() -> None:
    """An invalid transition raises rather than silently no-op'ing."""
    with pytest.raises(InvalidTransitionError, match="Cannot move a lead"):
        validate_transition(
            PipelineStage.NEW, PipelineStage.HOT, PipelineTransitionReason.MANUAL
        )


def test_force_bypasses_the_transition_graph() -> None:
    """An approver-level correction can move a lead anywhere."""
    result = validate_transition(
        PipelineStage.CONVERTED,
        PipelineStage.CONTACTED,
        PipelineTransitionReason.FORCED,
        force=True,
    )
    assert result.to_stage is PipelineStage.CONTACTED


def test_target_stage_for_book_call_is_hot() -> None:
    """The one reply category that routes straight to Hot."""
    assert target_stage_for_intent(ReplyIntent.BOOK_CALL) is PipelineStage.HOT


def test_target_stage_for_pricing_is_interested() -> None:
    """Pricing questions signal engagement, not yet a call request."""
    assert target_stage_for_intent(ReplyIntent.PRICING) is PipelineStage.INTERESTED


def test_target_stage_for_not_interested_is_lost() -> None:
    """A decline routes to Lost."""
    assert target_stage_for_intent(ReplyIntent.NOT_INTERESTED) is PipelineStage.LOST


def test_target_stage_for_unclear_is_none() -> None:
    """An unclear reply does not imply any automatic advancement."""
    assert target_stage_for_intent(ReplyIntent.UNCLEAR) is None


def test_next_stage_towards_walks_new_toward_hot() -> None:
    """A New lead with a book_call reply advances one step at a time."""
    assert next_stage_towards(PipelineStage.NEW, PipelineStage.HOT) is PipelineStage.CONTACTED


def test_next_stage_towards_direct_step_when_already_adjacent() -> None:
    """When the target is directly reachable, that's the next stage."""
    assert (
        next_stage_towards(PipelineStage.CONTACTED, PipelineStage.HOT) is PipelineStage.HOT
    )


def test_next_stage_towards_returns_none_when_already_at_target() -> None:
    """No movement needed if the lead already meets the implied target."""
    assert next_stage_towards(PipelineStage.HOT, PipelineStage.INTERESTED) is None


def test_next_stage_towards_returns_none_from_terminal_stages() -> None:
    """A converted or lost lead is never walked toward another target."""
    assert next_stage_towards(PipelineStage.CONVERTED, PipelineStage.HOT) is None
    assert next_stage_towards(PipelineStage.LOST, PipelineStage.HOT) is None


def test_next_stage_towards_never_routes_through_lost() -> None:
    """Lost is adjacent to every stage but must never be used as a waypoint."""
    # From INTERESTED, the only paths are HOT and LOST; toward HOT it should
    # step directly to HOT, never detouring through LOST.
    assert next_stage_towards(PipelineStage.INTERESTED, PipelineStage.HOT) is PipelineStage.HOT
