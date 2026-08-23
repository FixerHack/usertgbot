"""rename search_usage -> check_usage

The quota-limited command is `.check` (there is no `.search`).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-22 00:20:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("search_usage", "check_usage")


def downgrade() -> None:
    op.rename_table("check_usage", "search_usage")
