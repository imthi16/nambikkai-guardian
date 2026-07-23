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


def _reliability(provenance: ChunkProvenance) -> float:
    """Source-reliability score in [0, 1] for a chunk's support span.

    Born-digital text (no OCR engine) is fully reliable. OCR-derived text is
    only as reliable as its recorded confidence; an OCR chunk with no recorded
    confidence is treated as reliable rather than penalized, matching the
    verifier's OCR handling.
    """
    if provenance.ocr_engine and provenance.ocr_confidence is not None:
        return max(0.0, min(1.0, provenance.ocr_confidence))
    return 1.0


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
        if provenance is None:
            # Fake, deleted, or cross-tenant: all indistinguishable by design.
            raise CitationError(
                CitationErrorCode.NOT_FOUND,
                "The cited chunk does not exist in this workspace.",
            )
        if provenance.document_version_id != reference.document_version_id:
            # The chunk exists but not under the claimed version: a stale or
            # mismatched reference. Reported as not-found, not as a mismatch, so
            # it cannot be used to probe which versions a chunk belongs to.
            raise CitationError(
                CitationErrorCode.NOT_FOUND,
                "The cited chunk does not belong to the referenced document version.",
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
