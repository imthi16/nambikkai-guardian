"""Documents, immutable content versions, pages, and retrieval chunks.

Provenance fields (page, section, offsets, language, OCR engine, confidence)
are first-class columns because citation and verification depend on them.
`chunks.workspace_id` is denormalized so retrieval can filter by tenant
without joins; it must always equal the owning document's workspace.
"""

import uuid
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, WorkspaceOwnedModel
from app.db.models.enums import DocumentStatus, pg_enum

# BGE-M3 embedding width; the embeddings table is fixed to this dimension.
EMBEDDING_DIMENSIONS = 1024

if TYPE_CHECKING:
    from app.db.models.identity import Workspace


class Document(WorkspaceOwnedModel):
    """An uploaded source file; content lives in immutable versions."""

    __tablename__ = "documents"

    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    title: Mapped[str] = mapped_column(String(500))
    source_filename: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[DocumentStatus] = mapped_column(
        pg_enum(DocumentStatus, "document_status"),
        default=DocumentStatus.PENDING,
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentVersion.version_number",
    )


class DocumentVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable stored rendition of a document's content."""

    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_number"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(35))

    document: Mapped[Document] = relationship(back_populates="versions")
    pages: Mapped[list["Page"]] = relationship(
        back_populates="document_version",
        cascade="all, delete-orphan",
        order_by="Page.page_number",
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document_version",
        cascade="all, delete-orphan",
        order_by="Chunk.chunk_index",
    )


class Page(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Extracted text and OCR metadata for one page of a document version."""

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("document_version_id", "page_number"),
        CheckConstraint(
            "ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ocr_confidence_range",
        ),
    )

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        index=True,
    )
    page_number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(35))
    ocr_engine: Mapped[str | None] = mapped_column(String(100))
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    image_storage_key: Mapped[str | None] = mapped_column(String(1024))
    ocr_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    document_version: Mapped[DocumentVersion] = relationship(back_populates="pages")


class Chunk(WorkspaceOwnedModel):
    """A retrievable evidence span with complete provenance."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "chunk_index"),
        CheckConstraint("char_end > char_start", name="char_span_positive"),
        CheckConstraint(
            "ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1)",
            name="ocr_confidence_range",
        ),
    )

    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    token_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(500))
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(35))
    ocr_engine: Mapped[str | None] = mapped_column(String(100))
    ocr_confidence: Mapped[float | None] = mapped_column(Float)

    document_version: Mapped[DocumentVersion] = relationship(back_populates="chunks")
    embedding: Mapped["ChunkEmbedding | None"] = relationship(
        back_populates="chunk",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ChunkEmbedding(WorkspaceOwnedModel):
    """The dense vector for one chunk, with model provenance.

    One embedding per chunk per model (unique on `chunk_id, model,
    model_version`), so a model upgrade adds rows rather than silently
    overwriting reproducible provenance. `workspace_id` is denormalized from
    the chunk so vector search can filter by tenant before scoring.
    """

    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id",
            "model",
            "model_version",
            name="uq_chunk_embeddings_chunk_id_model_model_version",
        ),
        # The IVFFlat ANN index (ix_chunk_embeddings_embedding_cosine) is
        # created in migration 0007 and deliberately excluded from Alembic
        # autogenerate (see infra/migrations/env.py): pgvector operator-class
        # indexes do not round-trip cleanly through reflection.
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"),
        index=True,
    )
    model: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(100))
    dimensions: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    chunk: Mapped[Chunk] = relationship(back_populates="embedding")
