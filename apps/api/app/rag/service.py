"""The grounded-answer service: build state, run the graph, persist the trace.

This is the single entry point a route calls. It constructs the workspace-scoped
retriever, runs the typed LangGraph workflow, and records a non-sensitive audit
event describing *how* the answer was reached (counts, outcome, timings) without
persisting the query, evidence, or answer text.

Authorization is enforced upstream by the route dependency and the row-level
security binding, and again in the retrieval data layer; this service never
loosens that boundary.
"""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.audit import AuditLogRepository
from app.rag.config import RagConfig
from app.rag.generation import AnswerGenerator
from app.rag.graph import EvidenceRetriever, RagGraph
from app.rag.retriever import HybridEvidenceRetriever
from app.rag.state import RagState
from app.rag.types import RagResult, RagTrace
from app.rag.verification import ClaimVerifier
from app.retrieval.service import HybridRetrievalService

logger = logging.getLogger("app.rag")

AUDIT_ACTION = "rag.answer"
AUDIT_RESOURCE = "conversation"


class RagService:
    """Runs the grounded-answer pipeline for one authorized workspace query."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        retriever: EvidenceRetriever | None = None,
        generator: AnswerGenerator | None = None,
        verifier: ClaimVerifier | None = None,
        config: RagConfig | None = None,
    ) -> None:
        self._session = session
        self._config = config or RagConfig()
        resolved_retriever = retriever or HybridEvidenceRetriever(HybridRetrievalService(session))
        self._graph = RagGraph(
            resolved_retriever,
            generator=generator,
            verifier=verifier,
            config=self._config,
        )

    async def answer(
        self,
        *,
        workspace_id: uuid.UUID,
        query: str,
        actor_user_id: uuid.UUID | None = None,
        document_id: uuid.UUID | None = None,
        language: str | None = None,
        top_k: int | None = None,
    ) -> RagResult:
        """Produce a grounded answer or a calibrated abstention."""
        resolved_top_k = top_k or self._config.top_k
        trace = RagTrace(
            workspace_id=workspace_id,
            detected_language="unknown",
            top_k=resolved_top_k,
        )
        state = RagState(
            workspace_id=workspace_id,
            query=query,
            top_k=resolved_top_k,
            document_id=document_id,
            language_filter=language,
            trace=trace,
        )

        start = time.perf_counter()
        terminal = await self._graph.run(state)
        terminal.trace.total_ms = (time.perf_counter() - start) * 1000

        await self._record(workspace_id, actor_user_id, terminal.trace)
        logger.info(
            "rag answer completed",
            extra={"workspace_id": str(workspace_id), "trace": terminal.trace.as_metadata()},
        )
        return RagResult(answer=terminal.to_answer(), trace=terminal.trace)

    async def _record(
        self,
        workspace_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        trace: RagTrace,
    ) -> None:
        """Append an audit event carrying only the non-sensitive trace."""
        await AuditLogRepository(self._session).record(
            action=AUDIT_ACTION,
            resource_type=AUDIT_RESOURCE,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            detail=trace.as_metadata(),
        )
