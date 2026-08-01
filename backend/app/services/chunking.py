"""Text chunking for the Services Knowledge Base.

Standard library only, so the splitting rules can be unit-tested without a
database or an LLM client.

Strategy: split on paragraph boundaries, then pack paragraphs greedily into
chunks up to ``max_chars``. A paragraph longer than ``max_chars`` on its own is
split on sentence boundaries, and only then on hard character offsets. Adjacent
chunks share ``overlap_chars`` of trailing text so a fact spanning a boundary
is still retrievable.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 150

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"[ \t]+")


def normalize_text(text: str) -> str:
    """Collapse redundant whitespace while preserving paragraph breaks.

    Args:
        text: Raw source text.

    Returns:
        Text with runs of spaces/tabs collapsed and trailing whitespace
        stripped from each line.
    """
    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _split_oversized(segment: str, max_chars: int) -> list[str]:
    """Break a single oversized paragraph into pieces within ``max_chars``.

    Args:
        segment: The paragraph to split.
        max_chars: Maximum characters per resulting piece.

    Returns:
        A list of pieces, each no longer than ``max_chars``.
    """
    pieces: list[str] = []
    buffer = ""

    for sentence in _SENTENCE_SPLIT.split(segment):
        if not sentence:
            continue
        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            pieces.append(buffer)
            buffer = ""
        # A single sentence longer than the budget: hard-split it.
        while len(sentence) > max_chars:
            pieces.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        buffer = sentence

    if buffer:
        pieces.append(buffer)
    return pieces


def chunk_text(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split text into overlapping chunks suitable for embedding.

    Args:
        text: The source text.
        max_chars: Maximum characters per chunk.
        overlap_chars: Characters of trailing context repeated at the start of
            each subsequent chunk. Must be smaller than ``max_chars``.

    Returns:
        An ordered list of chunks. Empty when ``text`` has no content.

    Raises:
        ValueError: If ``max_chars`` is not positive, or ``overlap_chars`` is
            negative or not smaller than ``max_chars``.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must not be negative")
    if overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")

    cleaned = normalize_text(text)
    if not cleaned:
        return []

    segments: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT.split(cleaned):
        stripped = paragraph.strip()
        if not stripped:
            continue
        if len(stripped) <= max_chars:
            segments.append(stripped)
        else:
            segments.extend(_split_oversized(stripped, max_chars))

    chunks: list[str] = []
    buffer = ""
    for segment in segments:
        candidate = f"{buffer}\n\n{segment}" if buffer else segment
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            chunks.append(buffer)
            tail = buffer[-overlap_chars:] if overlap_chars else ""
            buffer = f"{tail}\n\n{segment}".strip() if tail else segment
            # Overlap can push the new buffer over budget; emit it as-is.
            if len(buffer) > max_chars:
                chunks.append(buffer[:max_chars])
                buffer = buffer[max_chars:]
        else:
            buffer = segment

    if buffer.strip():
        chunks.append(buffer.strip())

    logger.info("Chunked text", extra={"input_chars": len(cleaned), "chunks": len(chunks)})
    return chunks
