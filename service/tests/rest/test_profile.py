# POST /user/me 信封端点穷举(P5 REST 加密信封首个消费者,见 docs/auth.md §加密信道 / changes/0062)。
# 内存 sqlite + 直接 await handler(同 test_login;httpx/TestClient 未装)。客户端侧用 derive_rest_keys +
# seal_envelope/open_envelope 模拟封拆。覆盖:happy path(解密响应 + seq 回显 + 字段对 DB)、重放 401、
# 窗内乱序双帧都收、未知/过期 sid 401、坏 hex/伪 MAC/ws 域帧注入 401、非 dict 内层 401、DB 错 500、行缺失 500、路由注册。

import json

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool

from app.auth.channel import derive_keys, derive_rest_keys, open_envelope, seal_envelope
from app.auth.session import SessionStore
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.rest.profile import make_profile_router
from app.rest.secure import SecureRequest

_T0 = 1_000_000.0
_TTL = 3600
_MAX = 65536


async def _setup():
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            s.add(User(id=1, nickname="Alice", points=888, name="alice"))  # 资料查询不需要 hash/k_user 列
    return sm


def _endpoint(router):
    routes = [r for r in router.routes if getattr(r, "path", None) == "/user/me"]
    assert len(routes) == 1, "/user/me 路由应恰好注册一条"
    return routes[0].endpoint


def _seal_req(session, seq: int, params: dict | None = None, *, keys=derive_rest_keys) -> str:
    # 客户端侧封请求:REST 域密钥 + 调用方给 seq(模拟客户端计数器)。keys 可换 derive_keys 模拟跨信道注入。
    enc, mac = keys(session.token)
    return seal_envelope(enc, mac, seq, json.dumps({} if params is None else params).encode()).hex()


def _open_resp(session, resp) -> tuple[int, dict]:
    # 客户端侧拆响应:返回 (seq, payload);调用方断言 seq == 请求 seq(绑定)。
    enc, mac = derive_rest_keys(session.token)
    seq, plaintext = open_envelope(enc, mac, bytes.fromhex(resp.frame), _MAX)
    return seq, json.loads(plaintext)


async def _me(store, sm, sid: str, frame_hex: str, now: float = _T0):
    router = make_profile_router(lambda: sm, store, now=lambda: now)
    return await _endpoint(router)(SecureRequest(sid=sid, frame=frame_hex))


async def test_happy_path_returns_sealed_profile_with_seq_echo():
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    resp = await _me(store, sm, sid, _seal_req(session, 1))
    seq, data = _open_resp(session, resp)
    assert seq == 1  # 响应 seq 回显请求 seq(请求-响应绑定)
    assert data == {"name": "alice", "nickname": "Alice", "points": 888}  # 字段对 DB 行


async def test_replayed_frame_rejected_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    frame = _seal_req(session, 1)
    await _me(store, sm, sid, frame)  # 首投:成功
    with pytest.raises(HTTPException) as ei:
        await _me(store, sm, sid, frame)  # 原帧重放:MAC 真、seq 已见 → 窗拒
    assert ei.value.status_code == 401


async def test_out_of_order_frames_both_accepted():
    # 并发/乱序:seq 3 先到、seq 2 后到 → 滑动窗都收(严格单调会误拒;0057 决策 3)。
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    assert _open_resp(session, await _me(store, sm, sid, _seal_req(session, 3)))[0] == 3
    assert _open_resp(session, await _me(store, sm, sid, _seal_req(session, 2)))[0] == 2


async def test_unknown_sid_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    _, session = store.create("alice", "Alice", _T0)
    with pytest.raises(HTTPException) as ei:
        await _me(store, sm, "bogus", _seal_req(session, 1))
    assert ei.value.status_code == 401


async def test_expired_session_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    with pytest.raises(HTTPException) as ei:
        await _me(store, sm, sid, _seal_req(session, 1), now=_T0 + _TTL + 1)  # 过期:lookup 删并拒
    assert ei.value.status_code == 401


async def test_bad_frame_hex_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, _ = store.create("alice", "Alice", _T0)
    with pytest.raises(HTTPException) as ei:
        await _me(store, sm, sid, "not-hex!!")
    assert ei.value.status_code == 401


