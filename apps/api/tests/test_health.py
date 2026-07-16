"""Liveness API tests."""

import pytest
from app.main import app
from httpx import ASGITransport, AsyncClient


@pytest.mark.parametrize("path", ["/health", "/api/v1/health"])
async def test_health(path: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "nambikkai-api",
        "version": "0.1.0",
    }
