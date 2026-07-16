# Migrations

Alembic migrations for the PostgreSQL schema. The environment (`env.py`) loads
model metadata from `apps/api/app/db/models` and resolves the database URL from
`DATABASE_URL` or the application settings — never from a checked-in file.

## Commands (from the repository root)

- `make migrate-up` — apply migrations to head.
- `make migrate-down` — revert the most recent migration.
- `make migrate-new m="describe change"` — autogenerate a new revision against
  the running database; always review the generated operations.

Every migration must include a tested downgrade or a documented irreversible
decision. The integration suite (`apps/api/tests/integration/test_migrations.py`)
runs upgrade → `alembic check` (no model/schema drift) → downgrade → upgrade on
every CI run.

Row-level-security tenant policies are planned to land with the authentication
and hardening issues; until then tenant isolation is enforced by explicit
`workspace_id` ownership columns and the workspace-scoped repository layer.
