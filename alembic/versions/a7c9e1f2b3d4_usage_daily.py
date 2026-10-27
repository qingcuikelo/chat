"""usage daily

Revision ID: a7c9e1f2b3d4
Revises: 5f1c9a7b2d4e
Create Date: 2026-10-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c9e1f2b3d4'
down_revision: Union[str, Sequence[str], None] = '5f1c9a7b2d4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamp_default():
    if op.get_bind().dialect.name == "sqlite":
        return sa.text("CURRENT_TIMESTAMP")
    return sa.text("now()")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'usage_daily',
        sa.Column(
            'id',
            sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('prompt_tokens', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column(
            'completion_tokens', sa.BigInteger(), server_default='0', nullable=False
        ),
        sa.Column('request_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=_timestamp_default(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'usage_date', name='uq_usage_daily_user_date'
        ),
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('usage_daily')
