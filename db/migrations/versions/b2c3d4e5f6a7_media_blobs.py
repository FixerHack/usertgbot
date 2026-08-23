"""media_blobs

Revision ID: b2c3d4e5f6a7
Revises: a1c2e3d4f5b6
Create Date: 2026-08-22 00:10:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1c2e3d4f5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_blobs",
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("purpose", sa.String(), primary_key=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("mime", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("media_blobs")
