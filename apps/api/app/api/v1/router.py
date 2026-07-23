"""Top-level router for API version 1."""

from fastapi import APIRouter

from app.routes.answer import router as answer_router
from app.routes.auth import router as auth_router
from app.routes.documents import router as documents_router
from app.routes.health import router as health_router
from app.routes.retrieval import router as retrieval_router
from app.routes.workspaces import router as workspaces_router

api_v1_router = APIRouter()
api_v1_router.include_router(health_router)
api_v1_router.include_router(auth_router)
api_v1_router.include_router(workspaces_router)
api_v1_router.include_router(documents_router)
api_v1_router.include_router(retrieval_router)
api_v1_router.include_router(answer_router)
