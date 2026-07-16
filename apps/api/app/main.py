"""FastAPI application factory for NambikkAI Guardian."""

from fastapi import FastAPI

from app.api.v1.router import api_v1_router
from app.config import Settings, get_settings
from app.routes.health import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an application with validated configuration."""
    resolved_settings = settings or get_settings()
    application = FastAPI(
        title="NambikkAI Guardian API",
        description=("Secure multilingual document intelligence for Tamil, Tanglish, and English."),
        version=resolved_settings.app_version,
        docs_url="/docs" if resolved_settings.api_docs_enabled else None,
        redoc_url="/redoc" if resolved_settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if resolved_settings.api_docs_enabled else None,
    )
    application.include_router(health_router)
    application.include_router(api_v1_router, prefix="/api/v1")
    return application


app = create_app()
