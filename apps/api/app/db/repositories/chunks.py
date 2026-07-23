"""Workspace-scoped chunk access and lexical (full-text) retrieval.

Lexical search runs entirely inside the workspace filter (and row-level
security beneath it), so a chunk from another tenant can never appear in the
candidate list. The `simple` text-search configuration matches the GIN
expression index from migration 0008 and treats Tamil, English, and
romanized Tanglish tokens uniformly.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.documents import Chunk, DocumentVersion
from app.db.repositories.base import WorkspaceScopedRepository

# Matches the configuration of ix_chunks_content_fts (migration 0008).
_TS_CONFIG = "simple"


@dataclass(frozen=True)
class LexicalMatch:
    """One full-text hit: the chunk id and its ts_rank_cd relevance score."""

    chunk_id: uuid.UUID
    score: float


class ChunkRepository(WorkspaceScopedRepository[Chunk]):
    model = Chunk

    async def lexical_search(
        self,
        variants: Sequence[str],
        *,
        limit: int = 50,
        document_id: uuid.UUID | None = None,
        language: str | None = None,
    ) -> Sequence[LexicalMatch]:
        """Rank workspace chunks against the query variants by full-text score.

        The variants (normalized, transliterated, expansions) are split into
        their individual terms and OR-combined with ``websearch_to_tsquery`` so
        a Tanglish query can match Tamil-script content *and* a chunk missing a
        single query word is still recalled. This is a deliberately high-recall
        first pass: ``ts_rank_cd`` still ranks chunks covering more terms higher,
        and fusion, reranking, and claim verification recover precision. (An
        all-terms AND query would abstain the moment one query word is absent
        from otherwise-relevant evidence.) Optional `document_id`/`language`
        narrow the candidate set before ranking. Returns at most `limit`
        matches, best first.
        """
        # Split every variant into terms, preserving first-seen order and
        # dropping duplicates, so the OR query has no redundant clauses.
        seen: set[str] = set()
        terms: list[str] = []
        for variant in variants:
            for term in (variant or "").split():
                if term not in seen:
                    seen.add(term)
                    terms.append(term)
        if not terms:
            return []

        document = to_tsvector(Chunk.content)
        # OR the per-term tsqueries so partial matches are recalled; ts_rank_cd
        # rewards chunks matching more of them.
        query = to_tsquery(terms[0])
        for term in terms[1:]:
            query = query.op("||")(to_tsquery(term))

        rank = func.ts_rank_cd(document, query).label("score")
        statement = (
            select(Chunk.id, rank)
            .where(
                Chunk.workspace_id == self.workspace_id,
                document.op("@@")(query),
            )
            .order_by(rank.desc(), Chunk.id)
            .limit(limit)
        )
        if document_id is not None:
            statement = statement.where(
                Chunk.document_version_id.in_(
                    select(DocumentVersion.id).where(DocumentVersion.document_id == document_id)
                )
            )
        if language is not None:
            statement = statement.where(Chunk.language == language)

        rows = await self._session.execute(statement)
        return [LexicalMatch(chunk_id=row.id, score=float(row.score)) for row in rows]

    async def get_many(self, chunk_ids: Sequence[uuid.UUID]) -> Sequence[Chunk]:
        """Load full chunk rows for the given ids within this workspace, order preserved."""
        if not chunk_ids:
            return []
        statement = select(Chunk).where(
            Chunk.workspace_id == self.workspace_id,
            Chunk.id.in_(list(chunk_ids)),
        )
        result = await self._session.scalars(statement)
        by_id = {chunk.id: chunk for chunk in result.all()}
        return [by_id[cid] for cid in chunk_ids if cid in by_id]


def to_tsvector(column: ColumnElement[str] | InstrumentedAttribute[str]) -> ColumnElement[object]:
    """`to_tsvector('simple', column)` mirroring the FTS index expression."""
    return func.to_tsvector(_TS_CONFIG, column)


def to_tsquery(text_value: str) -> ColumnElement[object]:
    """A parsed, user-safe tsquery from free text via websearch_to_tsquery.

    `websearch_to_tsquery` never raises on arbitrary punctuation, so untrusted
    query text cannot cause a parse error or injection. The plain Python string
    is bound as a parameter by SQLAlchemy, never interpolated into SQL.
    """
    return func.websearch_to_tsquery(_TS_CONFIG, text_value)
