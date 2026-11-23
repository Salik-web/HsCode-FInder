"""tariff_codes: PCT codes with pgvector embeddings for HS retrieval.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "tariff_codes",
        sa.Column("code", sa.String(9), primary_key=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("chapter", sa.String(2), nullable=False),
        sa.Column("heading", sa.String(4), nullable=False),
        sa.Column("tariff_version", sa.String(16), nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tariff_codes")
