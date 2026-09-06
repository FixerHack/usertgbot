"""chat recordings (.save / .unsave)

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-07 02:20:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_recordings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_title", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_chat_recordings_owner_user_id", "chat_recordings", ["owner_user_id"])
    op.create_index("ix_chat_recordings_chat_id", "chat_recordings", ["chat_id"])

    op.create_table(
        "recorded_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "recording_id",
            sa.Integer(),
            sa.ForeignKey("chat_recordings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("sender", sa.String(), nullable=False),
        sa.Column("is_outgoing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("text", sa.String(), nullable=False, server_default=""),
    )
    op.create_index("ix_recorded_messages_recording_id", "recorded_messages", ["recording_id"])


def downgrade() -> None:
    op.drop_index("ix_recorded_messages_recording_id", table_name="recorded_messages")
    op.drop_table("recorded_messages")
    op.drop_index("ix_chat_recordings_chat_id", table_name="chat_recordings")
    op.drop_index("ix_chat_recordings_owner_user_id", table_name="chat_recordings")
    op.drop_table("chat_recordings")
