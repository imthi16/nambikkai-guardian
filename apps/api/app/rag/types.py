"""Grounded-answer domain types: evidence, claims, citations, and the trace.

These shapes are provider- and framework-agnostic so the pipeline can be
tested without a database, an LLM, or LangGraph. The guiding rule of the
platform lives here: an answer is only ever assembled from evidence the
retrieval layer authorized, every claim carries a citation back to an exact
evidence span, and unsupported claims are dropped rather than surfaced.

Evidence text is untrusted data throughout. Nothing in an evidence passage is
ever treated as an instruction; it is only quoted, scored, and cited.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class AnswerOutcome(StrEnum):
    """The grounding outcome the pipeline reached for one query."""

    ANSWERED = "answered"
    PARTIAL = "partial"
    ABSTAINED = "abstained"


class ClaimVerdict(StrEnum):
    """Verification outcome for one atomic claim.

    Mirrors the persisted ``app.db.models.enums.ClaimVerdict`` value set but is
    kept independent so the pipeline never imports the ORM.
    """

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class EvidencePassage:
    """The minimal, fully-provenanced view of one authorized chunk.

    Only these fields are handed to generation: the text to quote and the
    provenance a citation needs. Retrieval and rerank ranks/scores are carried
    so confidence can combine them, but the raw query and secrets never are.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    content: str
    page_number: int | None
    section: str | None
    char_start: int
    char_end: int
    language: str | None
    ocr_engine: str | None
    ocr_confidence: float | None
    fused_score: float
    rerank_score: float | None
    order: int  # 0-based position in the ranked evidence list
    # The reranker's *absolute* relevance score, distinct from ``rerank_score``
    # which is min-max normalized within one result set (so the top candidate
    # is always 1.0 and cannot be read as calibrated evidence). Confidence uses
    # this raw value; ``None`` when the reranker did not run.
    rerank_raw_score: float | None = None


@dataclass(frozen=True)
class Citation:
    """Links one claim span to the exact evidence span that supports it.

    Offsets are relative to the source chunk's content so a viewer can
    highlight the quote. Chunk offsets never span pages, so adding
    ``quote_char_start``/``quote_char_end`` to the chunk's own ``char_start``
    recovers a *page-relative* offset, not a document-wide one.
    """

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    quote: str
    quote_char_start: int
    quote_char_end: int
    page_number: int | None
    section: str | None
    language: str | None
    # The source chunk's own span within its page, so a client can recover the
    # page-relative quote offsets without re-fetching the chunk.
    chunk_char_start: int
    chunk_char_end: int
    # OCR provenance for the source chunk (``None`` for born-digital text), so a
    # consumer can weigh the reliability of the evidence the quote came from.
    ocr_engine: str | None
    ocr_confidence: float | None

    @property
    def page_quote_char_start(self) -> int:
        """The quote's start offset within its page's text."""
        return self.chunk_char_start + self.quote_char_start

    @property
    def page_quote_char_end(self) -> int:
        """The quote's end offset within its page's text."""
        return self.chunk_char_start + self.quote_char_end

    def as_metadata(self) -> dict[str, object]:
        return {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "page_number": self.page_number,
            "section": self.section,
            "quote_char_start": self.quote_char_start,
            "quote_char_end": self.quote_char_end,
            "page_quote_char_start": self.page_quote_char_start,
            "page_quote_char_end": self.page_quote_char_end,
            "ocr_engine": self.ocr_engine,
            "ocr_confidence": self.ocr_confidence,
        }


@dataclass(frozen=True)
class AtomicClaim:
    """One atomic, independently-verifiable statement drawn from evidence.

    In the extractive MVP the claim text is a verbatim quote from a single
    evidence passage, so the citation supports the claim by construction. The
    verdict and confidence are still computed explicitly so the same shape
    serves a future abstractive generator whose claims must be verified.
    """

    index: int
    text: str
    citation: Citation
    verdict: ClaimVerdict
    confidence: float
    # Why the verifier reached this verdict (entailment outcome, e.g. full
    # support or the reason a claim was dropped). Non-sensitive; safe to log.
    explanation: str = ""

    @property
    def is_supported(self) -> bool:
        return self.verdict is ClaimVerdict.SUPPORTED

    def as_metadata(self) -> dict[str, object]:
        return {
            "index": self.index,
            "verdict": self.verdict.value,
            "confidence": round(self.confidence, 4),
            "explanation": self.explanation,
            "citation": self.citation.as_metadata(),
        }


@dataclass(frozen=True)
class GroundedAnswer:
    """The final answer: outcome, supported claims, citations, and confidence.

    When ``outcome`` is ``ABSTAINED`` the answer text is a fixed refusal and
    ``claims`` is empty. Callers must treat ``ANSWERED``/``PARTIAL`` as the only
    states carrying assertions, and every assertion is backed by ``claims``.
    """

    outcome: AnswerOutcome
    text: str
    claims: tuple[AtomicClaim, ...]
    confidence: float
    abstention_reason: str | None = None
    # The calibrated 5-way operational decision (answer / answer_with_warning /
    # ask_for_clarification / abstain / escalate_for_review) and its rationale.
    decision: str = "abstain"
    decision_reason: str = ""

    @property
    def citations(self) -> tuple[Citation, ...]:
        return tuple(claim.citation for claim in self.claims)


@dataclass
class RagTrace:
    """A structured, non-sensitive record of one grounded-answer run.

    Carries counts, gate decisions, timings, and configuration but never the
    raw query, evidence text, answer text, or secrets, so it is safe to log,
    return to clients, and persist as an audit detail.
    """

    workspace_id: uuid.UUID
    detected_language: str
    top_k: int
    retrieved_count: int = 0
    evidence_count: int = 0
    sufficient: bool = False
    draft_claim_count: int = 0
    supported_claim_count: int = 0
    partial_claim_count: int = 0
    contradicted_claim_count: int = 0
    unsupported_claim_count: int = 0
    dropped_claim_count: int = 0
    outcome: str = AnswerOutcome.ABSTAINED.value
    decision: str = "abstain"
    decision_reason: str = ""
    confidence: float = 0.0
    abstained: bool = True
    abstention_reason: str | None = None
    generator: str | None = None
    verifier: str | None = None
    retrieval_ms: float = 0.0
    generation_ms: float = 0.0
    verification_ms: float = 0.0
    total_ms: float = 0.0
    retrieval: dict[str, object] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, object]:
        return {
            "workspace_id": str(self.workspace_id),
            "detected_language": self.detected_language,
            "top_k": self.top_k,
            "retrieved_count": self.retrieved_count,
            "evidence_count": self.evidence_count,
            "sufficient": self.sufficient,
            "draft_claim_count": self.draft_claim_count,
            "supported_claim_count": self.supported_claim_count,
            "partial_claim_count": self.partial_claim_count,
            "contradicted_claim_count": self.contradicted_claim_count,
            "unsupported_claim_count": self.unsupported_claim_count,
            "dropped_claim_count": self.dropped_claim_count,
            "outcome": self.outcome,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "confidence": round(self.confidence, 4),
            "abstained": self.abstained,
            "abstention_reason": self.abstention_reason,
            "generator": self.generator,
            "verifier": self.verifier,
            "timings_ms": {
                "retrieval": round(self.retrieval_ms, 3),
                "generation": round(self.generation_ms, 3),
                "verification": round(self.verification_ms, 3),
                "total": round(self.total_ms, 3),
            },
            "retrieval": self.retrieval,
        }


@dataclass(frozen=True)
class RagResult:
    """The grounded answer plus its trace, returned by the service."""

    answer: GroundedAnswer
    trace: RagTrace
