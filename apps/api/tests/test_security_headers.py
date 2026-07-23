"""Response-header hardening applied to every API response."""

import pytest
from app.config import Settings
from app.main import create_app
from httpx import ASGITransport, AsyncClient

_EXPECTED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _client(settings: Settings) -> AsyncClient:
    transport = ASGITransport(app=create_app(settings))
    return AsyncClient(transport=transport, base_url="http://testserver")


async def test_security_headers_present_on_success() -> None:
    async with _client(Settings(_env_file=None)) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    for header, value in _EXPECTED_HEADERS.items():
        assert response.headers[header] == value
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert "geolocation=()" in response.headers["Permissions-Policy"]
    assert response.headers["Server"] == "attest"


async def test_headers_present_on_error_responses() -> None:
    async with _client(Settings(_env_file=None)) as client:
        response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"]
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    ("hsts_enabled", "expect_header"),
    [(True, True), (False, False)],
)
async def test_hsts_is_environment_specific(hsts_enabled: bool, expect_header: bool) -> None:
    settings = Settings(_env_file=None, security_hsts_enabled=hsts_enabled)
    async with _client(settings) as client:
        response = await client.get("/health")

    assert ("Strict-Transport-Security" in response.headers) is expect_header
    if expect_header:
        assert "max-age=63072000" in response.headers["Strict-Transport-Security"]
