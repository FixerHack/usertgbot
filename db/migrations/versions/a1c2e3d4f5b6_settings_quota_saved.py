"""user_settings, search_usage, saved_messages

Revision ID: a1c2e3d4f5b6
Revises: 34f1b9b2a5a8
Create Date: 2026-08-22 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c2e3d4f5b6"
down_revision: Union[str, None] = "34f1b9b2a5a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("me_card", sa.JSON(), nullable=True),
        sa.Column("autoresponder", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "search_usage",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
    )

    op.create_table(
        "saved_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=True),
        sa.Column("previous_text", sa.String(), nullable=True),
        sa.Column("saved_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_saved_messages_owner_user_id", "saved_messages", ["owner_user_id"])
    op.create_index("ix_saved_owner_chat", "saved_messages", ["owner_user_id", "chat_id"])


def downgrade() -> None:
    op.drop_table("saved_messages")
    op.drop_table("search_usage")
    op.drop_table("user_settings")
