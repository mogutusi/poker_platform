"""hand detail tables: board, hole cards, vpip, showdown (0110)

Revision ID: c3a1f0e7d210
Revises: b8ca88a687af
Create Date: 2026-10-16 02:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # SQLModel 列类型(AutoString)须随模板带入,否则升级时 NameError


# revision identifiers, used by Alembic.
revision: str = 'c3a1f0e7d210'
down_revision: Union[str, Sequence[str], None] = 'b8ca88a687af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 两张新表,不动旧表:0110 之前的历史手牌没有明细行,读侧按「无牌面 / VPIP 不计」处理。
    op.create_table(
        'handdetail',
        sa.Column('hand_id', sa.Integer(), nullable=False),
        sa.Column('board', sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
        sa.Column('button_position', sa.Integer(), nullable=True),
        sa.Column('big_blind', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['hand_id'], ['handrecord.id']),
        sa.PrimaryKeyConstraint('hand_id'),
    )
    op.create_table(
        'handparticipantdetail',
        sa.Column('hand_id', sa.Integer(), nullable=False),
        sa.Column('uid', sa.Integer(), nullable=False),
        sa.Column('seat_position', sa.Integer(), nullable=True),
        sa.Column('hole_cards', sqlmodel.sql.sqltypes.AutoString(length=4), nullable=True),
        sa.Column('vpip', sa.Boolean(), nullable=False),
        sa.Column('showed', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['hand_id'], ['handrecord.id']),
        sa.ForeignKeyConstraint(['uid'], ['user.id']),
        sa.PrimaryKeyConstraint('hand_id', 'uid'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('handparticipantdetail')
    op.drop_table('handdetail')
