"""Top-level router for API version 1."""

from fastapi import APIRouter

from app.routes.health import router as health_router

api_v1_router = APIRouter()
api_v1_router.include_router(health_router)
