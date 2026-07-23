"""Resolve and validate untrusted citation references against provenance.

The resolver is the trust gate for citations. It reads a chunk's immutable
provenance through a workspace-scoped port (so a reference to another tenant's
chunk is indistinguishable from one that does not exist), then proves the
reference in order: the chunk must exist in this workspace, belong to the named
document version, carry in-range offsets, and quote the stored content
*exactly*. Only then is a :class:`ResolvedCitation` returned, with supporting
text sliced from storage rather than echoed from the request.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from app.citations.types import (
    ChunkProvenance,
    CitationError,
    CitationErrorCode,
    CitationReference,
    ResolvedCitation,
)


@runtime_checkable
class ChunkProvenanceReader(Protocol):
    """Loads immutable provenance for a chunk within one authorized workspace.

    Implementations must enforce the workspace boundary in the data layer and
    return ``None`` for any chunk this tenant may not read, so the resolver
    cannot leak the existence of another workspace's chunk.
    """

    async def get_provenance(self, chunk_id: uuid.UUID) -> ChunkProvenance | None: ...


def _reliability(provenance: ChunkProvenance) -> float | None:
    """Source-reliability score in [0, 1] for a chunk's support span.

    Only born-digital text (no OCR engine) is fully reliable (``1.0``).
    OCR-derived text is worth exactly its recorded confidence; when an OCR
    chunk has *no* recorded confidence the reliability is genuinely unknown, so
    this returns ``None`` (unavailable) rather than presenting unverified OCR as
    perfectly reliable.
    """
    if provenance.ocr_engine is None:
        return 1.0
    if provenance.ocr_confidence is None:
        return None
    return max(0.0, min(1.0, provenance.ocr_confidence))


class CitationResolver:
    """Validates citation references and resolves them to authorized provenance."""

    def __init__(self, reader: ChunkProvenanceReader) -> None:
        self._reader = reader

    async def resolve(self, reference: CitationReference) -> ResolvedCitation:
        """Prove ``reference`` and return its resolved, authorized citation.

        Raises :class:`CitationError` when the reference is fake, stale,
        cross-tenant, out of range, or does not quote the stored text exactly.
        """
        provenance = await self._reader.get_provenance(reference.chunk_id)
        # Fake, deleted, cross-tenant, and version-mismatched references must be
        # indistinguishable, so both the missing-chunk case and the
        # wrong-version case raise the *same* generic not-found error: a caller
        # holding a candidate chunk id can learn nothing about which versions it
        # belongs to or whether it exists in another workspace.
        if provenance is None or provenance.document_version_id != reference.document_version_id:
            raise CitationError(
                CitationErrorCode.NOT_FOUND,
                "The cited chunk does not exist in this workspace.",
            )

        start, end = reference.quote_char_start, reference.quote_char_end
        if not (0 <= start < end <= len(provenance.content)):
            raise CitationError(
                CitationErrorCode.OUT_OF_RANGE,
                "The citation offsets fall outside the cited chunk.",
            )

        supporting_text = provenance.content[start:end]
        if supporting_text != reference.quote:
            raise CitationError(
                CitationErrorCode.QUOTE_MISMATCH,
                "The citation quote does not match the cited chunk text.",
            )

        return ResolvedCitation(
            document_id=provenance.document_id,
            document_title=provenance.document_title,
            document_version_id=provenance.document_version_id,
            version_number=provenance.version_number,
            chunk_id=provenance.chunk_id,
            chunk_index=provenance.chunk_index,
            page_number=provenance.page_number,
            section=provenance.section,
            language=provenance.language,
            quote=reference.quote,
            quote_char_start=start,
            quote_char_end=end,
            chunk_char_start=provenance.char_start,
            chunk_char_end=provenance.char_end,
            supporting_text=supporting_text,
            ocr_engine=provenance.ocr_engine,
            ocr_confidence=provenance.ocr_confidence,
            support_score=_reliability(provenance),
        )
