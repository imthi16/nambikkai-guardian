"""Document endpoints under `/api/v1/workspaces/{workspace_id}/documents`.

Uploads require the `UPLOAD_DOCUMENTS` capability; reads require `VIEW`.
Every route runs inside the workspace context, so membership is proven and
row-level security is bound before any tenant data moves. Downloads are
time-limited presigned URLs — the bucket is never publicly readable.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, UploadFile, status

from app.auth import errors
from app.auth.dependencies import SessionDep, get_app_settings
from app.auth.permissions import WorkspaceAction
from app.auth.workspace import RequireAction, WorkspaceContext
from app.config import Settings
from app.db.repositories.audit import AuditLogRepository
from app.db.repositories.documents import DocumentRepository, DocumentVersionRepository
from app.db.repositories.ingestion import IngestionJobRepository
from app.documents.service import (
    DuplicateDocumentError,
    FileTooLargeError,
    UploadRejectedError,
    store_new_document,
)
from app.ingestion.queue import JobQueue
from app.schemas.documents import (
    DocumentProgressResponse,
    DocumentResponse,
    DownloadLinkResponse,
)
from app.storage.base import ObjectStorage

router = APIRouter(prefix="/workspaces/{workspace_id}/documents", tags=["documents"])

ViewerContext = Annotated[WorkspaceContext, Depends(RequireAction(WorkspaceAction.VIEW))]
UploaderContext = Annotated[
    WorkspaceContext,
    Depends(RequireAction(WorkspaceAction.UPLOAD_DOCUMENTS)),
]


def get_object_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.object_storage
    return storage


def get_job_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


StorageDep = Annotated[ObjectStorage, Depends(get_object_storage)]
QueueDep = Annotated[JobQueue, Depends(get_job_queue)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile,
    context: UploaderContext,
    session: SessionDep,
    storage: StorageDep,
    queue: QueueDep,
    settings: SettingsDep,
    title: str | None = None,
) -> DocumentResponse:
    # Read at most one byte over the cap: enough to detect oversize without
    # trusting Content-Length or buffering an arbitrarily large body.
    data = await file.read(settings.max_upload_bytes + 1)
    try:
        document = await store_new_document(
            session=session,
            storage=storage,
            queue=queue,
            workspace_id=context.workspace.id,
            uploader_id=context.user.id,
            raw_filename=file.filename,
            declared_mime=file.content_type,
            data=data,
            title=title,
            max_upload_bytes=settings.max_upload_bytes,
        )
    except FileTooLargeError:
        raise errors.file_too_large(settings.max_upload_bytes) from None
    except UploadRejectedError as rejection:
        raise errors.upload_rejected(rejection.code, rejection.message) from None
    except DuplicateDocumentError:
        raise errors.duplicate_document() from None
    return DocumentResponse.model_validate(document)


@router.get("", response_model=list[DocumentResponse])
async def list_documents(context: ViewerContext, session: SessionDep) -> list[DocumentResponse]:
    documents = await DocumentRepository(session, context.workspace.id).list_ordered()
    return [DocumentResponse.model_validate(document) for document in documents]


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    context: ViewerContext,
    session: SessionDep,
) -> DocumentResponse:
    document = await DocumentRepository(session, context.workspace.id).get(document_id)
    if document is None:
        raise errors.document_not_found()
    return DocumentResponse.model_validate(document)


@router.get("/{document_id}/status", response_model=DocumentProgressResponse)
async def document_progress(
    document_id: uuid.UUID,
    context: ViewerContext,
    session: SessionDep,
) -> DocumentProgressResponse:
    document = await DocumentRepository(session, context.workspace.id).get(document_id)
    if document is None:
        raise errors.document_not_found()
    job = await IngestionJobRepository(session, context.workspace.id).get_latest_for_document(
        document.id
    )
    return DocumentProgressResponse(
        document_id=document.id,
        status=document.status,
        job_status=job.status if job else None,
        stage=job.stage if job else None,
        attempts=job.attempts if job else 0,
        error=job.error if job else None,
        updated_at=job.updated_at if job else document.updated_at,
    )


@router.get("/{document_id}/download", response_model=DownloadLinkResponse)
async def download_document(
    document_id: uuid.UUID,
    context: ViewerContext,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> DownloadLinkResponse:
    document = await DocumentRepository(session, context.workspace.id).get(document_id)
    if document is None:
        raise errors.document_not_found()
    latest = await DocumentVersionRepository(session).get_latest_for_document(document.id)
    if latest is None:
        raise errors.document_not_found()
    url = await storage.presigned_get_url(
        latest.storage_key,
        expires_in_seconds=settings.download_url_ttl_seconds,
    )
    await AuditLogRepository(session).record(
        action="document.download_link_issued",
        resource_type="document",
        resource_id=document.id,
        workspace_id=context.workspace.id,
        actor_user_id=context.user.id,
        detail={"version_number": latest.version_number},
    )
    return DownloadLinkResponse(url=url, expires_in_seconds=settings.download_url_ttl_seconds)
