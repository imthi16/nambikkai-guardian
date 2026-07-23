"""Citation-resolution endpoint under `/api/v1/workspaces/{workspace_id}/citations`.

Resolving a citation requires the `QUERY` capability — the same capability that
lets a member see answers and their citations. The route runs inside the
workspace context, so membership is proven and row-level security is bound
before any tenant data is read, and the resolver loads provenance only through
a workspace-scoped repository. A reference to a chunk in another workspace is
therefore reported as not found, never confirmed to exist.

Every resolution — accepted or rejected — is a trust-gate decision and is
written to the append-only audit log with the workspace and actor and only
non-sensitive details (never the quote or the resolved text). Rejections are
returned as a response rather than raised, so the audit row commits with the
request transaction instead of being rolled back.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.auth.dependencies import SessionDep
from app.auth.permissions import WorkspaceAction
from app.auth.workspace import RequireAction, WorkspaceContext
from app.citations.resolver import CitationResolver
from app.citations.types import CitationError, CitationErrorCode, CitationReference
from app.db.repositories.audit import AuditLogRepository
from app.db.repositories.chunks import ChunkRepository
from app.schemas.citations import CitationResolveRequest, ResolvedCitationResponse

logger = logging.getLogger("app.citations")

router = APIRouter(prefix="/workspaces/{workspace_id}/citations", tags=["citations"])

QuerierContext = Annotated[WorkspaceContext, Depends(RequireAction(WorkspaceAction.QUERY))]

AUDIT_ACTION = "citation.resolve"
AUDIT_RESOURCE = "citation"

# Which HTTP status each stable citation-error code surfaces as.
_STATUS_FOR_CODE = {
    CitationErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    CitationErrorCode.OUT_OF_RANGE: status.HTTP_422_UNPROCESSABLE_ENTITY,
    CitationErrorCode.QUOTE_MISMATCH: status.HTTP_422_UNPROCESSABLE_ENTITY,
}


@router.post("/resolve", response_model=ResolvedCitationResponse)
async def resolve_citation(
    payload: CitationResolveRequest,
    context: QuerierContext,
    session: SessionDep,
) -> ResolvedCitationResponse | JSONResponse:
    audit = AuditLogRepository(session)
    resolver = CitationResolver(ChunkRepository(session, context.workspace.id))
    reference = CitationReference(
        document_version_id=payload.document_version_id,
        chunk_id=payload.chunk_id,
        quote=payload.quote,
        quote_char_start=payload.quote_char_start,
        quote_char_end=payload.quote_char_end,
    )

    try:
        resolved = await resolver.resolve(reference)
    except CitationError as error:
        await _record(
            audit,
            context,
            resource_id=payload.chunk_id,
            detail={
                "outcome": "rejected",
                "code": error.code.value,
                "chunk_id": str(payload.chunk_id),
                "document_version_id": str(payload.document_version_id),
            },
        )
        logger.info(
            "citation resolution rejected",
            extra={
                "workspace_id": str(context.workspace.id),
                "code": error.code.value,
                "chunk_id": str(payload.chunk_id),
            },
        )
        # Returned, not raised, so the audit row commits with the transaction.
        return JSONResponse(
            status_code=_STATUS_FOR_CODE[error.code],
            content={"detail": {"code": error.code.value, "message": error.message}},
        )

    await _record(
        audit,
        context,
        resource_id=resolved.chunk_id,
        detail={
            "outcome": "resolved",
            "chunk_id": str(resolved.chunk_id),
            "document_id": str(resolved.document_id),
            "document_version_id": str(resolved.document_version_id),
        },
    )
    logger.info(
        "citation resolved",
        extra={
            "workspace_id": str(context.workspace.id),
            "chunk_id": str(resolved.chunk_id),
            "document_id": str(resolved.document_id),
        },
    )
    return ResolvedCitationResponse.from_resolved(resolved)


async def _record(
    audit: AuditLogRepository,
    context: WorkspaceContext,
    *,
    resource_id: uuid.UUID,
    detail: dict[str, object],
) -> None:
    """Append one non-sensitive citation-resolution audit event."""
    await audit.record(
        action=AUDIT_ACTION,
        resource_type=AUDIT_RESOURCE,
        resource_id=resource_id,
        workspace_id=context.workspace.id,
        actor_user_id=context.user.id,
        detail=detail,
    )
