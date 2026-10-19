"""hand action log for stats (0111)

Revision ID: d4b2a9c1e311
Revises: c3a1f0e7d210
Create Date: 2026-10-16 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4b2a9c1e311'
down_revision: Union[str, Sequence[str], None] = 'c3a1f0e7d210'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'handactionlog',
        sa.Column('hand_id', sa.Integer(), nullable=False),
        sa.Column('actions', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['hand_id'], ['handrecord.id']),
        sa.PrimaryKeyConstraint('hand_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('handactionlog')
