"""Per-chunk dense embeddings with pgvector, tenant isolation, and an ANN index.

Adds `chunk_embeddings` (one vector per chunk per model version), a denormalized
`workspace_id` for pre-score tenant filtering, row-level security matching the
other tenant tables, and an IVFFlat cosine index for approximate search.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 1024

# Mirror the tenant-isolation predicate used in 0003 for the other tables.
_PREDICATE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name="fk_chunk_embeddings_chunk_id_chunks",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_chunk_embeddings_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chunk_embeddings"),
        sa.UniqueConstraint(
            "chunk_id",
            "model",
            "model_version",
            name="uq_chunk_embeddings_chunk_id_model_model_version",
        ),
    )
    op.create_index(
        "ix_chunk_embeddings_chunk_id", "chunk_embeddings", ["chunk_id"]
    )
    op.create_index(
        "ix_chunk_embeddings_workspace_id", "chunk_embeddings", ["workspace_id"]
    )
    # Approximate-nearest-neighbor index for cosine distance. IVFFlat needs
    # data to train its lists well; it is created empty here and can be
    # reindexed after backfill. Retrieval may also fall back to exact scan.
    op.create_index(
        "ix_chunk_embeddings_embedding_cosine",
        "chunk_embeddings",
        ["embedding"],
        postgresql_using="ivfflat",
        postgresql_with={"lists": 100},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.execute("ALTER TABLE chunk_embeddings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chunk_embeddings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY chunk_embeddings_tenant_isolation ON chunk_embeddings "
        f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY chunk_embeddings_tenant_isolation ON chunk_embeddings")
    op.execute("ALTER TABLE chunk_embeddings NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chunk_embeddings DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_chunk_embeddings_embedding_cosine", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_workspace_id", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_chunk_id", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
