"""Grounded-answer endpoint under `/api/v1/workspaces/{workspace_id}/answer`.

Answering requires the `QUERY` capability. The route runs inside the workspace
context, so membership is proven and row-level security is bound before the
pipeline touches tenant data. The pipeline reads evidence only through
workspace-scoped repositories and abstains when evidence is insufficient, so an
answer can never assert anything the caller was not authorized to see.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.dependencies import SessionDep, get_app_settings
from app.auth.permissions import WorkspaceAction
from app.auth.workspace import RequireAction, WorkspaceContext
from app.config import Settings
from app.rag.config import RagConfig
from app.rag.retriever import HybridEvidenceRetriever
from app.rag.service import RagService
from app.reranking.service import RerankService, build_reranker
from app.retrieval.service import HybridRetrievalService, RetrievalConfig
from app.schemas.answer import AnswerRequest, AnswerResponse

logger = logging.getLogger("app.rag")

router = APIRouter(prefix="/workspaces/{workspace_id}/answer", tags=["answer"])

QuerierContext = Annotated[WorkspaceContext, Depends(RequireAction(WorkspaceAction.QUERY))]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


@router.post("", response_model=AnswerResponse)
async def answer(
    payload: AnswerRequest,
    context: QuerierContext,
    session: SessionDep,
    settings: SettingsDep,
) -> AnswerResponse:
    retrieval_config = RetrievalConfig(
        rrf_k=settings.retrieval_rrf_k,
        candidate_limit=settings.retrieval_candidate_limit,
        top_k=settings.retrieval_top_k,
        rerank_enabled=settings.rerank_enabled,
        rerank_candidate_limit=settings.rerank_candidate_limit,
    )
    retrieval_service = HybridRetrievalService(
        session,
        rerank_service=RerankService(build_reranker(settings), threshold=settings.rerank_threshold)
        if settings.rerank_enabled
        else None,
        config=retrieval_config,
    )
    rag_config = RagConfig(
        top_k=settings.rag_top_k,
        max_evidence=settings.rag_max_evidence,
        min_evidence=settings.rag_min_evidence,
        min_evidence_score=settings.rag_min_evidence_score,
    )
    # Clamp caller-supplied top_k to a safe maximum; never trust the request.
    top_k = None
    if payload.top_k is not None:
        top_k = min(payload.top_k, settings.rag_max_top_k)

    service = RagService(
        session,
        retriever=HybridEvidenceRetriever(retrieval_service),
        config=rag_config,
    )
    result = await service.answer(
        workspace_id=context.workspace.id,
        query=payload.query,
        actor_user_id=context.user.id,
        document_id=payload.document_id,
        language=payload.language,
        top_k=top_k,
    )
    logger.info(
        "answer completed",
        extra={
            "workspace_id": str(context.workspace.id),
            "trace": result.trace.as_metadata(),
        },
    )
    return AnswerResponse.from_result(result)
