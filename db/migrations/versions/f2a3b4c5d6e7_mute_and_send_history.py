"""mute list + .send hourly history

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-07 00:40:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("send_history", sa.JSON(), nullable=True))

    op.create_table(
        "muted_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_user_id", "chat_id", "target_user_id", name="uq_mute_target"),
    )
    op.create_index("ix_muted_users_owner_user_id", "muted_users", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_muted_users_owner_user_id", table_name="muted_users")
    op.drop_table("muted_users")
    op.drop_column("user_settings", "send_history")
