"""Adapter binding the hybrid retrieval service to the RAG evidence port.

The graph depends only on the small :class:`EvidenceRetriever` protocol; this
adapter is the one place that knows about :class:`HybridRetrievalService`. It
maps a fully-provenanced :class:`RetrievedChunk` into the minimal
:class:`EvidencePassage` the pipeline needs, and returns the retrieval trace's
non-sensitive metadata for the RAG trace.

Authorization is not re-implemented here: the underlying service reads only
through workspace-scoped repositories, so every passage this adapter yields is
already authorized for the tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.rag.types import EvidencePassage
from app.retrieval.service import HybridRetrievalService
from app.retrieval.types import RetrievalFilters, RetrievedChunk


class HybridEvidenceRetriever:
    """Implements :class:`EvidenceRetriever` over the hybrid retrieval service."""

    def __init__(self, service: HybridRetrievalService) -> None:
        self._service = service

    async def retrieve(
        self,
        *,
        workspace_id: uuid.UUID,
        query: str,
        top_k: int,
        document_id: uuid.UUID | None,
        language: str | None,
    ) -> tuple[Sequence[EvidencePassage], dict[str, object]]:
        result = await self._service.search(
            workspace_id=workspace_id,
            query=query,
            filters=RetrievalFilters(document_id=document_id, language=language),
            top_k=top_k,
        )
        passages = tuple(_to_passage(chunk, order) for order, chunk in enumerate(result.chunks))
        return passages, result.trace.as_metadata()


def _to_passage(chunk: RetrievedChunk, order: int) -> EvidencePassage:
    return EvidencePassage(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        document_version_id=chunk.document_version_id,
        content=chunk.content,
        page_number=chunk.page_number,
        section=chunk.section,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        language=chunk.language,
        ocr_engine=chunk.ocr_engine,
        ocr_confidence=chunk.ocr_confidence,
        fused_score=chunk.fused_score,
        rerank_score=chunk.rerank_score,
        rerank_raw_score=chunk.rerank_raw_score,
        order=order,
    )
