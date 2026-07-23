"""Request and response bodies for the grounded-answer endpoint."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.rag.types import GroundedAnswer, RagResult


class AnswerRequest(BaseModel):
    """A grounded-answer query with optional evidence filters."""

    query: str = Field(min_length=1, max_length=2000)
    document_id: uuid.UUID | None = None
    language: str | None = Field(default=None, max_length=35)
    top_k: int | None = Field(default=None, ge=1)


class CitationResponse(BaseModel):
    """One evidence span supporting a claim, with full provenance.

    Offsets are exposed both relative to the source chunk
    (``quote_char_start``/``quote_char_end``) and relative to the whole document
    (``document_quote_char_start``/``document_quote_char_end``), and the source
    chunk's OCR provenance travels with the citation so a consumer can locate
    and weigh the exact evidence without a second lookup.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    quote: str
    quote_char_start: int
    quote_char_end: int
    document_quote_char_start: int
    document_quote_char_end: int
    page_number: int | None
    section: str | None
    language: str | None
    ocr_engine: str | None
    ocr_confidence: float | None


class ClaimResponse(BaseModel):
    """One supported atomic claim with its verdict, confidence, and citation."""

    index: int
    text: str
    verdict: str
    confidence: float
    citation: CitationResponse


class AnswerResponse(BaseModel):
    """A grounded answer or calibrated abstention plus a non-sensitive trace."""

    outcome: str
    answer: str
    confidence: float
    abstained: bool
    abstention_reason: str | None
    claims: list[ClaimResponse]
    trace: dict[str, object]

    @classmethod
    def from_result(cls, result: RagResult) -> AnswerResponse:
        answer: GroundedAnswer = result.answer
        return cls(
            outcome=answer.outcome.value,
            answer=answer.text,
            confidence=answer.confidence,
            abstained=answer.outcome.value == "abstained",
            abstention_reason=answer.abstention_reason,
            claims=[
                ClaimResponse(
                    index=claim.index,
                    text=claim.text,
                    verdict=claim.verdict.value,
                    confidence=claim.confidence,
                    citation=CitationResponse(
                        chunk_id=claim.citation.chunk_id,
                        document_id=claim.citation.document_id,
                        document_version_id=claim.citation.document_version_id,
                        quote=claim.citation.quote,
                        quote_char_start=claim.citation.quote_char_start,
                        quote_char_end=claim.citation.quote_char_end,
                        document_quote_char_start=claim.citation.document_quote_char_start,
                        document_quote_char_end=claim.citation.document_quote_char_end,
                        page_number=claim.citation.page_number,
                        section=claim.citation.section,
                        language=claim.citation.language,
                        ocr_engine=claim.citation.ocr_engine,
                        ocr_confidence=claim.citation.ocr_confidence,
                    ),
                )
                for claim in answer.claims
            ],
            trace=result.trace.as_metadata(),
        )
