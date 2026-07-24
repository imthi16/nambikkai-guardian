"""HybridRetrievalService orchestration with fakes: fusion wiring, filters, trace.

These tests avoid a database by faking the workspace-scoped repositories and
the hydration query. They assert the service passes tenant scope and filters
through, fuses both sources, and records a non-sensitive trace. Cross-tenant
isolation itself is proven against a real database in the integration suite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from app.db.models.enums import DocumentStatus
from app.db.repositories.chunks import LexicalMatch
from app.db.repositories.embeddings import VectorMatch
from app.embeddings.types import EmbeddingVector
from app.retrieval import service as service_module
from app.retrieval.fusion import FusedCandidate
from app.retrieval.service import HybridRetrievalService, RetrievalConfig
from app.retrieval.types import RetrievalFilters, RetrievedChunk

WORKSPACE = uuid.UUID(int=1)


def _chunk_id(n: int) -> uuid.UUID:
    return uuid.UUID(int=100 + n)


class FakeEmbeddingService:
    dimensions = 4

    def embed_query(self, text: str) -> EmbeddingVector:
        return EmbeddingVector(
            values=(1.0, 0.0, 0.0, 0.0),
            model="fake",
            model_version="v1",
            dimensions=4,
        )


@dataclass
class FakeChunkRepo:
    session: object
    workspace_id: uuid.UUID
    lexical: list[LexicalMatch]
    seen_filters: dict[str, object]

    async def lexical_search(self, variants, *, limit, document_id, language):  # type: ignore[no-untyped-def]
        self.seen_filters["lexical"] = {
            "workspace_id": self.workspace_id,
            "document_id": document_id,
            "language": language,
            "limit": limit,
        }
        return self.lexical


@dataclass
class FakeEmbeddingRepo:
    session: object
    workspace_id: uuid.UUID
    dense: list[VectorMatch]
    seen_filters: dict[str, object]

    async def search(self, vector, *, limit, document_id, language):  # type: ignore[no-untyped-def]
        self.seen_filters["dense"] = {
            "workspace_id": self.workspace_id,
            "document_id": document_id,
            "language": language,
            "limit": limit,
        }
        return self.dense


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lexical: list[LexicalMatch],
    dense: list[VectorMatch],
    hydrate: dict[uuid.UUID, RetrievedChunk],
) -> dict[str, object]:
    seen: dict[str, object] = {}

    def make_chunk_repo(session: object, workspace_id: uuid.UUID) -> FakeChunkRepo:
        return FakeChunkRepo(session, workspace_id, lexical, seen)

    def make_embedding_repo(session: object, workspace_id: uuid.UUID) -> FakeEmbeddingRepo:
        return FakeEmbeddingRepo(session, workspace_id, dense, seen)

    monkeypatch.setattr(service_module, "ChunkRepository", make_chunk_repo)
    monkeypatch.setattr(service_module, "ChunkEmbeddingRepository", make_embedding_repo)

    async def fake_hydrate(self, workspace_id, fused):  # type: ignore[no-untyped-def]
        seen["hydrate_workspace"] = workspace_id
        return [hydrate[item.chunk_id] for item in fused if item.chunk_id in hydrate]

    monkeypatch.setattr(HybridRetrievalService, "_hydrate", fake_hydrate)
    return seen


def _retrieved(chunk_id: uuid.UUID, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=uuid.UUID(int=9),
        document_version_id=uuid.UUID(int=8),
        content="evidence",
        fused_score=score,
        lexical_rank=None,
        dense_rank=None,
        page_number=1,
        section=None,
        char_start=0,
        char_end=8,
        language="eng",
        ocr_engine=None,
        ocr_confidence=None,
    )


async def test_hydrate_query_requires_ready_document() -> None:
    session = AsyncMock()
    session.execute.return_value = []
    service = HybridRetrievalService(
        session=session,
        embedding_service=FakeEmbeddingService(),  # type: ignore[arg-type]
        config=RetrievalConfig(rerank_enabled=False),
    )

    await service._hydrate(
        WORKSPACE,
        [FusedCandidate(chunk_id=_chunk_id(1), score=1.0, ranks={"lexical": 1})],
    )

    statement = session.execute.await_args.args[0]
    compiled = statement.compile()
    assert "JOIN documents" in str(compiled)
    assert DocumentStatus.READY in compiled.params.values()


async def test_service_fuses_both_sources_and_orders_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lexical = [
        LexicalMatch(chunk_id=_chunk_id(1), score=0.9),
        LexicalMatch(chunk_id=_chunk_id(2), score=0.5),
    ]
    dense = [
        VectorMatch(chunk_id=_chunk_id(2), similarity=0.95),
        VectorMatch(chunk_id=_chunk_id(3), similarity=0.80),
    ]
    hydrate = {
        _chunk_id(1): _retrieved(_chunk_id(1), 0.0),
        _chunk_id(2): _retrieved(_chunk_id(2), 0.0),
        _chunk_id(3): _retrieved(_chunk_id(3), 0.0),
    }
    _install_fakes(monkeypatch, lexical=lexical, dense=dense, hydrate=hydrate)

    service = HybridRetrievalService(
        session=object(),  # type: ignore[arg-type]
        embedding_service=FakeEmbeddingService(),  # type: ignore[arg-type]
        config=RetrievalConfig(rrf_k=60, candidate_limit=50, top_k=10, rerank_enabled=False),
    )
    result = await service.search(workspace_id=WORKSPACE, query="evidence please")

    # Chunk 2 appears in both lists, so it must rank first after fusion.
    assert result.chunks[0].chunk_id == _chunk_id(2)
    assert {chunk.chunk_id for chunk in result.chunks} == {
        _chunk_id(1),
        _chunk_id(2),
        _chunk_id(3),
    }
    assert result.trace.lexical_count == 2
    assert result.trace.dense_count == 2
    assert result.trace.fused_count == 3
    assert result.trace.returned_count == 3


async def test_service_passes_workspace_and_filters_to_both_retrievers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _install_fakes(monkeypatch, lexical=[], dense=[], hydrate={})
    document_id = uuid.UUID(int=77)

    service = HybridRetrievalService(
        session=object(),  # type: ignore[arg-type]
        embedding_service=FakeEmbeddingService(),  # type: ignore[arg-type]
    )
    await service.search(
        workspace_id=WORKSPACE,
        query="கோப்பு",
        filters=RetrievalFilters(document_id=document_id, language="tam"),
    )

    for source in ("lexical", "dense"):
        assert seen[source]["workspace_id"] == WORKSPACE  # type: ignore[index]
        assert seen[source]["document_id"] == document_id  # type: ignore[index]
        assert seen[source]["language"] == "tam"  # type: ignore[index]
    assert seen["hydrate_workspace"] == WORKSPACE


async def test_trace_metadata_carries_no_query_text_or_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fakes(monkeypatch, lexical=[], dense=[], hydrate={})
    service = HybridRetrievalService(
        session=object(),  # type: ignore[arg-type]
        embedding_service=FakeEmbeddingService(),  # type: ignore[arg-type]
    )
    result = await service.search(workspace_id=WORKSPACE, query="secret sensitive query")

    metadata = result.trace.as_metadata()
    serialized = str(metadata)
    assert "secret sensitive query" not in serialized
    assert "detected_language" in metadata
    assert metadata["returned_count"] == 0


class ReverseReranker:
    """Scores by reverse chunk-id so we can prove reranking changed the order."""

    model = "reverse-reranker"
    model_version = "v1"

    def score(self, query, items):  # type: ignore[no-untyped-def]
        from app.reranking.types import RerankScore

        # Later items get higher scores, so the incoming order is reversed.
        return [
            RerankScore(chunk_id=item.chunk_id, score=float(index + 1))
            for index, item in enumerate(items)
        ]


async def test_reranking_reorders_fused_results_and_records_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.reranking.service import RerankService

    lexical = [
        LexicalMatch(chunk_id=_chunk_id(1), score=0.9),
        LexicalMatch(chunk_id=_chunk_id(2), score=0.8),
        LexicalMatch(chunk_id=_chunk_id(3), score=0.7),
    ]
    hydrate = {
        _chunk_id(1): _retrieved(_chunk_id(1), 0.0),
        _chunk_id(2): _retrieved(_chunk_id(2), 0.0),
        _chunk_id(3): _retrieved(_chunk_id(3), 0.0),
    }
    _install_fakes(monkeypatch, lexical=lexical, dense=[], hydrate=hydrate)

    service = HybridRetrievalService(
        session=object(),  # type: ignore[arg-type]
        embedding_service=FakeEmbeddingService(),  # type: ignore[arg-type]
        rerank_service=RerankService(ReverseReranker()),
        config=RetrievalConfig(rrf_k=60, candidate_limit=50, top_k=10),
    )
    result = await service.search(workspace_id=WORKSPACE, query="q")

    # Fusion order is 1,2,3; the reverse reranker must flip it to 3,2,1.
    assert [chunk.chunk_id for chunk in result.chunks] == [
        _chunk_id(3),
        _chunk_id(2),
        _chunk_id(1),
    ]
    assert result.chunks[0].rerank_rank == 1
    assert result.chunks[0].rerank_score is not None
    assert result.trace.reranked is True
    assert result.trace.rerank_model == "reverse-reranker"
    assert result.trace.as_metadata()["reranked"] is True


async def test_reranking_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    lexical = [
        LexicalMatch(chunk_id=_chunk_id(1), score=0.9),
        LexicalMatch(chunk_id=_chunk_id(2), score=0.8),
    ]
    hydrate = {
        _chunk_id(1): _retrieved(_chunk_id(1), 0.0),
        _chunk_id(2): _retrieved(_chunk_id(2), 0.0),
    }
    _install_fakes(monkeypatch, lexical=lexical, dense=[], hydrate=hydrate)

    service = HybridRetrievalService(
        session=object(),  # type: ignore[arg-type]
        embedding_service=FakeEmbeddingService(),  # type: ignore[arg-type]
        config=RetrievalConfig(rerank_enabled=False),
    )
    result = await service.search(workspace_id=WORKSPACE, query="q")

    assert result.trace.reranked is False
    assert all(chunk.rerank_rank is None for chunk in result.chunks)
    assert [chunk.chunk_id for chunk in result.chunks] == [_chunk_id(1), _chunk_id(2)]
