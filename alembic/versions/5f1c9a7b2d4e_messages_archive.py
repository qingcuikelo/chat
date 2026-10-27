"""messages archive

Revision ID: 5f1c9a7b2d4e
Revises: 9c2f4b7a1d3e
Create Date: 2026-10-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5f1c9a7b2d4e'
down_revision: Union[str, Sequence[str], None] = '9c2f4b7a1d3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_default():
    if op.get_bind().dialect.name == "sqlite":
        return sa.text("CURRENT_TIMESTAMP")
    return sa.text("now()")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'messages_archive',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('token_count', sa.Integer(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=_timestamp_default(),
            nullable=False,
        ),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index(
        'ix_messages_archive_conversation_id_id',
        'messages_archive',
        ['conversation_id', 'id'],
        unique=False,
    )
    op.create_index(
        'ix_messages_archive_created_at',
        'messages_archive',
        ['created_at'],
        unique=False,
    )
    op.create_index(
        'ix_messages_created_at_id',
        'messages',
        ['created_at', 'id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_messages_created_at_id', table_name='messages')
    op.drop_index(
        'ix_messages_archive_created_at', table_name='messages_archive'
    )
    op.drop_index(
        'ix_messages_archive_conversation_id_id', table_name='messages_archive'
    )
    op.drop_table('messages_archive')
