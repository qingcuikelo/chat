"""composite indexes and drop redundant

Revision ID: 95d5a5e43a3c
Revises: 7e9ad576ade6
Create Date: 2026-10-02 00:34:07.451948

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95d5a5e43a3c'
down_revision: Union[str, Sequence[str], None] = '7e9ad576ade6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 先建复合索引（最左前缀满足外键要求），再删除被覆盖的单列索引
    op.create_index(
        "ix_conversation_user_id_updated_at_id",
        "conversation",
        ["user_id", "updated_at", "id"],
        unique=False,
    )
    op.drop_index(op.f("ix_conversation_user_id"), table_name="conversation")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index(
        op.f("ix_messages_conversation_id"),
        "messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_user_id"), "conversation", ["user_id"], unique=False
    )
    op.drop_index("ix_conversation_user_id_updated_at_id", table_name="conversation")
