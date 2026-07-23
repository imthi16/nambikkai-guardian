"""Unit test for the hybrid-evidence retriever adapter (no database).

Confirms the adapter maps a fully-provenanced ``RetrievedChunk`` into the
minimal ``EvidencePassage`` the pipeline needs, preserves order, forwards
workspace/filter scope to the underlying service, and returns the retrieval
trace's non-sensitive metadata.
"""

from __future__ import annotations

import uuid

from app.rag.retriever import HybridEvidenceRetriever
from app.retrieval.types import (
    RetrievalFilters,
    RetrievalResult,
    RetrievalTrace,
    RetrievedChunk,
)

WORKSPACE = uuid.UUID(int=1)


def _chunk(order: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.UUID(int=100 + order),
        document_id=uuid.UUID(int=9),
        document_version_id=uuid.UUID(int=8),
        content=f"evidence {order}",
        fused_score=0.9 - order * 0.1,
        lexical_rank=order + 1,
        dense_rank=None,
        page_number=order + 1,
        section="S",
        char_start=order * 10,
        char_end=order * 10 + 5,
        language="eng",
        ocr_engine="paddle",
        ocr_confidence=0.8,
        rerank_score=0.7,
        rerank_rank=order + 1,
    )


class FakeService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, *, workspace_id, query, filters, top_k):  # type: ignore[no-untyped-def]
        self.calls.append(
            {"workspace_id": workspace_id, "query": query, "filters": filters, "top_k": top_k}
        )
        trace = RetrievalTrace(
            workspace_id=workspace_id,
            detected_language="eng",
            variant_count=1,
            filters=filters.as_metadata(),
            rrf_k=60,
            candidate_limit=50,
            top_k=top_k or 8,
        )
        return RetrievalResult(chunks=[_chunk(0), _chunk(1)], trace=trace)


async def test_adapter_maps_chunks_and_preserves_order() -> None:
    service = FakeService()
    retriever = HybridEvidenceRetriever(service)  # type: ignore[arg-type]

    document_id = uuid.UUID(int=77)
    passages, meta = await retriever.retrieve(
        workspace_id=WORKSPACE,
        query="q",
        top_k=8,
        document_id=document_id,
        language="eng",
    )

    assert [p.order for p in passages] == [0, 1]
    assert passages[0].chunk_id == uuid.UUID(int=100)
    assert passages[0].page_number == 1
    assert passages[0].ocr_engine == "paddle"
    assert passages[0].rerank_score == 0.7
    # Forwarded scope reaches the underlying service intact.
    call = service.calls[0]
    assert call["workspace_id"] == WORKSPACE
    assert call["top_k"] == 8
    assert isinstance(call["filters"], RetrievalFilters)
    assert call["filters"].document_id == document_id
    assert call["filters"].language == "eng"
    # The returned metadata is the non-sensitive retrieval trace.
    assert meta["workspace_id"] == str(WORKSPACE)
    assert "detected_language" in meta
