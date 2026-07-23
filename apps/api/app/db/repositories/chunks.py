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

from app.citations.types import ChunkProvenance
from app.db.models.documents import Chunk, Document, DocumentVersion
from app.db.models.enums import DocumentStatus
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
        # Only READY documents are candidates: a quarantined or failed document
        # must never contribute evidence, so its chunks are excluded here (the
        # retrieval boundary), not merely hidden in the UI. This is the same
        # gate `get_provenance` enforces, applied at candidate generation.
        ready_versions = (
            select(DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(Document.status == DocumentStatus.READY)
        )
        statement = (
            select(Chunk.id, rank)
            .where(
                Chunk.workspace_id == self.workspace_id,
                document.op("@@")(query),
                Chunk.document_version_id.in_(ready_versions),
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

    async def get_provenance(self, chunk_id: uuid.UUID) -> ChunkProvenance | None:
        """Load one chunk's immutable provenance within this workspace.

        Joins the chunk to its document version and document to carry the title
        and version number a citation needs. The workspace filter is applied on
        the chunk (and row-level security fences it again), so a chunk owned by
        another tenant resolves to ``None`` and is indistinguishable from one
        that does not exist. Only chunks of a ``READY`` document are returned:
        ingestion commits chunks stage by stage, so a later quarantine or
        failure can leave chunk rows behind on a document that must never be
        cited — requiring readiness here keeps that text out of resolution.
        """
        statement = (
            select(
                Chunk,
                DocumentVersion.document_id,
                DocumentVersion.version_number,
                Document.title,
            )
            .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
            .join(Document, DocumentVersion.document_id == Document.id)
            .where(
                Chunk.id == chunk_id,
                Chunk.workspace_id == self.workspace_id,
                Document.status == DocumentStatus.READY,
            )
        )
        row = (await self._session.execute(statement)).first()
        if row is None:
            return None
        chunk, document_id, version_number, title = row
        return ChunkProvenance(
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_index,
            document_id=document_id,
            document_title=title,
            document_version_id=chunk.document_version_id,
            version_number=version_number,
            content=chunk.content,
            page_number=chunk.page_number,
            section=chunk.section,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            language=chunk.language,
            ocr_engine=chunk.ocr_engine,
            ocr_confidence=chunk.ocr_confidence,
        )

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
