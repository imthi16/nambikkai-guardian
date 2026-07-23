"""Citation-resolution endpoint over the real stack.

Requires `make infra-up` (or the CI containers). Covers the route's
authorization boundary and the resolver's guarantees against a real database: a
seeded citation resolves to immutable provenance and exact supporting text,
out-of-range and quote-mismatched references fail with stable codes, and a
reference to another tenant's chunk is reported as not found rather than
confirmed to exist.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from app.config import Settings
from app.db.models.documents import Chunk, Document, DocumentVersion
from app.db.models.enums import DocumentStatus
from app.db.models.identity import User, Workspace
from app.db.models.operations import AuditLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration import factories
from tests.integration.apptools import Account, build_client, make_account

CONTENT = "The invoice payment is due within thirty days of receipt."


def settings() -> Settings:
    return Settings(auth_rate_limit_attempts=1000)


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    async with build_client(db_session, settings()) as instance:
        yield instance


async def make_workspace(client: httpx.AsyncClient, account: Account) -> str:
    response = await client.post(
        "/api/v1/workspaces", json={"name": "Citations"}, headers=account.headers
    )
    assert response.status_code == 201, response.text
    workspace_id: str = response.json()["id"]
    return workspace_id


async def _seed_chunk(
    session: AsyncSession,
    *,
    workspace: Workspace,
    owner: User,
    content: str = CONTENT,
    title: str = "Vendor Agreement",
    char_start: int = 500,
    status: DocumentStatus = DocumentStatus.READY,
) -> Chunk:
    document = Document(
        workspace_id=workspace.id,
        created_by=owner.id,
        title=title,
        source_filename="agreement.pdf",
        mime_type="application/pdf",
        size_bytes=2048,
        sha256="d" * 64,
        status=status,
    )
    session.add(document)
    await session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version_number=2,
        storage_key=factories.unique("documents/agreement") + ".pdf",
        sha256="e" * 64,
        size_bytes=2048,
        page_count=1,
    )
    session.add(version)
    await session.flush()
    chunk = Chunk(
        workspace_id=workspace.id,
        document_version_id=version.id,
        chunk_index=0,
        content=content,
        content_hash="f" * 64,
        page_number=7,
        section="Payment terms",
        char_start=char_start,
        char_end=char_start + len(content),
        language="eng",
    )
    session.add(chunk)
    await session.flush()
    return chunk


def _reference_body(chunk: Chunk, quote: str) -> dict[str, object]:
    start = chunk.content.index(quote)
    return {
        "document_version_id": str(chunk.document_version_id),
        "chunk_id": str(chunk.id),
        "quote": quote,
        "quote_char_start": start,
        "quote_char_end": start + len(quote),
    }


async def test_resolves_seeded_citation_to_provenance_and_exact_text(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_account(client, "c-owner@example.com")
    workspace_id = await make_workspace(client, owner)
    workspace = await db_session.get(Workspace, workspace_id)
    user = await db_session.get(User, owner.user_id)
    assert workspace is not None and user is not None
    chunk = await _seed_chunk(db_session, workspace=workspace, owner=user)
    await db_session.flush()

    quote = "payment is due within thirty days"
    start = chunk.content.index(quote)
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/citations/resolve",
        json=_reference_body(chunk, quote),
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["supporting_text"] == quote
    assert body["document_title"] == "Vendor Agreement"
    assert body["version_number"] == 2
    assert body["page_number"] == 7
    assert body["section"] == "Payment terms"
    assert body["chunk_char_start"] == 500
    assert body["page_quote_char_start"] == 500 + start
    assert body["page_quote_char_end"] == 500 + start + len(quote)
    assert body["support_score"] == 1.0

    # The accepted resolution is recorded in the append-only audit log.
    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == workspace.id,
                    AuditLog.action == "citation.resolve",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].detail["outcome"] == "resolved"
    assert audits[0].actor_user_id == user.id


async def test_out_of_range_reference_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_account(client, "c-range@example.com")
    workspace_id = await make_workspace(client, owner)
    workspace = await db_session.get(Workspace, workspace_id)
    user = await db_session.get(User, owner.user_id)
    assert workspace is not None and user is not None
    chunk = await _seed_chunk(db_session, workspace=workspace, owner=user)
    await db_session.flush()

    body = {
        "document_version_id": str(chunk.document_version_id),
        "chunk_id": str(chunk.id),
        "quote": "anything",
        "quote_char_start": 0,
        "quote_char_end": len(CONTENT) + 100,
    }
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/citations/resolve",
        json=body,
        headers=owner.headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "citation_out_of_range"


async def test_negative_offset_uses_stable_citation_envelope(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """A negative offset is a domain failure, not a generic validation error."""
    owner = await make_account(client, "c-negative@example.com")
    workspace_id = await make_workspace(client, owner)
    workspace = await db_session.get(Workspace, workspace_id)
    user = await db_session.get(User, owner.user_id)
    assert workspace is not None and user is not None
    chunk = await _seed_chunk(db_session, workspace=workspace, owner=user)
    await db_session.flush()

    body = {
        "document_version_id": str(chunk.document_version_id),
        "chunk_id": str(chunk.id),
        "quote": "invoice",
        "quote_char_start": -3,
        "quote_char_end": 4,
    }
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/citations/resolve",
        json=body,
        headers=owner.headers,
    )
    assert response.status_code == 422
    # The stable citation envelope, not FastAPI's generic validation list.
    assert response.json()["detail"]["code"] == "citation_out_of_range"

    # The rejection is recorded in the audit log.
    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == workspace.id,
                    AuditLog.action == "citation.resolve",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].detail["outcome"] == "rejected"
    assert audits[0].detail["code"] == "citation_out_of_range"


async def test_citation_from_non_ready_document_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """A quarantined/incomplete document's chunk must never resolve."""
    owner = await make_account(client, "c-notready@example.com")
    workspace_id = await make_workspace(client, owner)
    workspace = await db_session.get(Workspace, workspace_id)
    user = await db_session.get(User, owner.user_id)
    assert workspace is not None and user is not None
    chunk = await _seed_chunk(
        db_session, workspace=workspace, owner=user, status=DocumentStatus.QUARANTINED
    )
    await db_session.flush()

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/citations/resolve",
        json=_reference_body(chunk, "invoice payment"),
        headers=owner.headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "citation_not_found"


