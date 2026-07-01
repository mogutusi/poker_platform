# load_user_for_login 查询穷举(P5,见 docs/auth.md §登录握手 / changes/0056)。
# 内存 sqlite(StaticPool 跨连接存活)+ create_all 建表(含 0056 鉴权列)+ 种子 user;验按 name 载入鉴权投影。

import pytest
from sqlalchemy.pool import StaticPool

from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.db.queries import LoginUser, load_user_for_login


async def _setup():
    # 三类 user:登录已启用(name+hash+k_user)、仅设 name 未设密钥、老行(name=NULL 未启用)。
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            s.add(User(id=1, nickname="Alice", points=1000, name="alice", hash_password="h$1$d", k_user="ab" * 16))
            s.add(User(id=2, nickname="Bob", points=500, name="bob"))  # name 设了但 hash/k_user NULL
            s.add(User(id=3, nickname="Legacy", points=200))  # 老行:name/hash/k_user 全 NULL
    return sm


async def test_load_enabled_user_returns_full_projection():
    sm = await _setup()
    row = await load_user_for_login(sm, "alice")
    assert row == LoginUser(uid=1, name="alice", nickname="Alice", hash_password="h$1$d", k_user="ab" * 16)


async def test_load_returns_named_fields():
    sm = await _setup()
    row = await load_user_for_login(sm, "alice")
    assert row is not None
    assert (row.uid, row.name, row.nickname) == (1, "alice", "Alice")  # NamedTuple 具名访问


async def test_load_name_set_but_secrets_null():
    # name 设了、hash/k_user 未设 → 载入成功但秘密为 None(authenticate 会据此判未启用)。
    sm = await _setup()
    row = await load_user_for_login(sm, "bob")
    assert row is not None and row.hash_password is None and row.k_user is None


async def test_load_unknown_name_returns_none():
    sm = await _setup()
    assert await load_user_for_login(sm, "nobody") is None


async def test_load_by_nickname_does_not_match_name():
    # 按 name 查,不按 nickname;传昵称 "Alice" 查不到(name 是 "alice")。
    sm = await _setup()
    assert await load_user_for_login(sm, "Alice") is None


async def test_legacy_null_name_row_not_loadable():
    # 老行 name=NULL:无法按 name 载入(NULL 不匹配任何值)→ 未启用登录天然不可登。
    sm = await _setup()
    assert await load_user_for_login(sm, "Legacy") is None
    assert await load_user_for_login(sm, "") is None
