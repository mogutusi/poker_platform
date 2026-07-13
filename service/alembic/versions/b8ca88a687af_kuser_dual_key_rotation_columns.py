"""kuser dual key rotation columns

K_user 双钥轮换扩列(changes/0066,见 docs/auth.md §K_user 每周轮换):
- k_user **重命名** k_cur(autogen 误判成删+加会丢已发密钥,手改 alter_column 保数据,见 docs/db-migrations.md)
- 加 k_cur_ver / k_cur_until / k_prev / k_prev_ver / k_prev_until(均 nullable;until = epoch 秒)
- 回填 k_cur_ver=1(已有钥的行);k_cur_until 留 NULL = 不排程(轮换 cron 不动,issue/rotate --name 才排)

Revision ID: b8ca88a687af
Revises: 49417b108733
Create Date: 2026-07-10 12:30:39.170706

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # SQLModel 列类型(如 AutoString)在自动生成迁移里被引用;须随模板带入,否则升级时 NameError


# revision identifiers, used by Alembic.
revision: str = 'b8ca88a687af'
down_revision: Union[str, Sequence[str], None] = '49417b108733'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('k_user', new_column_name='k_cur')  # 重命名保数据(autogen 给的是删+加)
        batch_op.add_column(sa.Column('k_cur_ver', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('k_cur_until', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('k_prev', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True))
        batch_op.add_column(sa.Column('k_prev_ver', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('k_prev_until', sa.Float(), nullable=True))
    # 既有已发钥的行版本记 1(与首发一致);k_cur_until 留 NULL(不排程,管理员 issue/rotate 时才进排程)
    op.execute("UPDATE \"user\" SET k_cur_ver = 1 WHERE k_cur IS NOT NULL")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('k_prev_until')
        batch_op.drop_column('k_prev_ver')
        batch_op.drop_column('k_prev')
        batch_op.drop_column('k_cur_until')
        batch_op.drop_column('k_cur_ver')
        batch_op.alter_column('k_cur', new_column_name='k_user')  # 对称改回,保数据
