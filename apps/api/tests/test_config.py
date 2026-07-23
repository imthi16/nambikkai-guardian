"""Configuration safety tests."""

from pathlib import Path
from typing import cast

import pytest
from app.config import Environment, Settings, _find_repository_env
from pydantic import ValidationError


def test_development_defaults_are_usable() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.api_docs_enabled is True
    assert settings.jwt_secret.get_secret_value() == "development-only-change-me"


def test_rag_pipeline_defaults_are_present() -> None:
    settings = Settings(_env_file=None)

    assert settings.rag_top_k == 8
    assert settings.rag_max_top_k == 20
    assert settings.rag_max_evidence == 6
    assert settings.rag_min_evidence == 1
    assert settings.rag_min_evidence_score == 0.0


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_deployed_environments_reject_local_secrets(environment: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET must be replaced"):
        Settings(app_env=cast(Environment, environment), _env_file=None)


def test_production_accepts_replaced_secrets() -> None:
    settings = Settings(
        app_env="production",
        jwt_secret="a-production-secret-provided-by-a-secret-manager",
        s3_secret_key="a-production-object-storage-secret",
        _env_file=None,
    )

    assert settings.app_env == "production"


def test_production_rejects_local_object_storage_secret() -> None:
    with pytest.raises(ValidationError, match="S3_SECRET_KEY must be replaced"):
        Settings(
            app_env="production",
            jwt_secret="a-production-secret-provided-by-a-secret-manager",
            _env_file=None,
        )


def test_repository_env_is_discovered_from_marker(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    module_path = repository / "apps" / "api" / "app" / "config.py"
    module_path.parent.mkdir(parents=True)
    (repository / "AGENTS.md").touch()

    assert _find_repository_env(module_path) == repository / ".env"


def test_container_layout_without_repository_marker_has_no_env_file(tmp_path: Path) -> None:
    module_path = tmp_path / "app" / "app" / "config.py"
    module_path.parent.mkdir(parents=True)

    assert _find_repository_env(module_path) is None


def test_security_defaults_are_safe_for_local_development() -> None:
    settings = Settings(_env_file=None)

    assert settings.cors_origins() == []
    assert settings.security_hsts_enabled is False
    assert settings.max_request_body_bytes >= settings.max_upload_bytes


def test_cors_origins_are_parsed_and_trimmed() -> None:
    settings = Settings(
        _env_file=None,
        cors_allowed_origins=" https://a.example.com , https://b.example.com ",
    )

    assert settings.cors_origins() == ["https://a.example.com", "https://b.example.com"]


def test_request_body_cap_below_upload_cap_is_rejected() -> None:
    with pytest.raises(ValidationError, match="MAX_REQUEST_BODY_BYTES"):
        Settings(_env_file=None, max_upload_bytes=1000, max_request_body_bytes=100)


def test_wildcard_cors_origin_is_rejected_everywhere() -> None:
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(_env_file=None, cors_allowed_origins="*")


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_short_jwt_secret_is_rejected_in_deployment(environment: str) -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(
            app_env=cast(Environment, environment),
            jwt_secret="too-short",
            s3_secret_key="a-production-object-storage-secret",
            _env_file=None,
        )


def test_plaintext_cors_origin_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="https"):
        Settings(
            app_env="production",
            jwt_secret="a-production-secret-provided-by-a-secret-manager",
            s3_secret_key="a-production-object-storage-secret",
            cors_allowed_origins="http://app.example.com",
            _env_file=None,
        )


def test_production_accepts_https_cors_origin() -> None:
    settings = Settings(
        app_env="production",
        jwt_secret="a-production-secret-provided-by-a-secret-manager",
        s3_secret_key="a-production-object-storage-secret",
        cors_allowed_origins="https://app.example.com",
        _env_file=None,
    )

    assert settings.cors_origins() == ["https://app.example.com"]
