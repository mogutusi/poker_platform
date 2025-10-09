from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from app.config import settings
from pathlib import Path
import os
import importlib

from sqlmodel import SQLModel

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# 导入所有模型
def import_models():
    app_path = Path(__file__).parent.parent / "app"
    for root, dirs, files in os.walk(app_path):
        for file in files:
            if file.endswith(".py") and "models" in file:
                # 将文件路径转换为模块路径
                file_path = Path(root) / file
                relative_path = file_path.relative_to(app_path.parent)
                module_path = str(relative_path.with_suffix("")).replace(os.sep, ".")
                importlib.import_module(module_path)
    
import_models()

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


target_metadata = SQLModel.metadata

def render_item(type_, obj, autogen_context):
    """自定义渲染器，跳过外键约束"""
    if type_ == "foreign_key":
        # 返回空字符串表示跳过
        return None

    # 默认渲染
    return False


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()