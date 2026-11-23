"""Public demo: sample documents, hashed uploader ids, and an upload log for daily caps.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("is_sample", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("documents", "is_sample", server_default=None)
    op.add_column("documents", sa.Column("uploader", sa.String(64), nullable=True))
    op.create_index("ix_documents_uploader", "documents", ["uploader"])
    # Uploads are purged after an hour in the demo; the daily caps count this log instead.
    op.create_table(
        "upload_log",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), primary_key=True),
        sa.Column("uploader", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_upload_log_created_at", "upload_log", ["created_at"])
    op.create_index("ix_upload_log_uploader", "upload_log", ["uploader"])


def downgrade() -> None:
    op.drop_table("upload_log")
    op.drop_index("ix_documents_uploader", table_name="documents")
    op.drop_column("documents", "uploader")
    op.drop_column("documents", "is_sample")
