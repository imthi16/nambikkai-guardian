"""Shared helpers to build test apps and authenticated accounts."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from app.config import Settings
from app.db.session import get_db_session
from app.main import create_app
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_PASSWORD = "a-strong-passphrase"


def build_settings() -> Settings:
    """Settings with a rate limit generous enough not to interfere with tests."""
    return Settings(auth_rate_limit_attempts=1000)


def build_client(db_session: AsyncSession, settings: Settings) -> httpx.AsyncClient:
    """An HTTP client over a fresh app whose DB dependency is the test session."""
    application = create_app(settings)

    async def _use_test_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = _use_test_session
    transport = httpx.ASGITransport(app=application)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@dataclass(frozen=True)
class Account:
    """A registered, logged-in user for API tests."""

    user_id: str
    email: str
    headers: dict[str, str]


async def make_account(client: httpx.AsyncClient, email: str) -> Account:
    registered = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": DEFAULT_PASSWORD, "full_name": "Synthetic Person"},
    )
    assert registered.status_code == 201, registered.text
    logged_in = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": DEFAULT_PASSWORD},
    )
    assert logged_in.status_code == 200, logged_in.text
    token = logged_in.json()["access_token"]
    return Account(
        user_id=registered.json()["id"],
        email=email,
        headers={"Authorization": f"Bearer {token}"},
    )
