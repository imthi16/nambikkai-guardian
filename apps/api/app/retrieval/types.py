"""Retrieval domain types: candidates, fused results, and the query trace.

These shapes are provider- and storage-agnostic so the service can be tested
with fakes. Provenance travels with every result (document, page, section,
offsets, language, OCR) because citation and verification downstream depend on
it. Chunk content is treated as untrusted data throughout.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class RetrievalSource(StrEnum):
    """Which retriever produced a candidate."""

    LEXICAL = "lexical"
    DENSE = "dense"


@dataclass(frozen=True)
class ScoredCandidate:
    """One retriever's ranked hit for a chunk (rank is 1-based)."""

    chunk_id: uuid.UUID
    rank: int
    score: float


@dataclass(frozen=True)
class RetrievedChunk:
    """A fused, authorized result with the provenance a citation needs."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    content: str
    fused_score: float
    lexical_rank: int | None
    dense_rank: int | None
    page_number: int | None
    section: str | None
    char_start: int
    char_end: int
    language: str | None
    ocr_engine: str | None
    ocr_confidence: float | None
    rerank_score: float | None = None
    # The reranker's absolute (un-normalized) score; ``rerank_score`` above is
    # normalized within the result set and is not comparable across queries.
    rerank_raw_score: float | None = None
    rerank_rank: int | None = None


@dataclass(frozen=True)
class RetrievalFilters:
    """Optional metadata narrowing applied before ranking."""

    document_id: uuid.UUID | None = None
    language: str | None = None

    def as_metadata(self) -> dict[str, object]:
        return {
            "document_id": str(self.document_id) if self.document_id else None,
            "language": self.language,
        }


@dataclass
class RetrievalTrace:
    """A structured, non-sensitive record of how one retrieval ran.

    Deliberately carries counts, ranks, timings, and configuration but never
    chunk text, the raw query, or secrets, so it is safe to log and return.
    """

    workspace_id: uuid.UUID
    detected_language: str
    variant_count: int
    filters: dict[str, object]
    rrf_k: int
    candidate_limit: int
    top_k: int
    lexical_count: int = 0
    dense_count: int = 0
    fused_count: int = 0
    returned_count: int = 0
    lexical_ms: float = 0.0
    dense_ms: float = 0.0
    fusion_ms: float = 0.0
    reranked: bool = False
    rerank_model: str | None = None
    rerank_ms: float = 0.0
    rerank_dropped: int = 0
    fused_scores: list[float] = field(default_factory=list)

    def as_metadata(self) -> dict[str, object]:
        return {
            "workspace_id": str(self.workspace_id),
            "detected_language": self.detected_language,
            "variant_count": self.variant_count,
            "filters": self.filters,
            "rrf_k": self.rrf_k,
            "candidate_limit": self.candidate_limit,
            "top_k": self.top_k,
            "lexical_count": self.lexical_count,
            "dense_count": self.dense_count,
            "fused_count": self.fused_count,
            "returned_count": self.returned_count,
            "reranked": self.reranked,
            "rerank_model": self.rerank_model,
            "rerank_dropped": self.rerank_dropped,
            "timings_ms": {
                "lexical": round(self.lexical_ms, 3),
                "dense": round(self.dense_ms, 3),
                "fusion": round(self.fusion_ms, 3),
                "rerank": round(self.rerank_ms, 3),
            },
        }


@dataclass(frozen=True)
class RetrievalResult:
    """The final ordered evidence plus its trace."""

    chunks: list[RetrievedChunk]
    trace: RetrievalTrace
