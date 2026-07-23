"""Workspace-scoped repository for documents."""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from app.db.models.documents import Document, DocumentVersion
from app.db.models.enums import DocumentStatus
from app.db.repositories.base import Repository, WorkspaceScopedRepository


class DocumentRepository(WorkspaceScopedRepository[Document]):
    model = Document

    async def count(self) -> int:
        """Number of documents in this workspace (for upload quota checks)."""
        statement = (
            select(func.count())
            .select_from(Document)
            .where(Document.workspace_id == self.workspace_id)
        )
        return await self._session.scalar(statement) or 0

    async def total_size_bytes(self) -> int:
        """Sum of stored document sizes in this workspace, in bytes."""
        statement = select(func.coalesce(func.sum(Document.size_bytes), 0)).where(
            Document.workspace_id == self.workspace_id
        )
        return await self._session.scalar(statement) or 0

    async def list_by_status(self, status: DocumentStatus) -> Sequence[Document]:
        statement = select(Document).where(
            Document.workspace_id == self.workspace_id,
            Document.status == status,
        )
        result = await self._session.scalars(statement)
        return result.all()

    async def get_by_sha256(self, sha256: str) -> Document | None:
        statement = select(Document).where(
            Document.workspace_id == self.workspace_id,
            Document.sha256 == sha256,
        )
        result = await self._session.scalars(statement)
        return result.first()

    async def list_ordered(self) -> Sequence[Document]:
        statement = (
            select(Document)
            .where(Document.workspace_id == self.workspace_id)
            .order_by(Document.created_at.desc())
        )
        result = await self._session.scalars(statement)
        return result.all()


class DocumentVersionRepository(Repository[DocumentVersion]):
    """Versions are reached through their (workspace-checked) document."""

    model = DocumentVersion

    async def get_latest_for_document(self, document_id: uuid.UUID) -> DocumentVersion | None:
        statement = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
        result = await self._session.scalars(statement)
        return result.first()
