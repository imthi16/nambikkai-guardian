"""Request and response bodies for the citation-resolution endpoint."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.citations.types import ResolvedCitation


class CitationResolveRequest(BaseModel):
    """An untrusted citation reference to validate against stored provenance.

    Offsets are relative to the cited chunk's content. Deliberately no field
    bounds are declared here: range and length are *domain* rules the resolver
    owns, so an out-of-range offset or an over-long quote surfaces as the stable
    ``{code, message}`` citation error (e.g. ``citation_out_of_range``) rather
    than a generic Pydantic validation list, and any quote the pipeline can
    legitimately emit — including an atomic table row longer than a fixed cap —
    still round-trips. Every field is verified server-side and nothing here is
    trusted or echoed back unverified.
    """

    document_version_id: uuid.UUID
    chunk_id: uuid.UUID
    quote: str
    quote_char_start: int
    quote_char_end: int


class ResolvedCitationResponse(BaseModel):
    """A citation proven against immutable, authorized provenance.

    ``supporting_text`` is read back from stored content at the validated
    offsets. Offsets are exposed relative to the chunk and to the page
    (``page_quote_char_*``); chunk offsets never span pages, so a page-relative
    position is the strongest offset available without a persisted per-page
    base. OCR provenance travels with the citation, and ``support_score`` is
    ``None`` when reliability is unknown (OCR text with no recorded confidence).
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
    page_quote_char_start: int
    page_quote_char_end: int
    supporting_text: str
    ocr_engine: str | None
    ocr_confidence: float | None
    support_score: float | None

    @classmethod
    def from_resolved(cls, resolved: ResolvedCitation) -> ResolvedCitationResponse:
        return cls(
            document_id=resolved.document_id,
            document_title=resolved.document_title,
            document_version_id=resolved.document_version_id,
            version_number=resolved.version_number,
            chunk_id=resolved.chunk_id,
            chunk_index=resolved.chunk_index,
            page_number=resolved.page_number,
            section=resolved.section,
            language=resolved.language,
            quote=resolved.quote,
            quote_char_start=resolved.quote_char_start,
            quote_char_end=resolved.quote_char_end,
            chunk_char_start=resolved.chunk_char_start,
            chunk_char_end=resolved.chunk_char_end,
            page_quote_char_start=resolved.page_quote_char_start,
            page_quote_char_end=resolved.page_quote_char_end,
            supporting_text=resolved.supporting_text,
            ocr_engine=resolved.ocr_engine,
            ocr_confidence=resolved.ocr_confidence,
            support_score=resolved.support_score,
        )
