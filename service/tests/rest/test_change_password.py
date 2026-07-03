# POST /user/password 信封端点穷举(P7 改密码,见 docs/rest.md §用户资料 / changes/0064)。
# 内存 sqlite + 直接 await handler(同 test_profile);客户端侧用 derive_rest_keys 封拆信封。
# 覆盖:happy(改后旧哈希验新密码过、旧不过 + 响应 status:ok)、旧密码错 403 不改库、新密码空 400、
# 缺参/非字符串 400、未启用密码 403、未知/重放 sid 401(信封继承)、DB 错 500、会话 name 无行 500、路由注册。

import json

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool

from app.auth.channel import derive_rest_keys, open_envelope, seal_envelope
from app.auth.passwords import hash_password, verify_password
from app.auth.session import SessionStore
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.rest.profile import make_profile_router
from app.rest.secure import SecureRequest

_T0 = 1_000_000.0
_TTL = 3600
_MAX = 65536
_ROUNDS = 500
_OLD = "old-correct-horse"


async def _setup():
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            s.add(User(id=1, nickname="Alice", points=100, name="alice",
                       hash_password=hash_password(_OLD, _ROUNDS)))  # 已启用密码
            s.add(User(id=2, nickname="NoPw", points=0, name="nopw"))  # name 有、hash NULL:未启用密码
    return sm


def _endpoint(router):
    routes = [r for r in router.routes if getattr(r, "path", None) == "/user/password"]
    assert len(routes) == 1, "/user/password 路由应恰好注册一条"
    return routes[0].endpoint


def _seal_req(session, seq: int, params: dict) -> str:
    enc, mac = derive_rest_keys(session.token)
    return seal_envelope(enc, mac, seq, json.dumps(params).encode()).hex()


def _open_resp(session, resp) -> tuple[int, dict]:
    enc, mac = derive_rest_keys(session.token)
    seq, plaintext = open_envelope(enc, mac, bytes.fromhex(resp.frame), _MAX)
    return seq, json.loads(plaintext)


async def _change(store, sm, sid: str, frame_hex: str, now: float = _T0):
    router = make_profile_router(lambda: sm, store, now=lambda: now)
    return await _endpoint(router)(SecureRequest(sid=sid, frame=frame_hex))


async def _current_hash(sm, uid: int) -> str | None:
    async with sm() as s:
        return (await s.get(User, uid)).hash_password


async def test_happy_path_changes_hash():
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    before = await _current_hash(sm, 1)
    resp = await _change(store, sm, sid, _seal_req(session, 1, {"old_password": _OLD, "new_password": "new-pw-2"}))
    seq, data = _open_resp(session, resp)
    assert seq == 1 and data == {"status": "ok"}  # 响应封回、seq 回显
    after = await _current_hash(sm, 1)
    assert after != before  # 哈希已换(新盐)
    assert verify_password("new-pw-2", after) and not verify_password(_OLD, after)  # 新密码生效、旧作废


async def test_change_password_does_not_clobber_other_columns():
    # 定向列 UPDATE(SET hash_password)不碰 points/nickname/name —— 兑现「与 PersistWriter 列不相交」(0064)。
    # 若退化成整行 merge,points/nickname 会被写 NULL / 默认值(此测杀该回归)。
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    await _change(store, sm, sid, _seal_req(session, 1, {"old_password": _OLD, "new_password": "new-pw-2"}))
    async with sm() as s:
        user = await s.get(User, 1)
        assert user.points == 100 and user.nickname == "Alice" and user.name == "alice"  # 其它列原封


async def test_wrong_old_password_403_no_change():
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    before = await _current_hash(sm, 1)
    with pytest.raises(HTTPException) as ei:
        await _change(store, sm, sid, _seal_req(session, 1, {"old_password": "WRONG", "new_password": "x-new"}))
    assert ei.value.status_code == 403
    assert await _current_hash(sm, 1) == before  # 未改库


