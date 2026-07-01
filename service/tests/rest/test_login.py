# POST /user/login 端点穷举(P5,见 docs/auth.md §登录握手 / changes/0059)。
# 内存 sqlite(StaticPool)+ create_all + 种 login-enabled 用户;httpx/TestClient 未装 → 直接 await handler。
# 覆盖:正路(响应 K_user 解密得 session_id/token/exp、会话登记且 token/name/nickname 一致)、错密码/未知账号/
# legacy(name=NULL)/错 K_user blob/坏 iv hex 一律 401、失败不铸会话、create_app 挂路由。

import json
import secrets

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from ttxsgm import sm4_cbc_dec, sm4_cbc_enc

from app.auth.passwords import hash_password
from app.auth.session import SessionStore
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.rest.login import LoginRequest, make_login_router

_ROUNDS = 500
_T0 = 1_000_000.0
_TTL = 3600
_PW = "correct horse battery staple"
_KUSER = secrets.token_bytes(16)


async def _setup():
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            s.add(User(id=1, nickname="Alice", points=1000, name="alice",
                       hash_password=hash_password(_PW, _ROUNDS), k_user=_KUSER.hex()))
            s.add(User(id=2, nickname="Legacy", points=0))  # name/hash/k_user NULL:未启用登录
    return sm


def _endpoint(router):
    routes = [r for r in router.routes if getattr(r, "path", None) == "/user/login"]
    assert len(routes) == 1, "login 路由应恰好注册一条"
    return routes[0].endpoint


def _make_blob(payload: dict, key: bytes = _KUSER) -> tuple[str, str]:
    iv = secrets.token_bytes(16)
    return iv.hex(), sm4_cbc_enc(key, iv, json.dumps(payload).encode()).hex()


async def _login(store, sm, name, iv_hex, blob_hex, now=_T0):
    router = make_login_router(lambda: sm, store, now=lambda: now)
    return await _endpoint(router)(LoginRequest(name=name, iv=iv_hex, blob=blob_hex))


async def test_happy_path_issues_k_user_encrypted_session():
    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n1"})
    resp = await _login(store, sm, "alice", iv_hex, blob_hex)
    # 响应用 K_user 解密 → session data
    data = json.loads(sm4_cbc_dec(_KUSER, bytes.fromhex(resp.iv), bytes.fromhex(resp.blob)))
    assert set(data) == {"session_id", "session_token", "exp"}
    assert data["exp"] == _T0 + _TTL
    # 会话已登记、token/name/nickname 一致
    session = store.lookup(data["session_id"], _T0)
    assert session is not None
    assert session.token.hex() == data["session_token"]
    assert (session.name, session.nickname) == ("alice", "Alice")


async def test_wrong_password_401_no_session():
    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": "WRONG", "client_nonce": "n"})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", iv_hex, blob_hex)
    assert ei.value.status_code == 401
    assert len(store) == 0  # 失败不铸会话


async def test_unknown_name_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n"})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "nobody", iv_hex, blob_hex)
    assert ei.value.status_code == 401


async def test_legacy_null_name_user_401():
    # Legacy(name=NULL)→ load_user_for_login 按 name 查不到 → 401(用 nickname "Legacy" 也不匹配 name)。
    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n"})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "Legacy", iv_hex, blob_hex)
    assert ei.value.status_code == 401


async def test_wrong_k_user_blob_401():
    # 用别的 K_user 封 blob → 服务器用登记 K_user 解出乱码 → authenticate None → 401。
    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n"}, key=secrets.token_bytes(16))
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", iv_hex, blob_hex)
    assert ei.value.status_code == 401


async def test_bad_iv_hex_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    _, blob_hex = _make_blob({"password": _PW, "client_nonce": "n"})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", "nothex!!", blob_hex)
    assert ei.value.status_code == 401


async def test_db_error_returns_uniform_401():
    # 基础设施错(DB 查询抛)也归统一 401,不冒成 500 泄「DB 故障 vs 认证失败」之别。
    store = SessionStore(_TTL)

    def _raising_sessionmaker():
        raise RuntimeError("db down")

    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n"})
    router = make_login_router(_raising_sessionmaker, store, now=lambda: _T0)
    with pytest.raises(HTTPException) as ei:
        await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))
    assert ei.value.status_code == 401
    assert len(store) == 0  # 未铸会话


def test_create_app_registers_login_route():
    # 布线:create_app() 注册 POST /user/login(不跑 lifespan,只验路由表)。
    from app.shell.lifespan import create_app

    app = create_app()
    routes = [r for r in app.routes if getattr(r, "path", None) == "/user/login"]
    assert len(routes) == 1 and "POST" in routes[0].methods
