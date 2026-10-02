"""user email verification

Revision ID: 9c2f4b7a1d3e
Revises: 95d5a5e43a3c
Create Date: 2026-10-02 13:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "9c2f4b7a1d3e"
down_revision: Union[str, Sequence[str], None] = "95d5a5e43a3c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "email_verified", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "users", sa.Column("email_verified_at", mysql.DATETIME(fsp=6), nullable=True)
    )
    # 既有账号视为已验证，避免被强制验证锁死
    op.execute(
        "UPDATE users SET email_verified = 1, email_verified_at = updated_at"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email_verified")
