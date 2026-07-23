"""Embedding persistence, workspace scoping, vector search, and RLS.

Requires the local PostgreSQL from `make infra-up` (or the CI pgvector
service). Covers the acceptance criteria that need a real database:
persistence with model/version provenance, dimension enforcement, provider
replacement adding rows, cosine search ranking, and tenant isolation.
"""

from __future__ import annotations

import uuid

import pytest
from app.db.models.documents import EMBEDDING_DIMENSIONS
from app.db.repositories.embeddings import ChunkEmbeddingRepository
from app.embeddings import EmbeddingService, LocalHashingEmbeddingProvider
from app.embeddings.types import EmbeddingVector
from sqlalchemy.exc import DataError, IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.factories import (
    make_chunk,
    make_document,
    make_user,
    make_version,
    make_workspace,
)


async def _chunk_in_new_workspace(session: AsyncSession):  # type: ignore[no-untyped-def]
    owner = await make_user(session)
    workspace = await make_workspace(session, owner)
    document = await make_document(session, workspace, owner)
    version = await make_version(session, document)
    chunk = await make_chunk(session, workspace, version)
    return workspace, chunk


def _vector(fill: float = 0.0, *, first: float = 1.0) -> EmbeddingVector:
    values = [fill] * EMBEDDING_DIMENSIONS
    values[0] = first
    return EmbeddingVector(
        values=tuple(values),
        model="bge-m3-local",
        model_version="hashing-v1",
        dimensions=EMBEDDING_DIMENSIONS,
    )


async def test_embedding_persists_with_provenance(db_session: AsyncSession) -> None:
    workspace, chunk = await _chunk_in_new_workspace(db_session)
    repo = ChunkEmbeddingRepository(db_session, workspace.id)

    stored = await repo.upsert(chunk.id, _vector())

    assert stored.model == "bge-m3-local"
    assert stored.model_version == "hashing-v1"
    assert stored.dimensions == EMBEDDING_DIMENSIONS
    assert len(stored.embedding) == EMBEDDING_DIMENSIONS
    assert await repo.count() == 1


async def test_upsert_replaces_same_model_version(db_session: AsyncSession) -> None:
    workspace, chunk = await _chunk_in_new_workspace(db_session)
    repo = ChunkEmbeddingRepository(db_session, workspace.id)

    first = await repo.upsert(chunk.id, _vector(first=1.0))
    second = await repo.upsert(chunk.id, _vector(first=0.5))

    assert first.id == second.id  # same row updated, not duplicated
    assert await repo.count() == 1
    assert second.embedding[0] == pytest.approx(0.5)


async def test_new_model_version_adds_a_row(db_session: AsyncSession) -> None:
    workspace, chunk = await _chunk_in_new_workspace(db_session)
    repo = ChunkEmbeddingRepository(db_session, workspace.id)

    await repo.upsert(chunk.id, _vector())
    upgraded = EmbeddingVector(
        values=_vector().values,
        model="bge-m3-local",
        model_version="hashing-v2",  # a model upgrade
        dimensions=EMBEDDING_DIMENSIONS,
    )
    await repo.upsert(chunk.id, upgraded)

    # Provenance is preserved: the upgrade adds a row rather than overwriting.
    assert await repo.count() == 2


async def test_duplicate_model_version_conflicts_at_db(db_session: AsyncSession) -> None:
    workspace, chunk = await _chunk_in_new_workspace(db_session)
    from app.db.models import ChunkEmbedding

    for _ in range(2):
        db_session.add(
            ChunkEmbedding(
                workspace_id=workspace.id,
                chunk_id=chunk.id,
                model="bge-m3-local",
                model_version="hashing-v1",
                dimensions=EMBEDDING_DIMENSIONS,
                embedding=list(_vector().values),
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_wrong_dimension_vector_is_rejected(db_session: AsyncSession) -> None:
    workspace, chunk = await _chunk_in_new_workspace(db_session)
    from app.db.models import ChunkEmbedding

    db_session.add(
        ChunkEmbedding(
            workspace_id=workspace.id,
            chunk_id=chunk.id,
            model="bge-m3-local",
            model_version="hashing-v1",
            dimensions=8,
            embedding=[0.1] * 8,  # not EMBEDDING_DIMENSIONS
        )
    )
    # pgvector enforces the fixed column width; the mismatch is caught when the
    # statement is bound (StatementError wrapping ValueError) or by the DB.
    with pytest.raises((StatementError, DataError, IntegrityError)):
        await db_session.flush()


async def test_upsert_rejects_foreign_chunk(db_session: AsyncSession) -> None:
    workspace_a, _ = await _chunk_in_new_workspace(db_session)
    _, chunk_b = await _chunk_in_new_workspace(db_session)
    repo = ChunkEmbeddingRepository(db_session, workspace_a.id)

    with pytest.raises(ValueError, match="workspace"):
        await repo.upsert(chunk_b.id, _vector())


async def test_upsert_rejects_missing_chunk(db_session: AsyncSession) -> None:
    workspace, _ = await _chunk_in_new_workspace(db_session)
    repo = ChunkEmbeddingRepository(db_session, workspace.id)

    with pytest.raises(ValueError, match="workspace"):
        await repo.upsert(uuid.uuid4(), _vector())


async def test_cosine_search_ranks_nearest_first(db_session: AsyncSession) -> None:
    owner = await make_user(db_session)
    workspace = await make_workspace(db_session, owner)
    document = await make_document(db_session, workspace, owner)
    version = await make_version(db_session, document)
    repo = ChunkEmbeddingRepository(db_session, workspace.id)
    service = EmbeddingService(LocalHashingEmbeddingProvider())

    near_chunk = await make_chunk(db_session, workspace, version, chunk_index=0)
    far_chunk = await make_chunk(db_session, workspace, version, chunk_index=1)
    await repo.upsert(near_chunk.id, service.embed_query("refund policy for late orders"))
    await repo.upsert(far_chunk.id, service.embed_query("tamil poetry about the sea"))

    query = service.embed_query("what is the refund policy")
    matches = await repo.search(query, limit=2)

    assert [match.chunk_id for match in matches][0] == near_chunk.id
    assert matches[0].similarity >= matches[1].similarity


async def test_search_only_returns_own_workspace(db_session: AsyncSession) -> None:
    workspace_a, chunk_a = await _chunk_in_new_workspace(db_session)
    workspace_b, chunk_b = await _chunk_in_new_workspace(db_session)
    service = EmbeddingService(LocalHashingEmbeddingProvider())
    query = service.embed_query("shared query text")

    repo_a = ChunkEmbeddingRepository(db_session, workspace_a.id)
    repo_b = ChunkEmbeddingRepository(db_session, workspace_b.id)
    await repo_a.upsert(chunk_a.id, query)
    await repo_b.upsert(chunk_b.id, query)

    matches_a = await repo_a.search(query, limit=10)
    returned = {match.chunk_id for match in matches_a}
    assert chunk_a.id in returned
    assert chunk_b.id not in returned  # never leaks the other tenant


async def test_delete_for_chunk_removes_embedding(db_session: AsyncSession) -> None:
    workspace, chunk = await _chunk_in_new_workspace(db_session)
    repo = ChunkEmbeddingRepository(db_session, workspace.id)
    await repo.upsert(chunk.id, _vector())
    assert await repo.count() == 1

    await repo.delete_for_chunk(chunk.id)
    assert await repo.count() == 0
