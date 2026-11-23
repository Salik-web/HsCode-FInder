"""tariff_codes: full-text search column and the text recipe behind each embedding.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tariff_codes",
        sa.Column(
            "search_vector",
            TSVECTOR(),
            sa.Computed("to_tsvector('english', path)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_tariff_codes_search_vector",
        "tariff_codes",
        ["search_vector"],
        postgresql_using="gin",
    )
    # Rows loaded before this column embedded the full path.
    op.add_column(
        "tariff_codes",
        sa.Column("embedding_text", sa.String(32), nullable=False, server_default="path"),
    )
    op.alter_column("tariff_codes", "embedding_text", server_default=None)


def downgrade() -> None:
    op.drop_column("tariff_codes", "embedding_text")
    op.drop_index("ix_tariff_codes_search_vector", table_name="tariff_codes")
    op.drop_column("tariff_codes", "search_vector")
