# async engine + session 工厂(P4 三之二)。DATABASE_URL 从 os.environ 读、缺省本地 sqlite+aiosqlite。
# OrmPersister 持本模块产的 sessionmaker(自有 session,不复用请求级注入,见 db.md「事务分组 & session」)。
# DATABASE_URL 统一进 app/config 是 P8「配置收编」的事(现与 alembic/env.py 一样直读 os.environ)。

import os

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

# 缺省本地 sqlite(dev/测试);postgres 走 postgresql+psycopg://(psycopg v3 异步,无需 asyncpg)。
DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./poker.db"


def database_url() -> str:
    # 同 alembic/env.py:从环境变量读、缺省本地 sqlite,免 .env 也能跑(见 docs/dev.md)。
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str | None = None, **kwargs) -> AsyncEngine:
    # 缺 url 取 database_url();kwargs 透传 create_async_engine(测试传 StaticPool 共享内存库)。
    engine = create_async_engine(url or database_url(), **kwargs)
    if engine.dialect.name == "sqlite":
        # sqlite 默认不强制外键,每连接需 PRAGMA foreign_keys=ON——使其与 postgres 一致地强制 FK
        # (HandParticipant.uid→user.id / hand_id→handrecord.id,见 db.md);否则脏 FK 静默写入。
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False:commit 后不主动过期对象、省一次 refresh 往返;本写路径 commit 后不再读对象。
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    # 按 ORM metadata 建全表——仅测试 / 无 Alembic 的 dev 引导用;生产由 Alembic 迁移建表,不调本函数。
    import app.db.models  # noqa: F401  确保三张表已注册进 SQLModel.metadata(再 create_all)
    from sqlmodel import SQLModel

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
