"""The hybrid retrieval service: lexical + dense search fused under RRF.

Orchestration only. Query representation comes from `QueryProcessor`, lexical
and dense candidates come from workspace-scoped repositories (so authorization
and row-level security are enforced in the data layer, never here), and the
two rank lists are merged with Reciprocal Rank Fusion. The service then
hydrates the fused chunk ids into fully-provenanced results and records a
non-sensitive trace.

Because both retrievers are constructed with the caller's `workspace_id`, a
chunk from another tenant can never enter the candidate set; there is no code
path that returns a chunk the repository did not scope.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.documents import Chunk, DocumentVersion
from app.db.repositories.chunks import ChunkRepository, LexicalMatch
from app.db.repositories.embeddings import ChunkEmbeddingRepository, VectorMatch
from app.embeddings.service import EmbeddingService
from app.language.processor import QueryProcessor, get_default_query_processor
from app.reranking.service import RerankService
from app.reranking.types import RerankItem
from app.retrieval.fusion import DEFAULT_RRF_K, FusedCandidate, reciprocal_rank_fusion
from app.retrieval.types import (
    RetrievalFilters,
    RetrievalResult,
    RetrievalTrace,
    RetrievedChunk,
    ScoredCandidate,
)


@dataclass(frozen=True)
class RetrievalConfig:
    """Tunable retrieval limits, defaulted from application settings."""

    rrf_k: int = DEFAULT_RRF_K
    candidate_limit: int = 50
    top_k: int = 10
    rerank_enabled: bool = True
    rerank_candidate_limit: int = 30


class HybridRetrievalService:
    """Runs permission-filtered hybrid retrieval for one workspace query."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_service: EmbeddingService | None = None,
        query_processor: QueryProcessor | None = None,
        rerank_service: RerankService | None = None,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._session = session
        self._embeddings = embedding_service or EmbeddingService()
        self._processor = query_processor or get_default_query_processor()
        self._config = config or RetrievalConfig()
        # A reranker is optional; when disabled the fused order is returned.
        self._reranker = rerank_service
        if self._config.rerank_enabled and self._reranker is None:
            self._reranker = RerankService()

    async def search(
        self,
        *,
        workspace_id: uuid.UUID,
        query: str,
        filters: RetrievalFilters | None = None,
        top_k: int | None = None,
    ) -> RetrievalResult:
        """Retrieve fused, authorized, and (optionally) reranked evidence."""
        active_filters = filters or RetrievalFilters()
        resolved_top_k = top_k or self._config.top_k
        processed = self._processor.process(query)
        reranking = self._config.rerank_enabled and self._reranker is not None

        trace = RetrievalTrace(
            workspace_id=workspace_id,
            detected_language=processed.detection.language.value,
            variant_count=len(processed.search_variants),
            filters=active_filters.as_metadata(),
            rrf_k=self._config.rrf_k,
            candidate_limit=self._config.candidate_limit,
            top_k=resolved_top_k,
        )

        lexical = await self._lexical(
            workspace_id, processed.search_variants, active_filters, trace
        )
        dense = await self._dense(workspace_id, processed.original, active_filters, trace)

        # When reranking, fuse a larger pool so the reranker can promote a
        # candidate the fusion ranked just outside top_k; otherwise fuse to
        # exactly top_k.
        fusion_limit = (
            max(resolved_top_k, self._config.rerank_candidate_limit)
            if reranking
            else resolved_top_k
        )
        fusion_start = time.perf_counter()
        fused = reciprocal_rank_fusion(
            {"lexical": lexical, "dense": dense},
            k=self._config.rrf_k,
            limit=fusion_limit,
        )
        trace.fusion_ms = (time.perf_counter() - fusion_start) * 1000
        trace.fused_count = len(fused)
        trace.fused_scores = [round(item.score, 6) for item in fused]

        hydrated = await self._hydrate(workspace_id, fused)
        if reranking:
            hydrated = self._rerank(processed.original, hydrated, trace)
        hydrated = hydrated[:resolved_top_k]
        trace.returned_count = len(hydrated)
        return RetrievalResult(chunks=hydrated, trace=trace)

    def _rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        trace: RetrievalTrace,
    ) -> list[RetrievedChunk]:
        """Reorder hydrated chunks by reranker score, preserving provenance.

        The reranker only sees chunk text that was already authorized and
        hydrated, so reranking cannot widen the result set or leak data; it can
        only reorder or drop below-threshold candidates.
        """
        assert self._reranker is not None  # noqa: S101 - guarded by caller
        items = [RerankItem(chunk_id=chunk.chunk_id, text=chunk.content) for chunk in chunks]
        outcome = self._reranker.rerank(query, items)

        trace.reranked = not outcome.metrics.failed
        trace.rerank_model = self._reranker.model
        trace.rerank_ms = outcome.metrics.duration_ms
        trace.rerank_dropped = outcome.result.dropped_below_threshold

        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        reordered: list[RetrievedChunk] = []
        for ranked in outcome.result.items:
            base = by_id.get(ranked.chunk_id)
            if base is None:
                continue
            reordered.append(
                replace(
                    base,
                    rerank_score=ranked.normalized_score,
                    rerank_raw_score=ranked.raw_score,
                    rerank_rank=ranked.rank,
                )
            )
        return reordered

    async def _lexical(
        self,
        workspace_id: uuid.UUID,
        variants: Sequence[str],
        filters: RetrievalFilters,
        trace: RetrievalTrace,
    ) -> list[ScoredCandidate]:
        repo = ChunkRepository(self._session, workspace_id)
        start = time.perf_counter()
        matches: Sequence[LexicalMatch] = await repo.lexical_search(
            variants,
            limit=self._config.candidate_limit,
            document_id=filters.document_id,
            language=filters.language,
        )
        trace.lexical_ms = (time.perf_counter() - start) * 1000
        trace.lexical_count = len(matches)
        return [
            ScoredCandidate(chunk_id=match.chunk_id, rank=index + 1, score=match.score)
            for index, match in enumerate(matches)
        ]

    async def _dense(
        self,
        workspace_id: uuid.UUID,
        query_text: str,
        filters: RetrievalFilters,
        trace: RetrievalTrace,
    ) -> list[ScoredCandidate]:
        repo = ChunkEmbeddingRepository(self._session, workspace_id)
        vector = self._embeddings.embed_query(query_text)
        start = time.perf_counter()
        matches: Sequence[VectorMatch] = await repo.search(
            vector,
            limit=self._config.candidate_limit,
            document_id=filters.document_id,
            language=filters.language,
        )
        trace.dense_ms = (time.perf_counter() - start) * 1000
        trace.dense_count = len(matches)
        return [
            ScoredCandidate(chunk_id=match.chunk_id, rank=index + 1, score=match.similarity)
            for index, match in enumerate(matches)
        ]

    async def _hydrate(
        self,
        workspace_id: uuid.UUID,
        fused: Sequence[FusedCandidate],
    ) -> list[RetrievedChunk]:
        """Load full chunk provenance for fused ids, preserving fused order.

        The load is workspace-scoped again as defense in depth: even if a
        fused id somehow referenced another tenant, it could not be hydrated.
        """
        if not fused:
            return []

        chunk_ids = [item.chunk_id for item in fused]
        rows = await self._session.execute(
            select(Chunk, DocumentVersion.document_id)
            .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
            .where(Chunk.workspace_id == workspace_id, Chunk.id.in_(chunk_ids))
        )
        by_id: dict[uuid.UUID, tuple[Chunk, uuid.UUID]] = {
            chunk.id: (chunk, document_id) for chunk, document_id in rows
        }

        results: list[RetrievedChunk] = []
        for item in fused:
            found = by_id.get(item.chunk_id)
            if found is None:
                continue
            chunk, document_id = found
            results.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    document_id=document_id,
                    document_version_id=chunk.document_version_id,
                    content=chunk.content,
                    fused_score=item.score,
                    lexical_rank=item.ranks.get("lexical"),
                    dense_rank=item.ranks.get("dense"),
                    page_number=chunk.page_number,
                    section=chunk.section,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    language=chunk.language,
                    ocr_engine=chunk.ocr_engine,
                    ocr_confidence=chunk.ocr_confidence,
                )
            )
        return results
