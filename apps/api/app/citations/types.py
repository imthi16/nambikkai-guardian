"""Domain types for the structured citation system.

A :class:`CitationReference` is *untrusted* input: it names a chunk, a document
version, a quoted span, and that span's offsets, exactly as a client (or a
model) claims them. Resolving it proves the reference against immutable,
workspace-authorized provenance and yields a :class:`ResolvedCitation` whose
supporting text is read back from storage — never echoed from the request.

The resolver is deliberately conservative and framework-agnostic: it raises a
:class:`CitationError` with a stable code for every way a reference can be
invalid, and the values it carries (chunk content, quotes) are only ever
compared and sliced, never interpreted as instructions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


class CitationErrorCode(StrEnum):
    """Stable, machine-readable reasons a citation failed validation.

    ``NOT_FOUND`` intentionally covers fake, stale, deleted, *and* cross-tenant
    references alike, so a caller can never distinguish "exists in another
    workspace" from "does not exist" — the same non-disclosure rule the
    workspace boundary uses.
    """

    NOT_FOUND = "citation_not_found"
    OUT_OF_RANGE = "citation_out_of_range"
    QUOTE_MISMATCH = "citation_quote_mismatch"


class CitationError(Exception):
    """A citation reference could not be resolved to authorized provenance."""

    def __init__(self, code: CitationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CitationReference:
    """An untrusted claim that a span of a chunk supports something.

    ``quote_char_start``/``quote_char_end`` are offsets *within the chunk's
    content* (not the document). Everything here is supplied by the caller and
    is verified during resolution; nothing is trusted.
    """

    document_version_id: uuid.UUID
    chunk_id: uuid.UUID
    quote: str
    quote_char_start: int
    quote_char_end: int


@dataclass(frozen=True)
class ChunkProvenance:
    """Immutable provenance for one chunk, loaded through the tenant boundary.

    This is what the data layer returns for an authorized chunk; the resolver
    turns it (plus a validated span) into a :class:`ResolvedCitation`.
    """

    chunk_id: uuid.UUID
    chunk_index: int
    document_id: uuid.UUID
    document_title: str
    document_version_id: uuid.UUID
    version_number: int
    content: str
    page_number: int | None
    section: str | None
    char_start: int
    char_end: int
    language: str | None
    ocr_engine: str | None
    ocr_confidence: float | None


@dataclass(frozen=True)
class ResolvedCitation:
    """A citation proven against immutable, authorized provenance.

    ``supporting_text`` is sliced from stored chunk content at the validated
    offsets, so it is the exact evidence span — never the caller's quote echoed
    back. ``support_score`` is a deterministic source-reliability signal in
    ``[0, 1]``: born-digital text is fully reliable (``1.0``) and OCR-derived
    spans carry their recorded OCR confidence, but it is ``None`` when the span
    came from OCR with no recorded confidence (reliability genuinely unknown).
    It is distinct from the answer-time claim confidence computed during
    verification, which blends query-dependent retrieval signals this standalone
    lookup does not have.

    Offsets are *page-relative*: chunk offsets never span pages, so
    ``page_quote_char_start``/``page_quote_char_end`` locate the span within its
    page's text (``chunk_char_start`` + the in-chunk quote offset). A true
    document-wide offset would require a persisted per-page base this lookup
    does not have, so it is deliberately not claimed.
    """

    document_id: uuid.UUID
    document_title: str
    document_version_id: uuid.UUID
    version_number: int
    chunk_id: uuid.UUID
    chunk_index: int
    page_number: int | None
    section: str | None
    language: str | None
    quote: str
    quote_char_start: int
    quote_char_end: int
    chunk_char_start: int
    chunk_char_end: int
    supporting_text: str
    ocr_engine: str | None
    ocr_confidence: float | None
    support_score: float | None

    @property
    def page_quote_char_start(self) -> int:
        """The span's start offset within its page's text."""
        return self.chunk_char_start + self.quote_char_start

    @property
    def page_quote_char_end(self) -> int:
        """The span's end offset within its page's text."""
        return self.chunk_char_start + self.quote_char_end
