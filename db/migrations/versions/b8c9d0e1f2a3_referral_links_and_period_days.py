"""referral_links, users.referred_by_link_id, subscriptions.period_days

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-28 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "referral_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_referral_links_code", "referral_links", ["code"], unique=True)

    op.add_column("users", sa.Column("referred_by_link_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_users_referred_by_link_id", "users", "referral_links",
        ["referred_by_link_id"], ["id"], ondelete="SET NULL",
    )

    op.add_column(
        "subscriptions",
        sa.Column("period_days", sa.Integer(), nullable=False, server_default="30"),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "period_days")
    op.drop_constraint("fk_users_referred_by_link_id", "users", type_="foreignkey")
    op.drop_column("users", "referred_by_link_id")
    op.drop_index("ix_referral_links_code", table_name="referral_links")
    op.drop_table("referral_links")
