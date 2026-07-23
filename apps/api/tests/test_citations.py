"""Unit tests for citation resolution (no database).

Exercise the resolver against a fake provenance reader: a valid reference
resolves to immutable provenance with supporting text sliced from stored
content, and fake, stale, out-of-range, and quote-mismatched references each
fail with their stable code. Cross-tenant behaviour (a chunk this reader may
not see returns ``None``) is covered by the not-found case here and end-to-end
in the integration suite.
"""

from __future__ import annotations

import uuid

import pytest
from app.citations.resolver import CitationResolver
from app.citations.types import (
    ChunkProvenance,
    CitationError,
    CitationErrorCode,
    CitationReference,
)

CHUNK_ID = uuid.UUID(int=100)
VERSION_ID = uuid.UUID(int=200)
CONTENT = "The invoice payment is due within thirty days of receipt."


def _provenance(
    *,
    content: str = CONTENT,
    ocr_engine: str | None = None,
    ocr_confidence: float | None = None,
    char_start: int = 500,
) -> ChunkProvenance:
    return ChunkProvenance(
        chunk_id=CHUNK_ID,
        chunk_index=3,
        document_id=uuid.UUID(int=9),
        document_title="Vendor Agreement",
        document_version_id=VERSION_ID,
        version_number=2,
        content=content,
        page_number=7,
        section="Payment terms",
        char_start=char_start,
        char_end=char_start + len(content),
        language="eng",
        ocr_engine=ocr_engine,
        ocr_confidence=ocr_confidence,
    )


class FakeReader:
    """Returns fixed provenance for one chunk id, ``None`` for anything else."""

    def __init__(self, provenance: ChunkProvenance | None) -> None:
        self._provenance = provenance

    async def get_provenance(self, chunk_id: uuid.UUID) -> ChunkProvenance | None:
        if self._provenance is not None and chunk_id == self._provenance.chunk_id:
            return self._provenance
        return None


def _reference(
    *,
    quote: str,
    start: int,
    end: int,
    version_id: uuid.UUID = VERSION_ID,
    chunk_id: uuid.UUID = CHUNK_ID,
) -> CitationReference:
    return CitationReference(
        document_version_id=version_id,
        chunk_id=chunk_id,
        quote=quote,
        quote_char_start=start,
        quote_char_end=end,
    )


async def test_resolves_valid_reference_to_immutable_provenance() -> None:
    resolver = CitationResolver(FakeReader(_provenance()))
    quote = "payment is due within thirty days"
    start = CONTENT.index(quote)
    resolved = await resolver.resolve(_reference(quote=quote, start=start, end=start + len(quote)))

    assert resolved.supporting_text == quote
    assert resolved.document_title == "Vendor Agreement"
    assert resolved.version_number == 2
    assert resolved.page_number == 7
    assert resolved.section == "Payment terms"
    # Page-relative offsets are the chunk's own start plus the in-chunk span.
    assert resolved.page_quote_char_start == 500 + start
    assert resolved.page_quote_char_end == 500 + start + len(quote)
    # Born-digital text is fully reliable.
    assert resolved.support_score == 1.0


async def test_supporting_text_comes_from_storage_not_the_request() -> None:
    """A caller cannot dictate the supporting text; it is sliced from content."""
    resolver = CitationResolver(FakeReader(_provenance()))
    quote = "invoice payment"
    start = CONTENT.index(quote)
    # The quote is honest here (it must match), but the returned supporting_text
    # is independently read from stored content at the validated offsets.
    resolved = await resolver.resolve(_reference(quote=quote, start=start, end=start + len(quote)))
    assert resolved.supporting_text == CONTENT[start : start + len(quote)]


async def test_unknown_chunk_is_not_found() -> None:
    resolver = CitationResolver(FakeReader(_provenance()))
    reference = _reference(quote="x", start=0, end=1, chunk_id=uuid.UUID(int=999))
    with pytest.raises(CitationError) as excinfo:
        await resolver.resolve(reference)
    assert excinfo.value.code is CitationErrorCode.NOT_FOUND


async def test_missing_provenance_is_not_found() -> None:
    """A reader that can see no chunk (e.g. cross-tenant) yields not-found."""
    resolver = CitationResolver(FakeReader(None))
    reference = _reference(quote="The", start=0, end=3)
    with pytest.raises(CitationError) as excinfo:
        await resolver.resolve(reference)
    assert excinfo.value.code is CitationErrorCode.NOT_FOUND


async def test_wrong_document_version_is_not_found() -> None:
    resolver = CitationResolver(FakeReader(_provenance()))
    reference = _reference(quote="The", start=0, end=3, version_id=uuid.UUID(int=777))
    with pytest.raises(CitationError) as excinfo:
        await resolver.resolve(reference)
    assert excinfo.value.code is CitationErrorCode.NOT_FOUND


async def test_out_of_range_offsets_are_rejected() -> None:
    resolver = CitationResolver(FakeReader(_provenance()))
    reference = _reference(quote="x", start=0, end=len(CONTENT) + 5)
    with pytest.raises(CitationError) as excinfo:
        await resolver.resolve(reference)
    assert excinfo.value.code is CitationErrorCode.OUT_OF_RANGE


async def test_inverted_span_is_rejected_as_out_of_range() -> None:
    resolver = CitationResolver(FakeReader(_provenance()))
    reference = _reference(quote="x", start=10, end=10)
    with pytest.raises(CitationError) as excinfo:
        await resolver.resolve(reference)
    assert excinfo.value.code is CitationErrorCode.OUT_OF_RANGE


async def test_quote_not_matching_stored_text_is_rejected() -> None:
    resolver = CitationResolver(FakeReader(_provenance()))
    # In-range offsets, but the quote is not what the chunk says there.
    reference = _reference(quote="payment is waived", start=0, end=17)
    with pytest.raises(CitationError) as excinfo:
        await resolver.resolve(reference)
    assert excinfo.value.code is CitationErrorCode.QUOTE_MISMATCH


async def test_ocr_confidence_becomes_support_score() -> None:
    resolver = CitationResolver(FakeReader(_provenance(ocr_engine="paddle", ocr_confidence=0.42)))
    quote = "invoice"
    start = CONTENT.index(quote)
    resolved = await resolver.resolve(_reference(quote=quote, start=start, end=start + len(quote)))
    assert resolved.support_score == pytest.approx(0.42)
    assert resolved.ocr_engine == "paddle"


async def test_ocr_chunk_without_recorded_confidence_has_unknown_support() -> None:
    """Unknown OCR reliability is reported as None, not as perfect (1.0)."""
    resolver = CitationResolver(FakeReader(_provenance(ocr_engine="paddle", ocr_confidence=None)))
    quote = "invoice"
    start = CONTENT.index(quote)
    resolved = await resolver.resolve(_reference(quote=quote, start=start, end=start + len(quote)))
    assert resolved.support_score is None
    assert resolved.ocr_engine == "paddle"