async def test_quote_mismatch_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_account(client, "c-mismatch@example.com")
    workspace_id = await make_workspace(client, owner)
    workspace = await db_session.get(Workspace, workspace_id)
    user = await db_session.get(User, owner.user_id)
    assert workspace is not None and user is not None
    chunk = await _seed_chunk(db_session, workspace=workspace, owner=user)
    await db_session.flush()

    body = {
        "document_version_id": str(chunk.document_version_id),
        "chunk_id": str(chunk.id),
        "quote": "payment is waived entirely",  # not what the chunk says here
        "quote_char_start": 0,
        "quote_char_end": 26,
    }
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/citations/resolve",
        json=body,
        headers=owner.headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "citation_quote_mismatch"


async def test_wrong_document_version_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_account(client, "c-version@example.com")
    workspace_id = await make_workspace(client, owner)
    workspace = await db_session.get(Workspace, workspace_id)
    user = await db_session.get(User, owner.user_id)
    assert workspace is not None and user is not None
    chunk = await _seed_chunk(db_session, workspace=workspace, owner=user)
    await db_session.flush()

    quote = "invoice payment"
    body = _reference_body(chunk, quote)
    body["document_version_id"] = str(uuid.uuid4())  # a version the chunk is not under
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/citations/resolve",
        json=body,
        headers=owner.headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "citation_not_found"


async def test_citation_never_resolves_another_tenants_chunk(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_a = await make_account(client, "c-tenant-a@example.com")
    owner_b = await make_account(client, "c-tenant-b@example.com")
    workspace_a = await make_workspace(client, owner_a)
    workspace_b = await make_workspace(client, owner_b)

    ws_b = await db_session.get(Workspace, workspace_b)
    user_b = await db_session.get(User, owner_b.user_id)
    assert ws_b is not None and user_b is not None
    foreign = await _seed_chunk(db_session, workspace=ws_b, owner=user_b)
    await db_session.flush()

    # Tenant A references tenant B's real chunk with a genuine quote/offsets.
    response = await client.post(
        f"/api/v1/workspaces/{workspace_a}/citations/resolve",
        json=_reference_body(foreign, "invoice payment"),
        headers=owner_a.headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "citation_not_found"


async def test_non_member_gets_workspace_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_account(client, "c-owner2@example.com")
    outsider = await make_account(client, "c-outsider@example.com")
    workspace_id = await make_workspace(client, owner)
    workspace = await db_session.get(Workspace, workspace_id)
    user = await db_session.get(User, owner.user_id)
    assert workspace is not None and user is not None
    chunk = await _seed_chunk(db_session, workspace=workspace, owner=user)
    await db_session.flush()

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/citations/resolve",
        json=_reference_body(chunk, "invoice payment"),
        headers=outsider.headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workspace_not_found"


async def test_unauthenticated_request_is_rejected(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "c-owner3@example.com")
    workspace_id = await make_workspace(client, owner)

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/citations/resolve",
        json={
            "document_version_id": str(uuid.uuid4()),
            "chunk_id": str(uuid.uuid4()),
            "quote": "x",
            "quote_char_start": 0,
            "quote_char_end": 1,
        },
    )
    assert response.status_code == 401


async def test_viewer_can_resolve_citations(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_account(client, "c-owner4@example.com")
    viewer = await make_account(client, "c-viewer@example.com")
    workspace_id = await make_workspace(client, owner)
    enrolled = await client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"email": viewer.email, "role": "viewer"},
        headers=owner.headers,
    )
    assert enrolled.status_code == 201, enrolled.text

    workspace = await db_session.get(Workspace, workspace_id)
    user = await db_session.get(User, owner.user_id)
    assert workspace is not None and user is not None
    chunk = await _seed_chunk(db_session, workspace=workspace, owner=user)
    await db_session.flush()

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/citations/resolve",
        json=_reference_body(chunk, "invoice payment"),
        headers=viewer.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["supporting_text"] == "invoice payment"
