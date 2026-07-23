"""OWASP-focused API hardening against real PostgreSQL.

These cover controls that need the database: per-workspace upload quotas,
security audit trails for authentication, and the cookie-free session model
that underpins the CSRF strategy. Quota rejections are enforced before any byte
reaches object storage, so these tests need Postgres but not MinIO.
"""

import logging

import httpx
import pytest
from app.config import Settings
from app.db.models.operations import AuditLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.apptools import Account, build_client, make_account

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


async def make_workspace(client: httpx.AsyncClient, account: Account) -> str:
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "Security"},
        headers=account.headers,
    )
    assert response.status_code == 201, response.text
    workspace_id: str = response.json()["id"]
    return workspace_id


async def upload(
    client: httpx.AsyncClient,
    account: Account,
    workspace_id: str,
    *,
    content: bytes = PDF_BYTES,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/workspaces/{workspace_id}/documents",
        files={"file": ("report.pdf", content, "application/pdf")},
        headers=account.headers,
    )


async def test_document_count_quota_rejects_upload(db_session: AsyncSession) -> None:
    settings = Settings(auth_rate_limit_attempts=1000, workspace_max_documents=0)
    async with build_client(db_session, settings) as client:
        owner = await make_account(client, "owner@example.com")
        workspace_id = await make_workspace(client, owner)

        rejected = await upload(client, owner, workspace_id)

    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "workspace_document_limit_reached"


async def test_storage_quota_rejects_upload(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(auth_rate_limit_attempts=1000, workspace_storage_quota_bytes=8)
    with caplog.at_level(logging.WARNING, logger="app.security"):
        async with build_client(db_session, settings) as client:
            owner = await make_account(client, "owner@example.com")
            workspace_id = await make_workspace(client, owner)

            rejected = await upload(client, owner, workspace_id)

    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "workspace_storage_quota_exceeded"
    assert any(
        getattr(record, "security_event", None) == "workspace_storage_quota_exceeded"
        for record in caplog.records
    )


async def test_authentication_events_are_audited(db_session: AsyncSession) -> None:
    settings = Settings(auth_rate_limit_attempts=1000)
    async with build_client(db_session, settings) as client:
        account = await make_account(client, "owner@example.com")

    actions = (
        await db_session.scalars(select(AuditLog.action).where(AuditLog.resource_type == "user"))
    ).all()
    assert set(actions) == {"auth.user_registered", "auth.login_succeeded"}

    actors = (
        await db_session.scalars(
            select(AuditLog.actor_user_id).where(AuditLog.resource_type == "user")
        )
    ).all()
    assert all(str(actor) == account.user_id for actor in actors)


async def test_failed_login_is_logged_not_audited(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(auth_rate_limit_attempts=1000)
    with caplog.at_level(logging.WARNING, logger="app.security"):
        async with build_client(db_session, settings) as client:
            await make_account(client, "owner@example.com")
            bad = await client.post(
                "/api/v1/auth/login",
                json={"email": "owner@example.com", "password": "the-wrong-password"},
            )

    assert bad.status_code == 401
    assert bad.json()["detail"]["code"] == "invalid_credentials"
    login_events = [
        record
        for record in caplog.records
        if getattr(record, "security_event", None) == "login_failed"
    ]
    assert login_events
    # Privacy-safe: the submitted email is never in the security event.
    assert all("owner@example.com" not in record.getMessage() for record in login_events)


async def test_login_sets_no_cookie(db_session: AsyncSession) -> None:
    settings = Settings(auth_rate_limit_attempts=1000)
    async with build_client(db_session, settings) as client:
        await make_account(client, "owner@example.com")
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "a-strong-passphrase"},
        )

    assert response.status_code == 200
    # The CSRF strategy depends on there being no ambient cookie credential.
    assert "set-cookie" not in response.headers
