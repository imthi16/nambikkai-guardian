"""Request and response bodies for document endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models.enums import DocumentStatus, IngestionStage, IngestionStatus


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    source_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: DocumentStatus
    created_at: datetime


class DownloadLinkResponse(BaseModel):
    url: str
    expires_in_seconds: int


class DocumentProgressResponse(BaseModel):
    """Lifecycle progress: document status plus the latest ingestion run."""

    document_id: uuid.UUID
    status: DocumentStatus
    job_status: IngestionStatus | None
    stage: IngestionStage | None
    attempts: int
    error: str | None
    updated_at: datetime
