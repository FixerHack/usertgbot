"""wayforpay: order_reference, auto_renew, payment_events

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-09-04 12:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("order_reference", sa.String(), nullable=True))
    op.create_index(
        "ix_subscriptions_order_reference", "subscriptions", ["order_reference"], unique=True
    )
    op.add_column(
        "subscriptions",
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("subscriptions", sa.Column("amount_uah", sa.Integer(), nullable=True))

    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("order_reference", sa.String(), nullable=False),
        sa.Column("auth_code", sa.String(), nullable=False),
        sa.Column("processing_date", sa.String(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "provider", "order_reference", "auth_code", "processing_date",
            name="uq_payment_event_charge",
        ),
    )
    op.create_index("ix_payment_events_provider", "payment_events", ["provider"])
    op.create_index("ix_payment_events_order_reference", "payment_events", ["order_reference"])


def downgrade() -> None:
    op.drop_index("ix_payment_events_order_reference", table_name="payment_events")
    op.drop_index("ix_payment_events_provider", table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_column("subscriptions", "amount_uah")
    op.drop_column("subscriptions", "auto_renew")
    op.drop_index("ix_subscriptions_order_reference", table_name="subscriptions")
    op.drop_column("subscriptions", "order_reference")
