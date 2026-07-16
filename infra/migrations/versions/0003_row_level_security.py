"""Row-level security on tenant-owned tables.

Policies key on the transaction-local `app.workspace_id` setting (see
`app.db.session.bind_workspace`). `FORCE` makes the table owner subject to
the policies too; note that PostgreSQL superusers always bypass RLS, so the
deployed application must connect as a non-superuser role for these policies
to bite. Repository-layer workspace scoping remains the first enforcement
line; RLS is defense in depth beneath it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("documents", "chunks", "conversations", "ingestion_jobs")

# NULLIF guards the cast: an unset or reset setting reads as NULL (or ''),
# which fails USING (no rows visible) and WITH CHECK (writes rejected).
_PREDICATE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
        )


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
