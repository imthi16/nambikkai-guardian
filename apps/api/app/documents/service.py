"""The upload workflow: validate, hash, deduplicate, store, record, audit.

The file passes every validation rule before a single byte reaches object
storage, and the database rows and audit event commit in the same
transaction as the rest of the request. Storage keys are server-generated —
user-controlled names never appear in object keys.
"""

import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.documents import Document, DocumentVersion
from app.db.models.operations import IngestionJob
from app.db.repositories.audit import AuditLogRepository
from app.db.repositories.documents import DocumentRepository, DocumentVersionRepository
from app.documents.validation import UploadRejectedError, validate_upload
from app.ingestion.queue import JobMessage, JobQueue
from app.storage.base import ObjectStorage


class DuplicateDocumentError(Exception):
    """The same content already exists in this workspace."""

    def __init__(self, existing_document_id: uuid.UUID) -> None:
        super().__init__("duplicate document")
        self.existing_document_id = existing_document_id


class FileTooLargeError(Exception):
    """The upload exceeds the configured size cap."""


def enforce_size_cap(data: bytes, max_upload_bytes: int) -> None:
    if len(data) > max_upload_bytes:
        raise FileTooLargeError


async def store_new_document(
    *,
    session: AsyncSession,
    storage: ObjectStorage,
    queue: JobQueue,
    workspace_id: uuid.UUID,
    uploader_id: uuid.UUID,
    raw_filename: str | None,
    declared_mime: str | None,
    data: bytes,
    title: str | None,
    max_upload_bytes: int,
) -> Document:
    """Run the full secure-upload workflow and return the new document."""
    enforce_size_cap(data, max_upload_bytes)
    validated = validate_upload(raw_filename, declared_mime, data)
    sha256 = hashlib.sha256(data).hexdigest()

    documents = DocumentRepository(session, workspace_id)
    existing = await documents.get_by_sha256(sha256)
    if existing is not None:
        raise DuplicateDocumentError(existing.id)

    document = await documents.add(
        Document(
            workspace_id=workspace_id,
            created_by=uploader_id,
            title=title or validated.filename,
            source_filename=validated.filename,
            mime_type=validated.canonical_mime,
            size_bytes=len(data),
            sha256=sha256,
        )
    )
    storage_key = f"workspaces/{workspace_id}/documents/{document.id}/v1-{sha256[:16]}"
    await DocumentVersionRepository(session).add(
        DocumentVersion(
            document_id=document.id,
            version_number=1,
            storage_key=storage_key,
            sha256=sha256,
            size_bytes=len(data),
        )
    )
    # Storage write happens after validation and dedup so rejected uploads
    # never leave bytes behind; if the transaction later rolls back, the
    # orphaned object is unreferenced and harmless.
    await storage.put_object(storage_key, data, validated.canonical_mime)
    await AuditLogRepository(session).record(
        action="document.uploaded",
        resource_type="document",
        resource_id=document.id,
        workspace_id=workspace_id,
        actor_user_id=uploader_id,
        detail={
            "filename": validated.filename,
            "sha256": sha256,
            "size_bytes": len(data),
            "mime_type": validated.canonical_mime,
        },
    )
    job = IngestionJob(workspace_id=workspace_id, document_id=document.id)
    session.add(job)
    await session.flush()
    # A worker may dequeue before this transaction commits; it will find no
    # row, drop the message, and `requeue_stale` re-enqueues the job later.
    # Duplicate delivery is safe because claiming is a compare-and-set.
    await queue.enqueue(JobMessage(job_id=job.id, workspace_id=workspace_id))
    return document


__all__ = [
    "DuplicateDocumentError",
    "FileTooLargeError",
    "UploadRejectedError",
    "store_new_document",
]