async def test_tampered_mac_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    frame = bytearray(bytes.fromhex(_seal_req(session, 1)))
    frame[-1] ^= 0xFF  # 改 mac 末字节 → bad_mac
    with pytest.raises(HTTPException) as ei:
        await _me(store, sm, sid, bytes(frame).hex())
    assert ei.value.status_code == 401


async def test_ws_domain_frame_injected_401():
    # 跨信道注入:用 **ws 域密钥** 封的帧投 REST → 密钥分域 → bad_mac → 401(changes/0062 决策 1)。
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    with pytest.raises(HTTPException) as ei:
        await _me(store, sm, sid, _seal_req(session, 1, keys=derive_keys))
    assert ei.value.status_code == 401


async def test_non_dict_payload_401():
    # 内层明文是合法 JSON 但非对象形(列表)→ bad_payload_shape → 401(端点参数一律 dict)。
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    enc, mac = derive_rest_keys(session.token)
    frame = seal_envelope(enc, mac, 1, b"[1,2]").hex()
    with pytest.raises(HTTPException) as ei:
        await _me(store, sm, sid, frame)
    assert ei.value.status_code == 401


async def test_oversized_frame_rejected_401():
    # REST_FRAME_MAX_BYTES 端到端消费:超上限的真信封(MAC 也真)→ open_envelope frame_too_large → 401。
    from app import gameconfig

    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    big = _seal_req(session, 1, {"pad": "x" * gameconfig.REST_FRAME_MAX_BYTES})  # 内层即超限 → 帧必超
    assert len(bytes.fromhex(big)) > gameconfig.REST_FRAME_MAX_BYTES
    with pytest.raises(HTTPException) as ei:
        await _me(store, sm, sid, big)
    assert ei.value.status_code == 401


async def test_replay_window_width_from_gameconfig():
    # REST_REPLAY_WINDOW 端到端消费:先投高 seq 推进窗,恰滑出窗(seq ≤ top-宽度)拒、窗内沿(top-宽度+1)收。
    from app import gameconfig

    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)
    top = gameconfig.REST_REPLAY_WINDOW + 10
    await _me(store, sm, sid, _seal_req(session, top))  # 推进 top
    with pytest.raises(HTTPException) as ei:
        await _me(store, sm, sid, _seal_req(session, top - gameconfig.REST_REPLAY_WINDOW))  # 恰滑出:太旧拒
    assert ei.value.status_code == 401
    resp = await _me(store, sm, sid, _seal_req(session, top - gameconfig.REST_REPLAY_WINDOW + 1))  # 窗内沿:收
    assert _open_resp(session, resp)[0] == top - gameconfig.REST_REPLAY_WINDOW + 1


async def test_db_error_returns_500_not_401():
    # 信封已验过(已认证)后的 DB 错 → 如实 500(非鉴权问题,客户端不必重登);与信封失败的 401 两段式区分。
    store = SessionStore(_TTL)
    sid, session = store.create("alice", "Alice", _T0)

    def _raising_sessionmaker():
        raise RuntimeError("db down")

    router = make_profile_router(_raising_sessionmaker, store, now=lambda: _T0)
    with pytest.raises(HTTPException) as ei:
        await _endpoint(router)(SecureRequest(sid=sid, frame=_seal_req(session, 1)))
    assert ei.value.status_code == 500


async def test_session_without_db_row_500():
    # 会话在、DB 无该 name 行(内部不一致)→ 500(信封已验过,非鉴权问题)。
    sm = await _setup()
    store = SessionStore(_TTL)
    sid, session = store.create("ghost", "Ghost", _T0)  # DB 只有 alice
    with pytest.raises(HTTPException) as ei:
        await _me(store, sm, sid, _seal_req(session, 1))
    assert ei.value.status_code == 500


def test_create_app_registers_profile_route():
    from app.shell.lifespan import create_app

    app = create_app()
    routes = [r for r in app.routes if getattr(r, "path", None) == "/user/me"]
    assert len(routes) == 1 and "POST" in routes[0].methods
