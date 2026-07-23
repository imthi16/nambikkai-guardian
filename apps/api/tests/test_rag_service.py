"""Unit tests for RagService: it runs the graph and records a safe audit trace.

A fake audit repository captures what would be persisted so we can assert the
service records the non-sensitive trace (never the query, evidence, or answer
text) and drives the graph to a terminal answer.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.rag import service as service_module
from app.rag.config import RagConfig
from app.rag.service import RagService
from app.rag.types import AnswerOutcome, EvidencePassage

WORKSPACE = uuid.UUID(int=1)
ACTOR = uuid.UUID(int=2)


def _passage(text: str, order: int) -> EvidencePassage:
    return EvidencePassage(
        chunk_id=uuid.UUID(int=100 + order),
        document_id=uuid.UUID(int=9),
        document_version_id=uuid.UUID(int=8),
        content=text,
        page_number=order + 1,
        section=None,
        char_start=0,
        char_end=len(text),
        language="eng",
        ocr_engine=None,
        ocr_confidence=None,
        fused_score=0.9,
        rerank_score=0.85,
        order=order,
    )


class FakeRetriever:
    def __init__(self, passages: Sequence[EvidencePassage]) -> None:
        self._passages = tuple(passages)

    async def retrieve(self, *, workspace_id, query, top_k, document_id, language):  # type: ignore[no-untyped-def]
        return self._passages, {"returned_count": len(self._passages)}


class FakeAuditRepo:
    def __init__(self, session: object) -> None:
        self.session = session
        self.records: list[dict[str, object]] = []

    async def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


def _install_audit(monkeypatch) -> list[FakeAuditRepo]:  # type: ignore[no-untyped-def]
    created: list[FakeAuditRepo] = []

    def factory(session: object) -> FakeAuditRepo:
        repo = FakeAuditRepo(session)
        created.append(repo)
        return repo

    monkeypatch.setattr(service_module, "AuditLogRepository", factory)
    return created


async def test_service_answers_and_records_safe_trace(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    audits = _install_audit(monkeypatch)
    evidence = [_passage("The invoice payment is due within thirty days of receipt.", 0)]
    service = RagService(
        session=object(),  # type: ignore[arg-type]
        retriever=FakeRetriever(evidence),
        config=RagConfig(),
    )
    result = await service.answer(
        workspace_id=WORKSPACE,
        query="invoice payment due date",
        actor_user_id=ACTOR,
    )

    assert result.answer.outcome is AnswerOutcome.ANSWERED
    assert result.answer.claims
    assert result.trace.total_ms >= 0.0

    assert len(audits) == 1 and len(audits[0].records) == 1
    recorded = audits[0].records[0]
    assert recorded["action"] == service_module.AUDIT_ACTION
    assert recorded["workspace_id"] == WORKSPACE
    assert recorded["actor_user_id"] == ACTOR
    # The persisted detail is the non-sensitive trace, not query/answer text.
    detail = str(recorded["detail"])
    assert "invoice payment due date" not in detail
    assert "thirty days" not in detail


async def test_service_abstains_and_still_records(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    audits = _install_audit(monkeypatch)
    service = RagService(
        session=object(),  # type: ignore[arg-type]
        retriever=FakeRetriever([]),
        config=RagConfig(),
    )
    result = await service.answer(workspace_id=WORKSPACE, query="anything at all")

    assert result.answer.outcome is AnswerOutcome.ABSTAINED
    assert result.answer.claims == ()
    assert audits[0].records[0]["detail"]["abstained"] is True  # type: ignore[index]
