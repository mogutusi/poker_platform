# Alembic 环境(重构后:重定向到新架构 app/db/ 模型)。用法见 docs/db-migrations.md。
#
# 与原型 env.py 的差异(原型已于 0027 拆除):① DATABASE_URL 经 app.config.settings 读(env > .env;0045
# 收编),仍**免 .env 也能跑迁移**——`settings.DATABASE_URL` 有默认(None),缺 .env 不崩(原「不依赖会崩的
# Settings」之意图,换实现达成;`DATABASE_URL=… alembic upgrade head` 经 os.environ 优先仍覆盖);② 只 import
# app.db.models(显式,单一事实源),不 os.walk 全仓 *models*;③ 不跳过外键(新架构要真 FK,见 db.md);
# ④ render_as_batch 让 sqlite 也能 ALTER(postgres 无害)。

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from alembic import context
from app.config import settings  # 基础设施配置单一事实源(DATABASE_URL);headless 约束见 app/config.py

import app.db.models  # noqa: F401  仅此一行注册新架构表到 SQLModel.metadata(单一事实源)

config = context.config
# 生产设 DATABASE_URL=postgresql+psycopg://…;本地缺省 sqlite(同步驱动),免 .env 也能跑迁移(见 docs/db-migrations.md)。
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL or "sqlite:///./poker.db")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    # 离线:不连库,只据 metadata 出 SQL(`alembic upgrade head --sql`)。
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # 在线:连库执行迁移(`alembic upgrade head` / autogenerate 比对)。
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # sqlite ALTER 走 batch 重建;postgres 无害
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
