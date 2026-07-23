"""Grounded-answer endpoint authorization and grounding, over the real stack.

Requires `make infra-up` (or the CI containers). Covers the route's
authorization boundary and the pipeline's end-to-end behaviour against a real
database: a viewer can query and gets a trace, an empty workspace abstains,
seeded evidence yields a grounded answer with an exact citation, and one
tenant's answer can never cite another tenant's evidence.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from app.config import Settings
from app.db.models.documents import Chunk
from app.db.models.identity import User, Workspace
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration import factories
from tests.integration.apptools import Account, build_client, make_account


def settings() -> Settings:
    return Settings(auth_rate_limit_attempts=1000)


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    async with build_client(db_session, settings()) as instance:
        yield instance


async def make_workspace(client: httpx.AsyncClient, account: Account) -> str:
    response = await client.post(
        "/api/v1/workspaces", json={"name": "Answers"}, headers=account.headers
    )
    assert response.status_code == 201, response.text
    workspace_id: str = response.json()["id"]
    return workspace_id


async def _seed_chunk(
    session: AsyncSession,
    *,
    workspace: Workspace,
    owner: User,
    content: str,
    language: str = "eng",
    chunk_index: int = 0,
) -> Chunk:
    document = await factories.make_document(session, workspace, owner)
    version = await factories.make_version(session, document)
    chunk = Chunk(
        workspace_id=workspace.id,
        document_version_id=version.id,
        chunk_index=chunk_index,
        content=content,
        content_hash=f"{chunk_index + 1:064x}",
        page_number=1,
        char_start=0,
        char_end=len(content),
        language=language,
    )
    session.add(chunk)
    await session.flush()
    return chunk


async def test_viewer_can_request_answer_and_gets_trace(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_account(client, "a-owner@example.com")
    viewer = await make_account(client, "a-viewer@example.com")
    workspace_id = await make_workspace(client, owner)
    enrolled = await client.post(
        f"/api/v1/workspaces/{workspace_id}/members",
        json={"email": viewer.email, "role": "viewer"},
        headers=owner.headers,
    )
    assert enrolled.status_code == 201, enrolled.text

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/answer",
        json={"query": "anything"},
        headers=viewer.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Empty workspace: the pipeline must abstain, never invent an answer.
    assert body["outcome"] == "abstained"
    assert body["abstained"] is True
    assert body["claims"] == []
    assert body["trace"]["workspace_id"] == workspace_id
    assert body["trace"]["abstention_reason"] == "insufficient_evidence"


async def test_non_member_gets_workspace_not_found(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "a-owner2@example.com")
    outsider = await make_account(client, "a-outsider@example.com")
    workspace_id = await make_workspace(client, owner)

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/answer",
        json={"query": "anything"},
        headers=outsider.headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workspace_not_found"


async def test_unauthenticated_request_is_rejected(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "a-owner3@example.com")
    workspace_id = await make_workspace(client, owner)

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/answer",
        json={"query": "anything"},
    )
    assert response.status_code == 401


async def test_empty_query_is_rejected_by_validation(client: httpx.AsyncClient) -> None:
    owner = await make_account(client, "a-owner4@example.com")
    workspace_id = await make_workspace(client, owner)

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/answer",
        json={"query": ""},
        headers=owner.headers,
    )
    assert response.status_code == 422


async def test_seeded_evidence_yields_grounded_answer_with_citation(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await make_account(client, "a-grounded@example.com")
    workspace_id = await make_workspace(client, owner)

    workspace = await db_session.get(Workspace, workspace_id)
    user = await db_session.get(User, owner.user_id)
    assert workspace is not None and user is not None
    chunk = await _seed_chunk(
        db_session,
        workspace=workspace,
        owner=user,
        content="The invoice payment is due within thirty days of receipt.",
    )
    await db_session.flush()

    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/answer",
        json={"query": "invoice payment due date"},
        headers=owner.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] in {"answered", "partial"}
    assert body["claims"], "expected a supported claim"
    citation = body["claims"][0]["citation"]
    assert citation["chunk_id"] == str(chunk.id)
    # The cited quote must be a real substring of the seeded chunk content.
    assert citation["quote"] in chunk.content
    assert body["confidence"] > 0.0


async def test_answer_never_cites_another_tenants_evidence(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_a = await make_account(client, "a-tenant-a@example.com")
    owner_b = await make_account(client, "a-tenant-b@example.com")
    workspace_a = await make_workspace(client, owner_a)
    workspace_b = await make_workspace(client, owner_b)

    ws_b = await db_session.get(Workspace, workspace_b)
    user_b = await db_session.get(User, owner_b.user_id)
    assert ws_b is not None and user_b is not None
    await _seed_chunk(
        db_session,
        workspace=ws_b,
        owner=user_b,
        content="The confidential merger closes within thirty days.",
    )
    await db_session.flush()

    # Tenant A holds no evidence; querying for B's content must abstain.
    response = await client.post(
        f"/api/v1/workspaces/{workspace_a}/answer",
        json={"query": "confidential merger thirty days"},
        headers=owner_a.headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "abstained"
    assert body["claims"] == []