async def test_password_not_enabled_403():
    # name 有、hash NULL(未启用密码)→ 无旧密码可验 → 403(不崩,verify 不接触 None)。
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("nopw", "NoPw", _T0)
    with pytest.raises(HTTPException) as ei:
        await _change(store, sm, sid, _seal_req(session, 1, {"old_password": "whatever", "new_password": "x"}))
    assert ei.value.status_code == 403


async def test_empty_new_password_400():
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    with pytest.raises(HTTPException) as ei:
        await _change(store, sm, sid, _seal_req(session, 1, {"old_password": _OLD, "new_password": "   "}))
    assert ei.value.status_code == 400


@pytest.mark.parametrize("params", [
    {"old_password": _OLD},  # 缺 new_password
    {"new_password": "x"},  # 缺 old_password
    {"old_password": 5, "new_password": "x"},  # 非字符串
    {"old_password": _OLD, "new_password": ["x"]},  # 非字符串
])
async def test_malformed_params_400(params):
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    with pytest.raises(HTTPException) as ei:
        await _change(store, sm, sid, _seal_req(session, 1, params))
    assert ei.value.status_code == 400


async def test_unknown_sid_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    _, session = store.create("alice", "Alice", _T0)
    with pytest.raises(HTTPException) as ei:
        await _change(store, sm, "bogus", _seal_req(session, 1, {"old_password": _OLD, "new_password": "x-new"}))
    assert ei.value.status_code == 401


async def test_replayed_frame_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    frame = _seal_req(session, 1, {"old_password": _OLD, "new_password": "x-new"})
    await _change(store, sm, sid, frame)  # 首投成功(改成 x-new)
    with pytest.raises(HTTPException) as ei:
        await _change(store, sm, sid, frame)  # 同 seq 重放 → 窗拒
    assert ei.value.status_code == 401


async def test_change_to_same_password_still_rehashes():
    # 决策 4:新盐重算 → 改成**同密码**也换哈希(杀「复用旧盐」变异——不同明文的 happy path 测不出)。
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    before = await _current_hash(sm, 1)
    await _change(store, sm, sid, _seal_req(session, 1, {"old_password": _OLD, "new_password": _OLD}))
    after = await _current_hash(sm, 1)
    assert after != before and verify_password(_OLD, after)  # 哈希换了(新盐)、密码仍验得过


async def test_write_path_error_500_leaves_hash_unchanged():
    # 写路径失败(load 成功、update 抛)→ 500(区别于 load 失败的 500);且未改库(旧密码仍有效)。
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] >= 2:  # 第 1 次(load)给真 sm,第 2 次(write)抛 → 命中写路径 try/except
            raise RuntimeError("write down")
        return sm

    router = make_profile_router(_flaky, store, now=lambda: _T0)
    with pytest.raises(HTTPException) as ei:
        await _endpoint(router)(
            SecureRequest(sid=sid, frame=_seal_req(session, 1, {"old_password": _OLD, "new_password": "x-new"}))
        )
    assert ei.value.status_code == 500
    assert verify_password(_OLD, await _current_hash(sm, 1))  # 写失败 → 库未改,旧密码仍有效


async def test_db_lookup_error_500():
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)

    def _raising_sessionmaker():
        raise RuntimeError("db down")

    router = make_profile_router(_raising_sessionmaker, store, now=lambda: _T0)
    with pytest.raises(HTTPException) as ei:
        await _endpoint(router)(SecureRequest(sid=sid, frame=_seal_req(session, 1, {"old_password": _OLD, "new_password": "x"})))
    assert ei.value.status_code == 500


async def test_session_name_without_db_row_500():
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("ghost", "Ghost", _T0)  # DB 无 ghost
    with pytest.raises(HTTPException) as ei:
        await _change(store, sm, sid, _seal_req(session, 1, {"old_password": _OLD, "new_password": "x"}))
    assert ei.value.status_code == 500


def test_create_app_registers_change_password_route():
    from app.shell.lifespan import create_app

    app = create_app()
    routes = [r for r in app.routes if getattr(r, "path", None) == "/user/password"]
    assert len(routes) == 1 and "POST" in routes[0].methods
