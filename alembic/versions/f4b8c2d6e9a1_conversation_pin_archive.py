"""conversation pin and archive

Revision ID: f4b8c2d6e9a1
Revises: a7c9e1f2b3d4
Create Date: 2026-10-26

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "f4b8c2d6e9a1"
down_revision: Union[str, Sequence[str], None] = "a7c9e1f2b3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ARCHIVED_AT = sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")
_NEW_INDEX = "ix_conversation_user_id_is_archived_is_pinned_updated_at_id"
_OLD_INDEX = "ix_conversation_user_id_updated_at_id"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "conversation",
        sa.Column("is_pinned", sa.Boolean(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "conversation",
        sa.Column("is_archived", sa.Boolean(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("conversation", sa.Column("archived_at", _ARCHIVED_AT, nullable=True))
    op.create_index(
        _NEW_INDEX,
        "conversation",
        ["user_id", "is_archived", "is_pinned", "updated_at", "id"],
        unique=False,
    )
    op.drop_index(_OLD_INDEX, table_name="conversation")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index(
        _OLD_INDEX,
        "conversation",
        ["user_id", "updated_at", "id"],
        unique=False,
    )
    op.drop_index(_NEW_INDEX, table_name="conversation")
    op.drop_column("conversation", "archived_at")
    op.drop_column("conversation", "is_archived")
    op.drop_column("conversation", "is_pinned")
