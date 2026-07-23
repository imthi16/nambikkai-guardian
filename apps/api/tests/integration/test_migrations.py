"""The migration chain must upgrade, match the models, and downgrade cleanly."""

import asyncio

from tests.integration.dbtools import MIGRATION_TEST_DB, alembic, provision_database, scalar

TABLE_COUNT_SQL = "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
ENUM_COUNT_SQL = "SELECT count(*) FROM pg_type WHERE typtype = 'e'"
VECTOR_EXTENSION_SQL = "SELECT count(*) FROM pg_extension WHERE extname = 'vector'"


def test_migrations_upgrade_check_downgrade_and_reupgrade() -> None:
    url = provision_database(MIGRATION_TEST_DB)

    upgrade = alembic(["upgrade", "head"], url)
    assert upgrade.returncode == 0, upgrade.stderr
    # 15 model tables + alembic_version
    assert asyncio.run(scalar(url, TABLE_COUNT_SQL)) == 16
    assert asyncio.run(scalar(url, VECTOR_EXTENSION_SQL)) == 1

    # The migration must exactly express the current models: autogenerate
    # against the migrated schema must find nothing to do.
    check = alembic(["check"], url)
    assert check.returncode == 0, f"schema drift detected:\n{check.stdout}\n{check.stderr}"

    downgrade = alembic(["downgrade", "base"], url)
    assert downgrade.returncode == 0, downgrade.stderr
    assert asyncio.run(scalar(url, TABLE_COUNT_SQL)) == 1  # alembic_version remains
    assert asyncio.run(scalar(url, ENUM_COUNT_SQL)) == 0

    reupgrade = alembic(["upgrade", "head"], url)
    assert reupgrade.returncode == 0, reupgrade.stderr
    assert asyncio.run(scalar(url, TABLE_COUNT_SQL)) == 16
