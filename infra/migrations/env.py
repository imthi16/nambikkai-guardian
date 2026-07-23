"""Alembic environment: async engine, metadata from the API models."""

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))

import app.db.models  # noqa: E402,F401 - registers every table on the metadata
from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Indexes that live only in migrations (not in model metadata) because their
# definitions do not round-trip through Alembic autogenerate. They are excluded
# from comparison so `alembic check` does not report false drift.
_AUTOGENERATE_IGNORED_INDEXES = frozenset({"ix_chunk_embeddings_embedding_cosine"})


def _include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object,
) -> bool:
    """Exclude migration-only ANN indexes from autogenerate comparison."""
    if type_ == "index" and name in _AUTOGENERATE_IGNORED_INDEXES:
        return False
    return True


def _database_url() -> str:
    """Resolve the URL from the environment first, then application settings."""
    return os.environ.get("DATABASE_URL") or get_settings().database_url


def run_migrations_offline() -> None:
    """Emit SQL without a live connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
        await connection.commit()
    await engine.dispose()


def run_migrations_online() -> None:
    """Apply migrations through an async engine."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
