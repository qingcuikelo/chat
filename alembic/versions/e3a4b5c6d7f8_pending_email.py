"""pending email change

Revision ID: e3a4b5c6d7f8
Revises: c1e2f3a4b5d6
Create Date: 2026-10-27

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3a4b5c6d7f8"
down_revision: Union[str, Sequence[str], None] = "c1e2f3a4b5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users", sa.Column("pending_email", sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "pending_email")
