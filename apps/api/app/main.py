from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


app = FastAPI(
    title="NambikkAI Guardian API",
    description=(
        "Secure multilingual document intelligence for Tamil, Tanglish, and English."
    ),
    version="0.1.0",
)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Return a dependency-free liveness response."""
    return HealthResponse(status="ok", service="nambikkai-api", version="0.1.0")
