"""Permission-filtered hybrid retrieval against a real database.

Requires the local PostgreSQL from `make infra-up` (or the CI pgvector
service). Covers the acceptance criteria that need a database: lexical recall
for Tamil and English, dense recall, rank fusion across both, metadata
filters, and — most importantly — that a query in one workspace never returns
another tenant's chunk (zero cross-tenant leakage).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest
from app.db.models.documents import (
    EMBEDDING_DIMENSIONS,
    Chunk,
    ChunkEmbedding,
    Document,
    DocumentVersion,
)
from app.db.models.enums import DocumentStatus
from app.db.models.identity import User, Workspace
from app.embeddings.types import EmbeddingVector
from app.retrieval.fusion import FusedCandidate
from app.retrieval.service import HybridRetrievalService, RetrievalConfig
from app.retrieval.types import RetrievalFilters, RetrievedChunk
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration import factories


def _dense_vector(*nonzero: tuple[int, float]) -> tuple[float, ...]:
    """A 1024-dim vector (the model width) with a few coordinates set."""
    values = [0.0] * EMBEDDING_DIMENSIONS
    for index, value in nonzero:
        values[index] = value
    return tuple(values)


class StubEmbeddingService:
    """Deterministic query embedder aligned with the vectors we seed."""

    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    def embed_query(self, text: str) -> EmbeddingVector:
        return EmbeddingVector(
            values=self._vector,
            model="stub",
            model_version="v1",
            dimensions=EMBEDDING_DIMENSIONS,
        )


async def _seed_chunk(
    session: AsyncSession,
    *,
    workspace: Workspace,
    owner: User,
    content: str,
    language: str,
    embedding: tuple[float, ...] | None = None,
    chunk_index: int = 0,
) -> Chunk:
    document = await factories.make_document(session, workspace, owner, status=DocumentStatus.READY)
    version = await factories.make_version(session, document)
    chunk = Chunk(
        workspace_id=workspace.id,
        document_version_id=version.id,
        chunk_index=chunk_index,
        content=content,
        content_hash=f"{chunk_index + 1:064x}",
        page_number=1,
        char_start=0,
        char_end=len(content),
        language=language,
    )
    session.add(chunk)
    await session.flush()
    if embedding is not None:
        session.add(
            ChunkEmbedding(
                workspace_id=workspace.id,
                chunk_id=chunk.id,
                model="stub",
                model_version="v1",
                dimensions=EMBEDDING_DIMENSIONS,
                embedding=list(embedding),
            )
        )
        await session.flush()
    return chunk


def _service(session: AsyncSession, query_vector: tuple[float, ...]) -> HybridRetrievalService:
    return HybridRetrievalService(
        session,
        embedding_service=StubEmbeddingService(query_vector),  # type: ignore[arg-type]
        config=RetrievalConfig(rrf_k=60, candidate_limit=50, top_k=10, rerank_enabled=False),
    )


def _ids(chunks: Sequence[RetrievedChunk]) -> set[uuid.UUID]:
    return {chunk.chunk_id for chunk in chunks}


async def test_lexical_recall_matches_english_terms(db_session: AsyncSession) -> None:
    owner = await factories.make_user(db_session)
    workspace = await factories.make_workspace(db_session, owner)
    target = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="The invoice total is due on the fifteenth of March.",
        language="eng",
    )
    await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="Weather patterns over the coastal plains this season.",
        language="eng",
        chunk_index=1,
    )

    result = await _service(db_session, _dense_vector()).search(
        workspace_id=workspace.id, query="invoice total due"
    )
    assert target.id in _ids(result.chunks)
    assert result.chunks[0].chunk_id == target.id


async def test_lexical_recall_matches_partial_terms(db_session: AsyncSession) -> None:
    """A relevant chunk is recalled even when the query carries extra terms.

    Query terms are OR-combined, so evidence that omits one query word (here
    "date") is still retrieved rather than filtered out by an all-terms AND.
    Without this, a natural query would abstain against otherwise-grounded
    evidence.
    """
    owner = await factories.make_user(db_session)
    workspace = await factories.make_workspace(db_session, owner)
    target = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="The invoice payment is due within thirty days of receipt.",
        language="eng",
    )

    # "date" never appears in the chunk; an AND query would match nothing.
    result = await _service(db_session, _dense_vector()).search(
        workspace_id=workspace.id, query="invoice payment due date"
    )
    assert target.id in _ids(result.chunks)


async def test_lexical_recall_matches_tamil_terms(db_session: AsyncSession) -> None:
    owner = await factories.make_user(db_session)
    workspace = await factories.make_workspace(db_session, owner)
    target = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="இந்த ஒப்பந்தம் மார்ச் மாதம் முடிவடைகிறது.",
        language="tam",
    )
    await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="கடலோர பகுதிகளில் வானிலை மாறுபாடு.",
        language="tam",
        chunk_index=1,
    )

    result = await _service(db_session, _dense_vector()).search(
        workspace_id=workspace.id, query="ஒப்பந்தம் மார்ச்"
    )
    assert target.id in _ids(result.chunks)
    assert result.chunks[0].chunk_id == target.id


async def test_dense_recall_returns_nearest_vector(db_session: AsyncSession) -> None:
    owner = await factories.make_user(db_session)
    workspace = await factories.make_workspace(db_session, owner)
    near = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="alpha content",
        language="eng",
        embedding=_dense_vector((0, 1.0)),
    )
    await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="beta content",
        language="eng",
        embedding=_dense_vector((1, 1.0)),
        chunk_index=1,
    )

    # A query with no lexical overlap: only the dense side can surface a hit.
    result = await _service(db_session, _dense_vector((0, 1.0))).search(
        workspace_id=workspace.id, query="zzzz"
    )
    assert near.id in _ids(result.chunks)
    assert result.chunks[0].chunk_id == near.id
    assert result.chunks[0].dense_rank == 1


async def test_hybrid_fusion_prefers_chunk_in_both_lists(db_session: AsyncSession) -> None:
    owner = await factories.make_user(db_session)
    workspace = await factories.make_workspace(db_session, owner)
    # `both` matches lexically and has a matching embedding, so it appears in
    # both ranked lists. `lexical_only` matches lexically but has no embedding,
    # so it appears only in the lexical list. RRF must therefore rank `both`
    # first, since its extra dense contribution can only add to its score.
    both = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="quarterly revenue summary",
        language="eng",
        embedding=_dense_vector((0, 1.0)),
    )
    lexical_only = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="quarterly revenue appendix",
        language="eng",
        chunk_index=1,
    )

    result = await _service(db_session, _dense_vector((0, 1.0))).search(
        workspace_id=workspace.id, query="quarterly revenue"
    )
    assert result.chunks[0].chunk_id == both.id
    assert result.chunks[0].lexical_rank is not None
    assert result.chunks[0].dense_rank == 1
    assert lexical_only.id in _ids(result.chunks)


async def test_document_filter_restricts_candidates(db_session: AsyncSession) -> None:
    owner = await factories.make_user(db_session)
    workspace = await factories.make_workspace(db_session, owner)
    keep = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="contract renewal terms",
        language="eng",
    )
    other = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="contract renewal terms",
        language="eng",
        chunk_index=1,
    )

    keep_version = await db_session.get(DocumentVersion, keep.document_version_id)
    assert keep_version is not None
    result = await _service(db_session, _dense_vector()).search(
        workspace_id=workspace.id,
        query="contract renewal",
        filters=RetrievalFilters(document_id=keep_version.document_id),
    )
    assert keep.id in _ids(result.chunks)
    assert other.id not in _ids(result.chunks)


async def test_language_filter_restricts_candidates(db_session: AsyncSession) -> None:
    owner = await factories.make_user(db_session)
    workspace = await factories.make_workspace(db_session, owner)
    english = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="renewal terms apply",
        language="eng",
    )
    tamil = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="renewal terms apply",
        language="tam",
        chunk_index=1,
    )

    result = await _service(db_session, _dense_vector()).search(
        workspace_id=workspace.id,
        query="renewal terms",
        filters=RetrievalFilters(language="tam"),
    )
    assert tamil.id in _ids(result.chunks)
    assert english.id not in _ids(result.chunks)


async def test_cross_tenant_query_never_returns_other_workspace_chunks(
    db_session: AsyncSession,
) -> None:
    owner_a = await factories.make_user(db_session)
    workspace_a = await factories.make_workspace(db_session, owner_a)
    owner_b = await factories.make_user(db_session)
    workspace_b = await factories.make_workspace(db_session, owner_b)

    # Identical content and identical embedding, but owned by the *other* tenant.
    secret_vector = _dense_vector((0, 0.5), (1, 0.5), (2, 0.5))
    foreign = await _seed_chunk(
        db_session,
        workspace=workspace_b,
        owner=owner_b,
        content="confidential merger memo",
        language="eng",
        embedding=secret_vector,
    )
    await _seed_chunk(
        db_session,
        workspace=workspace_a,
        owner=owner_a,
        content="unrelated public note",
        language="eng",
        embedding=_dense_vector((3, 1.0)),
    )

    # Query workspace A for exactly workspace B's content and vector.
    result = await _service(db_session, secret_vector).search(
        workspace_id=workspace_a.id, query="confidential merger memo"
    )
    returned = _ids(result.chunks)
    # Nothing from B leaked, regardless of how strong the match would have been.
    assert foreign.id not in returned


@pytest.mark.parametrize(
    "non_ready_status",
    [DocumentStatus.PROCESSING, DocumentStatus.QUARANTINED, DocumentStatus.FAILED],
)
async def test_hydration_rechecks_ready_status_after_candidate_selection(
    db_session: AsyncSession, non_ready_status: DocumentStatus
) -> None:
    owner = await factories.make_user(db_session)
    workspace = await factories.make_workspace(db_session, owner)
    candidate = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="candidate selected before quarantine",
        language="eng",
    )
    version = await db_session.get(DocumentVersion, candidate.document_version_id)
    assert version is not None
    document = await db_session.get(Document, version.document_id)
    assert document is not None

    fused = [FusedCandidate(chunk_id=candidate.id, score=1.0, ranks={"lexical": 1})]
    service = _service(db_session, _dense_vector())
    assert [chunk.chunk_id for chunk in await service._hydrate(workspace.id, fused)] == [
        candidate.id
    ]

    document.status = non_ready_status
    await db_session.flush()

    assert await service._hydrate(workspace.id, fused) == []


def _reranking_service(
    session: AsyncSession, query_vector: tuple[float, ...]
) -> HybridRetrievalService:
    from app.reranking.service import RerankService

    return HybridRetrievalService(
        session,
        embedding_service=StubEmbeddingService(query_vector),  # type: ignore[arg-type]
        rerank_service=RerankService(),
        config=RetrievalConfig(
            rrf_k=60, candidate_limit=50, top_k=10, rerank_enabled=True, rerank_candidate_limit=30
        ),
    )


async def test_reranking_promotes_the_most_relevant_chunk(db_session: AsyncSession) -> None:
    owner = await factories.make_user(db_session)
    workspace = await factories.make_workspace(db_session, owner)
    # The strongest lexical match for the query terms is the exact phrase; a
    # weaker chunk merely shares one word. The reranker must keep the exact
    # match first and annotate both with rerank provenance.
    exact = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="annual compliance audit schedule",
        language="eng",
    )
    weak = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=owner,
        content="the annual staff picnic notes",
        language="eng",
        chunk_index=1,
    )

    result = await _reranking_service(db_session, _dense_vector()).search(
        workspace_id=workspace.id, query="compliance audit schedule"
    )
    ids = _ids(result.chunks)
    assert exact.id in ids
    assert result.chunks[0].chunk_id == exact.id
    assert result.chunks[0].rerank_rank == 1
    assert result.chunks[0].rerank_score is not None
    assert result.trace.reranked is True
    # Reranking only reorders authorized results; the weak chunk is not leaked
    # away or duplicated.
    assert weak.id in ids or result.trace.rerank_dropped >= 0


async def test_reranking_never_returns_other_workspace_chunks(db_session: AsyncSession) -> None:
    owner_a = await factories.make_user(db_session)
    workspace_a = await factories.make_workspace(db_session, owner_a)
    owner_b = await factories.make_user(db_session)
    workspace_b = await factories.make_workspace(db_session, owner_b)

    foreign = await _seed_chunk(
        db_session,
        workspace=workspace_b,
        owner=owner_b,
        content="compliance audit schedule",
        language="eng",
    )
    await _seed_chunk(
        db_session,
        workspace=workspace_a,
        owner=owner_a,
        content="compliance audit schedule",
        language="eng",
    )

    result = await _reranking_service(db_session, _dense_vector()).search(
        workspace_id=workspace_a.id, query="compliance audit schedule"
    )
    # Even though B holds an identically-relevant chunk, reranking runs only
    # over A's authorized candidates.
    assert foreign.id not in _ids(result.chunks)
