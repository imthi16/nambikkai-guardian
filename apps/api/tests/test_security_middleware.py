"""Global rate limiting, request-body caps, and CORS wiring."""

import logging

import pytest
from app.config import Settings
from app.main import create_app
from httpx import ASGITransport, AsyncClient


def _client(settings: Settings) -> AsyncClient:
    transport = ASGITransport(app=create_app(settings))
    return AsyncClient(transport=transport, base_url="http://testserver")


async def test_global_rate_limit_blocks_after_quota(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(_env_file=None, global_rate_limit_attempts=2)
    with caplog.at_level(logging.WARNING, logger="app.security"):
        async with _client(settings) as client:
            first = await client.get("/api/v1/does-not-exist")
            second = await client.get("/api/v1/does-not-exist")
            blocked = await client.get("/api/v1/does-not-exist")

    assert first.status_code == 404
    assert second.status_code == 404
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert blocked.headers["Retry-After"] == "60"
    # The 429 is still hardened.
    assert blocked.headers["X-Content-Type-Options"] == "nosniff"
    assert any(
        getattr(record, "security_event", None) == "rate_limited" for record in caplog.records
    )


async def test_health_is_exempt_from_global_rate_limit() -> None:
    settings = Settings(_env_file=None, global_rate_limit_attempts=1)
    async with _client(settings) as client:
        await client.get("/api/v1/does-not-exist")  # consume the single slot
        health = await client.get("/health")
        health_again = await client.get("/api/v1/health")

    assert health.status_code == 200
    assert health_again.status_code == 200


async def test_request_body_cap_rejects_oversized_body() -> None:
    settings = Settings(_env_file=None, max_upload_bytes=100, max_request_body_bytes=100)
    async with _client(settings) as client:
        response = await client.post("/api/v1/auth/login", content=b"x" * 200)

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_body_too_large"


async def test_request_body_cap_allows_small_body() -> None:
    settings = Settings(_env_file=None, max_upload_bytes=100, max_request_body_bytes=100)
    async with _client(settings) as client:
        # Small body passes the cap; the request then fails validation (422),
        # proving the middleware forwarded it rather than rejecting on size.
        response = await client.post("/api/v1/auth/login", json={"email": "a@b.co"})

    assert response.status_code == 422


async def test_invalid_content_length_is_rejected() -> None:
    settings = Settings(_env_file=None)
    async with _client(settings) as client:
        response = await client.post(
            "/api/v1/auth/login",
            headers={"content-length": "not-a-number"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_content_length"


async def test_cors_allows_configured_origin() -> None:
    settings = Settings(_env_file=None, cors_allowed_origins="https://app.example.com")
    async with _client(settings) as client:
        response = await client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.headers["access-control-allow-origin"] == "https://app.example.com"
    # Credentials are never reflected: bearer-token API carries no cookies.
    assert "access-control-allow-credentials" not in response.headers


async def test_cors_rejects_unconfigured_origin() -> None:
    settings = Settings(_env_file=None)
    async with _client(settings) as client:
        response = await client.get(
            "/api/v1/health",
            headers={"Origin": "https://evil.example.com"},
        )

    assert "access-control-allow-origin" not in response.headers
