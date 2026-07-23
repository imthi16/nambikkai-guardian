"""Workspace-scoped persistence and vector search for chunk embeddings.

Every query filters on `workspace_id` (the authorization boundary required by
the project rules), so unauthorized embeddings never leave the data layer even
before row-level security applies underneath. Similarity search uses pgvector
cosine distance and returns chunk ids with scores, ready for fusion in the
retrieval stage.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select

from app.db.models.documents import Chunk, ChunkEmbedding
from app.db.repositories.base import WorkspaceScopedRepository
from app.embeddings.types import EmbeddingVector


@dataclass(frozen=True)
class VectorMatch:
    """One nearest-neighbor result: the chunk id and its cosine similarity."""

    chunk_id: uuid.UUID
    similarity: float


class ChunkEmbeddingRepository(WorkspaceScopedRepository[ChunkEmbedding]):
    model = ChunkEmbedding

    async def upsert(
        self,
        chunk_id: uuid.UUID,
        vector: EmbeddingVector,
    ) -> ChunkEmbedding:
        """Persist (or replace) the embedding for one chunk and model version.

        The chunk is looked up through the workspace filter first, so an
        embedding can never be attached to another tenant's chunk.
        """
        chunk = await self._session.get(Chunk, chunk_id)
        if chunk is None or chunk.workspace_id != self.workspace_id:
            msg = "chunk not found in this workspace"
            raise ValueError(msg)

        existing = await self._get_for_model(chunk_id, vector.model, vector.model_version)
        if existing is not None:
            existing.embedding = list(vector.values)
            existing.dimensions = vector.dimensions
            await self._session.flush()
            return existing

        embedding = ChunkEmbedding(
            workspace_id=self.workspace_id,
            chunk_id=chunk_id,
            model=vector.model,
            model_version=vector.model_version,
            dimensions=vector.dimensions,
            embedding=list(vector.values),
        )
        return await self.add(embedding)

    async def _get_for_model(
        self,
        chunk_id: uuid.UUID,
        model: str,
        model_version: str,
    ) -> ChunkEmbedding | None:
        statement = select(ChunkEmbedding).where(
            ChunkEmbedding.workspace_id == self.workspace_id,
            ChunkEmbedding.chunk_id == chunk_id,
            ChunkEmbedding.model == model,
            ChunkEmbedding.model_version == model_version,
        )
        result = await self._session.scalars(statement)
        return result.first()

    async def count(self) -> int:
        statement = select(ChunkEmbedding).where(
            ChunkEmbedding.workspace_id == self.workspace_id,
        )
        result = await self._session.scalars(statement)
        return len(result.all())

    async def delete_for_chunk(self, chunk_id: uuid.UUID) -> None:
        statement = delete(ChunkEmbedding).where(
            ChunkEmbedding.workspace_id == self.workspace_id,
            ChunkEmbedding.chunk_id == chunk_id,
        )
        await self._session.execute(statement)
        await self._session.flush()

    async def search(
        self,
        query: EmbeddingVector,
        *,
        limit: int = 10,
    ) -> Sequence[VectorMatch]:
        """Return the closest chunks in this workspace by cosine similarity.

        Similarity is `1 - cosine_distance`, so higher is nearer. Only the
        query's own model/version is compared, keeping the vector space
        consistent.
        """
        distance = ChunkEmbedding.embedding.cosine_distance(list(query.values))
        statement = (
            select(ChunkEmbedding.chunk_id, distance.label("distance"))
            .where(
                ChunkEmbedding.workspace_id == self.workspace_id,
                ChunkEmbedding.model == query.model,
                ChunkEmbedding.model_version == query.model_version,
            )
            .order_by(distance)
            .limit(limit)
        )
        rows = await self._session.execute(statement)
        return [
            VectorMatch(chunk_id=row.chunk_id, similarity=1.0 - float(row.distance)) for row in rows
        ]
