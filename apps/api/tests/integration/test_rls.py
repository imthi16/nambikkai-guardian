"""Row-level security must isolate tenants beneath the repository layer.

PostgreSQL superusers bypass RLS, and the local bootstrap user is a
superuser, so these tests provision a dedicated database and connect with a
non-superuser probe role — the shape a deployed application role must have.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from app.db.models.documents import Document
from app.db.models.identity import User, Workspace
from app.db.session import bind_workspace
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration import factories
from tests.integration.dbtools import alembic, provision_database, scalar

RLS_DB = "attest_rls_test"
PROBE_ROLE = "attest_rls_probe"
PROBE_PASSWORD = "rls-probe-only"  # noqa: S105 - throwaway local test role
TENANT_TABLES = ("documents", "chunks", "chunk_embeddings", "conversations", "ingestion_jobs")


@dataclass(frozen=True)
class SeededTenants:
    admin_url: str
    probe_url: str
    workspace_one: uuid.UUID
    workspace_two: uuid.UUID
    owner_id: uuid.UUID


@pytest.fixture(scope="module")
def tenants() -> SeededTenants:
    """A migrated database with two workspaces, one document each, and a probe role."""
    admin_url = provision_database(RLS_DB)
    result = alembic(["upgrade", "head"], admin_url)
    assert result.returncode == 0, result.stderr

    async def _seed() -> SeededTenants:
        engine = create_async_engine(admin_url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session, session.begin():
                owner = await factories.make_user(session)
                workspace_one = await factories.make_workspace(session, owner)
                workspace_two = await factories.make_workspace(session, owner)
                await factories.make_document(session, workspace_one, owner)
                await factories.make_document(session, workspace_two, owner)
                seeded = SeededTenants(
                    admin_url=admin_url,
                    probe_url=make_url(admin_url)
                    .set(username=PROBE_ROLE, password=PROBE_PASSWORD)
                    .render_as_string(hide_password=False),
                    workspace_one=workspace_one.id,
                    workspace_two=workspace_two.id,
                    owner_id=owner.id,
                )
            async with engine.connect() as connection:
                await connection.execute(
                    text(
                        f"""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT FROM pg_roles WHERE rolname = '{PROBE_ROLE}'
                            ) THEN
                                CREATE ROLE {PROBE_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS;
                            END IF;
                        END
                        $$
                        """
                    )
                )
                await connection.execute(
                    text(f"ALTER ROLE {PROBE_ROLE} LOGIN PASSWORD '{PROBE_PASSWORD}'")
                )
                await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {PROBE_ROLE}"))
                await connection.execute(
                    text(
                        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                        f"IN SCHEMA public TO {PROBE_ROLE}"
                    )
                )
                await connection.commit()
            return seeded
        finally:
            await engine.dispose()

    import asyncio

    return asyncio.run(_seed())


@pytest.fixture
async def probe_session(tenants: SeededTenants) -> AsyncIterator[AsyncSession]:
    """A non-superuser session whose work is rolled back."""
    engine = create_async_engine(tenants.probe_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def test_policies_cover_every_tenant_table(tenants: SeededTenants) -> None:
    tables = ", ".join(f"'{name}'" for name in TENANT_TABLES)
    covered = await scalar(
        tenants.admin_url,
        "SELECT count(DISTINCT tablename) FROM pg_policies "  # noqa: S608 - constant names
        f"WHERE tablename IN ({tables})",
    )
    assert covered == len(TENANT_TABLES)


async def test_unbound_session_sees_and_writes_nothing(
    probe_session: AsyncSession,
    tenants: SeededTenants,
) -> None:
    visible = await probe_session.scalar(select(func.count()).select_from(Document))
    assert visible == 0

    workspace = await probe_session.get(Workspace, tenants.workspace_one)
    owner = await probe_session.get(User, tenants.owner_id)
    assert workspace is not None and owner is not None
    with pytest.raises(ProgrammingError, match="row-level security"):
        await factories.make_document(probe_session, workspace, owner)


async def test_bound_session_is_fenced_to_its_workspace(
    probe_session: AsyncSession,
    tenants: SeededTenants,
) -> None:
    await bind_workspace(probe_session, tenants.workspace_one)

    rows = (await probe_session.scalars(select(Document))).all()
    assert [row.workspace_id for row in rows] == [tenants.workspace_one]

    # Raw UPDATE aimed at the other tenant touches nothing.
    await probe_session.execute(
        update(Document)
        .where(Document.workspace_id == tenants.workspace_two)
        .values(title="defaced")
    )
    defaced = await probe_session.scalar(
        select(func.count()).select_from(Document).where(Document.title == "defaced")
    )
    assert defaced == 0

    workspace_one = await probe_session.get(Workspace, tenants.workspace_one)
    owner = await probe_session.get(User, tenants.owner_id)
    assert workspace_one is not None and owner is not None
    created = await factories.make_document(probe_session, workspace_one, owner)
    assert created.workspace_id == tenants.workspace_one


async def test_bound_session_cannot_write_into_another_workspace(
    probe_session: AsyncSession,
    tenants: SeededTenants,
) -> None:
    await bind_workspace(probe_session, tenants.workspace_one)
    workspace_two = await probe_session.get(Workspace, tenants.workspace_two)
    owner = await probe_session.get(User, tenants.owner_id)
    assert workspace_two is not None and owner is not None
    with pytest.raises(ProgrammingError, match="row-level security"):
        await factories.make_document(probe_session, workspace_two, owner)
