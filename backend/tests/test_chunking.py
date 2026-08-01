"""Tests for :mod:`app.services.chunking`."""

from __future__ import annotations

import pytest

from app.services.chunking import chunk_text, normalize_text


def test_normalize_collapses_spaces_but_keeps_paragraphs() -> None:
    """Runs of spaces collapse while blank-line paragraph breaks survive."""
    result = normalize_text("Hello    world  \n\n  Second   paragraph ")
    assert result == "Hello world\n\nSecond paragraph"


def test_empty_input_yields_no_chunks() -> None:
    """Whitespace-only input produces an empty chunk list."""
    assert chunk_text("   \n\n  ") == []


def test_short_text_is_a_single_chunk() -> None:
    """Text under the budget is not split."""
    chunks = chunk_text("A short service description.", max_chars=200)
    assert chunks == ["A short service description."]


def test_paragraphs_pack_into_budgeted_chunks() -> None:
    """Paragraphs are packed greedily and every chunk respects max_chars."""
    text = "\n\n".join(f"Paragraph {index} " + ("word " * 20) for index in range(10))
    chunks = chunk_text(text, max_chars=300, overlap_chars=50)

    assert len(chunks) > 1
    assert all(len(chunk) <= 300 for chunk in chunks)


def test_oversized_single_paragraph_is_split() -> None:
    """A paragraph longer than the budget is broken up rather than emitted whole."""
    text = " ".join(f"Sentence number {index}." for index in range(200))
    chunks = chunk_text(text, max_chars=250, overlap_chars=40)

    assert len(chunks) > 1
    assert all(len(chunk) <= 250 for chunk in chunks)


def test_sentence_with_no_boundaries_is_hard_split() -> None:
    """A single unbroken token longer than the budget still gets split."""
    chunks = chunk_text("x" * 1000, max_chars=100, overlap_chars=10)

    assert len(chunks) >= 10
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_content_is_preserved_across_chunks() -> None:
    """No source paragraph is silently dropped during chunking."""
    text = "\n\n".join(f"Unique marker {index} here." for index in range(30))
    joined = " ".join(chunk_text(text, max_chars=200, overlap_chars=20))

    for index in range(30):
        assert f"Unique marker {index}" in joined


@pytest.mark.parametrize(
    ("max_chars", "overlap_chars"),
    [(0, 10), (-5, 1), (100, -1), (100, 100), (100, 150)],
)
def test_invalid_parameters_are_rejected(max_chars: int, overlap_chars: int) -> None:
    """Nonsensical chunk parameters raise rather than silently misbehaving."""
    with pytest.raises(ValueError):
        chunk_text("some text", max_chars=max_chars, overlap_chars=overlap_chars)
