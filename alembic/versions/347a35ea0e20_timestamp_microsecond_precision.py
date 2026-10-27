"""timestamp microsecond precision

将时间列升级为微秒精度 DATETIME(6)，避免同一秒内时间并列影响排序。

Revision ID: 347a35ea0e20
Revises: 0c280e456d09
Create Date: 2026-09-29 20:29:39.953625

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "347a35ea0e20"
down_revision: Union[str, Sequence[str], None] = "0c280e456d09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TIMESTAMP_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": ("created_at", "updated_at"),
    "conversation": ("created_at", "updated_at"),
    "messages": ("created_at",),
}


def upgrade() -> None:
    """Upgrade schema."""
    if op.get_bind().dialect.name != "mysql":
        return
    for table, columns in _TIMESTAMP_COLUMNS.items():
        for column in columns:
            op.execute(
                f"ALTER TABLE {table} MODIFY {column} "
                f"DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)"
            )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name != "mysql":
        return
    for table, columns in _TIMESTAMP_COLUMNS.items():
        for column in columns:
            op.execute(
                f"ALTER TABLE {table} MODIFY {column} "
                f"DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            )
