# Alembic 环境(重构后:重定向到新架构 app/db/ 模型)。用法见 docs/db-migrations.md。
#
# 与原型 env.py 的差异:① 不 import app.config(它读 .env、无则崩)——DATABASE_URL 从 os.environ 读、
# 缺省本地 sqlite,免 .env 也能跑迁移;② 只 import app.db.models(显式),不再 os.walk 全仓 *models*,
# 避免把原型模型注册进 SQLModel.metadata 造表名冲突;③ 不跳过外键(新架构要真 FK,见 db.md);④ render_as_batch
# 让 sqlite 也能 ALTER(postgres 无害)。

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from alembic import context

import app.db.models  # noqa: F401  仅此一行注册新架构表到 SQLModel.metadata(不碰原型模型 / 不 import app.config)

config = context.config
# 生产设 DATABASE_URL=postgresql+psycopg://…;本地缺省 sqlite,免 .env 也能跑迁移(见 docs/db-migrations.md)。
config.set_main_option("sqlalchemy.url", os.environ.get("DATABASE_URL", "sqlite:///./poker.db"))

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
