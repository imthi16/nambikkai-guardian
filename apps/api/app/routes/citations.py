"""Citation-resolution endpoint under `/api/v1/workspaces/{workspace_id}/citations`.

Resolving a citation requires the `QUERY` capability — the same capability that
lets a member see answers and their citations. The route runs inside the
workspace context, so membership is proven and row-level security is bound
before any tenant data is read, and the resolver loads provenance only through
a workspace-scoped repository. A reference to a chunk in another workspace is
therefore reported as not found, never confirmed to exist.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import SessionDep
from app.auth.permissions import WorkspaceAction
from app.auth.workspace import RequireAction, WorkspaceContext
from app.citations.resolver import CitationResolver
from app.citations.types import CitationError, CitationErrorCode, CitationReference
from app.db.repositories.chunks import ChunkRepository
from app.schemas.citations import CitationResolveRequest, ResolvedCitationResponse

logger = logging.getLogger("app.citations")

router = APIRouter(prefix="/workspaces/{workspace_id}/citations", tags=["citations"])

QuerierContext = Annotated[WorkspaceContext, Depends(RequireAction(WorkspaceAction.QUERY))]

# Which HTTP status each stable citation-error code surfaces as.
_STATUS_FOR_CODE = {
    CitationErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    CitationErrorCode.OUT_OF_RANGE: status.HTTP_422_UNPROCESSABLE_ENTITY,
    CitationErrorCode.QUOTE_MISMATCH: status.HTTP_422_UNPROCESSABLE_ENTITY,
}


def _http_error(error: CitationError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_FOR_CODE[error.code],
        detail={"code": error.code.value, "message": error.message},
    )


@router.post("/resolve", response_model=ResolvedCitationResponse)
async def resolve_citation(
    payload: CitationResolveRequest,
    context: QuerierContext,
    session: SessionDep,
) -> ResolvedCitationResponse:
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
        # Record only the non-sensitive outcome: the failure code and chunk id,
        # never the quote text or document content.
        logger.info(
            "citation resolution rejected",
            extra={
                "workspace_id": str(context.workspace.id),
                "code": error.code.value,
                "chunk_id": str(payload.chunk_id),
            },
        )
        raise _http_error(error) from error

    logger.info(
        "citation resolved",
        extra={
            "workspace_id": str(context.workspace.id),
            "chunk_id": str(resolved.chunk_id),
            "document_id": str(resolved.document_id),
        },
    )
    return ResolvedCitationResponse.from_resolved(resolved)
