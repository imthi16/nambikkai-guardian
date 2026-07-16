"""End-to-end authentication flows against the real database schema.

Each test runs inside the rolled-back `db_session`, so nothing persists.
The HTTP app is rebuilt per test with that session injected, which also gives
every test its own rate-limiter state.
"""

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from app.auth import tokens
from app.config import Settings
from app.db.models.identity import RefreshToken, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.apptools import build_client, build_settings

EMAIL = "priya@example.com"
PASSWORD = "a-strong-passphrase"


@pytest.fixture
def settings() -> Settings:
    return build_settings()


@pytest.fixture
async def client(
    db_session: AsyncSession,
    settings: Settings,
) -> AsyncIterator[httpx.AsyncClient]:
    async with build_client(db_session, settings) as instance:
        yield instance


async def register(client: httpx.AsyncClient, email: str = EMAIL) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Priya Test"},
    )


async def login(client: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": password})


async def register_and_login(client: httpx.AsyncClient) -> dict[str, str]:
    assert (await register(client)).status_code == 201
    response = await login(client)
    assert response.status_code == 200
    body: dict[str, str] = response.json()
    return body


async def test_register_returns_public_profile_only(client: httpx.AsyncClient) -> None:
    response = await register(client)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == EMAIL
    assert body["full_name"] == "Priya Test"
    assert body["is_active"] is True
    assert "password" not in str(body)


async def test_register_duplicate_email_is_conflict(client: httpx.AsyncClient) -> None:
    assert (await register(client)).status_code == 201
    duplicate = await register(client, email=EMAIL.upper())
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "email_already_registered"


async def test_register_rejects_weak_input(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "short", "full_name": ""},
    )
    assert response.status_code == 422


async def test_login_issues_bearer_token_pair(client: httpx.AsyncClient) -> None:
    pair = await register_and_login(client)
    assert pair["token_type"] == "bearer"
    assert pair["access_token"] and pair["refresh_token"]
    assert int(pair["expires_in"]) > 0


async def test_login_with_wrong_password_is_unauthorized(client: httpx.AsyncClient) -> None:
    assert (await register(client)).status_code == 201
    response = await login(client, password="wrong-passphrase")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_login_with_unknown_email_matches_wrong_password(client: httpx.AsyncClient) -> None:
    response = await login(client)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


async def test_login_for_deactivated_account_is_unauthorized(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    assert (await register(client)).status_code == 201
    user = (await db_session.scalars(select(User).where(User.email == EMAIL))).one()
    user.is_active = False
    await db_session.flush()
    response = await login(client)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


async def test_me_returns_current_user(client: httpx.AsyncClient) -> None:
    pair = await register_and_login(client)
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {pair['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == EMAIL


async def test_me_without_token_is_unauthorized(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "not_authenticated"


async def test_me_with_garbage_token_is_unauthorized(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


async def test_me_with_expired_token_is_unauthorized(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    settings: Settings,
) -> None:
    await register_and_login(client)
    user = (await db_session.scalars(select(User).where(User.email == EMAIL))).one()
    expired = tokens.issue_access_token(
        user.id,
        secret=settings.jwt_secret.get_secret_value(),
        ttl_seconds=1,
        now=tokens.utcnow() - timedelta(minutes=5),
    )
    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


async def test_me_for_deactivated_user_with_valid_token(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    pair = await register_and_login(client)
    user = (await db_session.scalars(select(User).where(User.email == EMAIL))).one()
    user.is_active = False
    await db_session.flush()
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {pair['access_token']}"},
    )
    assert response.status_code == 401


async def test_refresh_rotates_and_invalidates_the_old_token(client: httpx.AsyncClient) -> None:
    pair = await register_and_login(client)
    rotated = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": pair["refresh_token"]},
    )
    assert rotated.status_code == 200
    new_pair = rotated.json()
    assert new_pair["refresh_token"] != pair["refresh_token"]

    replay = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": pair["refresh_token"]},
    )
    assert replay.status_code == 401
    assert replay.json()["detail"]["code"] == "invalid_refresh_token"


async def test_refresh_reuse_revokes_every_session(client: httpx.AsyncClient) -> None:
    pair = await register_and_login(client)
    rotated = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": pair["refresh_token"]},
    )
    new_pair = rotated.json()

    replay = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": pair["refresh_token"]},
    )
    assert replay.status_code == 401

    # The reuse must have voided the rotated (still-unexpired) session too.
    after_reuse = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": new_pair["refresh_token"]},
    )
    assert after_reuse.status_code == 401


async def test_refresh_with_expired_session_is_unauthorized(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    pair = await register_and_login(client)
    token_hash = tokens.hash_refresh_token(pair["refresh_token"])
    stored = (
        await db_session.scalars(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).one()
    stored.expires_at = tokens.utcnow() - timedelta(seconds=1)
    await db_session.flush()

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": pair["refresh_token"]},
    )
    assert response.status_code == 401


async def test_refresh_with_unknown_token_is_unauthorized(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "never-issued"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_refresh_token"


async def test_refresh_for_deactivated_user_is_unauthorized(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    pair = await register_and_login(client)
    user = (await db_session.scalars(select(User).where(User.email == EMAIL))).one()
    user.is_active = False
    await db_session.flush()
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": pair["refresh_token"]},
    )
    assert response.status_code == 401


async def test_logout_revokes_the_session_and_is_idempotent(client: httpx.AsyncClient) -> None:
    pair = await register_and_login(client)
    first = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": pair["refresh_token"]},
    )
    assert first.status_code == 204

    refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": pair["refresh_token"]},
    )
    assert refresh.status_code == 401

    again = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": pair["refresh_token"]},
    )
    assert again.status_code == 204


async def test_login_attempts_are_rate_limited_per_route(db_session: AsyncSession) -> None:
    settings = Settings(auth_rate_limit_attempts=3, auth_rate_limit_window_seconds=3600)
    async with build_client(db_session, settings) as client:
        for _ in range(3):
            response = await login(client)
            assert response.status_code == 401
        limited = await login(client)
        assert limited.status_code == 429
        assert limited.json()["detail"]["code"] == "rate_limited"

        # Another auth route keeps its own window.
        response = await register(client)
        assert response.status_code == 201
