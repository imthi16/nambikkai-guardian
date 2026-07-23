"""Validated environment configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]
_UNSAFE_JWT_SECRETS = {
    "development-only-change-me",
    "replace-with-a-long-random-value",
}


def _find_repository_env(module_path: Path) -> Path | None:
    """Locate the repository `.env` without assuming a source-tree depth."""
    for parent in module_path.resolve().parents:
        if (parent / "AGENTS.md").is_file():
            return parent / ".env"
    return None


_ROOT_ENV_FILE = _find_repository_env(Path(__file__))


class Settings(BaseSettings):
    """Application settings loaded from process environment or the root `.env`."""

    model_config = SettingsConfigDict(
        env_file=_ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Environment = "development"
    app_version: str = "0.1.0"
    api_host: str = "0.0.0.0"  # noqa: S104 - required inside containers
    api_port: int = 8000
    api_docs_enabled: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    database_url: str = "postgresql+asyncpg://attest:attest@localhost:5432/attest"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minio"
    s3_secret_key: SecretStr = SecretStr("minio123")
    s3_bucket: str = "attest-documents"
    jwt_secret: SecretStr = SecretStr("development-only-change-me")
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 14 * 24 * 3600
    auth_rate_limit_attempts: int = 10
    auth_rate_limit_window_seconds: int = 60
    max_upload_bytes: int = 25 * 1024 * 1024
    download_url_ttl_seconds: int = 300
    ingestion_queue_key: str = "attest:ingestion:queue"
    ingestion_dead_letter_key: str = "attest:ingestion:dead"
    ingestion_max_attempts: int = 3
    ingestion_stale_after_seconds: int = 300
    ingestion_store_page_images: bool = True
    ocr_engine: Literal["none", "tesseract", "paddle"] = "none"
    ocr_languages: str = "tam+eng"
    chunk_max_chars: int = 1200
    chunk_overlap_chars: int = 150
    embedding_provider: Literal["local"] = "local"
    embedding_model: str = "bge-m3-local"
    embedding_model_version: str = "hashing-v1"
    embedding_dimensions: int = 1024
    embedding_batch_size: int = 32
    embedding_max_attempts: int = 3
    embedding_backoff_seconds: float = 0.5
    retrieval_rrf_k: int = 60
    retrieval_candidate_limit: int = 50
    retrieval_top_k: int = 10
    retrieval_max_top_k: int = 50
    rerank_enabled: bool = True
    rerank_threshold: float = 0.0
    rerank_candidate_limit: int = 30
    rag_top_k: int = 8
    rag_max_top_k: int = 20
    rag_max_evidence: int = 6
    rag_min_evidence: int = 1
    rag_min_evidence_score: float = 0.0

    @model_validator(mode="after")
    def enforce_deployment_secrets(self) -> Self:
        """Reject known local-only secrets outside development and test."""
        if self.app_env in {"staging", "production"}:
            if self.jwt_secret.get_secret_value() in _UNSAFE_JWT_SECRETS:
                msg = "JWT_SECRET must be replaced in staging and production"
                raise ValueError(msg)
            if self.s3_secret_key.get_secret_value() == "minio123":
                msg = "S3_SECRET_KEY must be replaced in staging and production"
                raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""
    return Settings()
