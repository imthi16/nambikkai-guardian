"""Dependency-free liveness routes."""

from fastapi import APIRouter

from app.config import get_settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Confirm that the API process can serve requests."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service="nambikkai-api",
        version=settings.app_version,
    )
