"""clone targets (.clone / .stopc)

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-09-07 02:35:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cloned_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_user_id", "chat_id", "target_user_id", name="uq_clone_target"),
    )
    op.create_index("ix_cloned_users_owner_user_id", "cloned_users", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_cloned_users_owner_user_id", table_name="cloned_users")
    op.drop_table("cloned_users")
