"""user token version

Revision ID: c1e2f3a4b5d6
Revises: f4b8c2d6e9a1
Create Date: 2026-10-27

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1e2f3a4b5d6"
down_revision: Union[str, Sequence[str], None] = "f4b8c2d6e9a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "token_version", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "token_version")
