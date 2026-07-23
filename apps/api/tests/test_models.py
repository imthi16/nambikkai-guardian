"""Schema-shape tests that need no database."""

import app.db.models  # noqa: F401 - registers every table
from app.db.base import Base

EXPECTED_TABLES = {
    "audit_logs",
    "chunk_embeddings",
    "chunks",
    "citations",
    "conversations",
    "document_versions",
    "documents",
    "ingestion_jobs",
    "memberships",
    "messages",
    "pages",
    "refresh_tokens",
    "users",
    "verification_results",
    "workspaces",
}

WORKSPACE_OWNED_TABLES = {
    "chunk_embeddings",
    "chunks",
    "conversations",
    "documents",
    "ingestion_jobs",
}


def test_all_initial_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_tenant_tables_declare_workspace_ownership() -> None:
    for name in WORKSPACE_OWNED_TABLES:
        column = Base.metadata.tables[name].columns["workspace_id"]
        assert not column.nullable, name
        targets = {fk.target_fullname for fk in column.foreign_keys}
        assert targets == {"workspaces.id"}, name


def test_constraint_names_follow_the_naming_convention() -> None:
    users = Base.metadata.tables["users"]
    assert users.primary_key.name == "pk_users"


def test_audit_logs_are_append_only_shaped() -> None:
    audit = Base.metadata.tables["audit_logs"]
    assert "updated_at" not in audit.columns
    assert audit.columns["workspace_id"].nullable
    assert audit.columns["actor_user_id"].nullable
