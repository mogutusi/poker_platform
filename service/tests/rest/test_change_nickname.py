# POST /user/nickname 信封端点穷举(P7 改昵称,见 docs/rest.md/presence.md / changes/0065)。
# 内存 sqlite + 直接 await handler(同 test_change_password);Presence 用真 world(tests.builders)。
# 覆盖:happy 三处联动(DB/会话表/连接键)、无连接仍成、在房 403、撞名 409、同名/空/超长/非串 400、
# 未知 sid 401、DB 错 500、会话 name 无行 500、presence 未接线 500、路由注册。

import json

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool

from app.auth.channel import derive_rest_keys, open_envelope, seal_envelope
from app.auth.session import SessionStore
from app.core.domain import UserState
from app.core.enums import UserStatus
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.rest.profile import make_nickname_router
from app.rest.secure import SecureRequest
from app.shell.connection import Connection, ConnectionManager
from app.shell.presence import Presence
from tests.builders import make_world, room_with
from tests.shell._fakes import FakeWS

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
            s.add(User(id=1, nickname="Alice", points=100, name="alice"))
            s.add(User(id=2, nickname="Bob", points=50, name="bob"))  # 撞名测试的既有昵称
    return sm


def _wiring(world=None):
    # 组一套 presence/conns(默认空 world = 全员大厅)。
    world = world if world is not None else make_world(rooms={}, users={})
    conns = ConnectionManager()
    return Presence(world, conns), conns


def _endpoint(router):
    routes = [r for r in router.routes if getattr(r, "path", None) == "/user/nickname"]
    assert len(routes) == 1, "/user/nickname 路由应恰好注册一条"
    return routes[0].endpoint


def _seal_req(session, seq: int, params: dict) -> str:
    enc, mac = derive_rest_keys(session.token)
    return seal_envelope(enc, mac, seq, json.dumps(params).encode()).hex()


def _open_resp(session, resp) -> tuple[int, dict]:
    enc, mac = derive_rest_keys(session.token)
    seq, plaintext = open_envelope(enc, mac, bytes.fromhex(resp.frame), _MAX)
    return seq, json.loads(plaintext)


async def _rename(store, sm, presence, conns, sid: str, frame_hex: str):
    router = make_nickname_router(lambda: sm, store, lambda: presence, conns, now=lambda: _T0)
    return await _endpoint(router)(SecureRequest(sid=sid, frame=frame_hex))


async def _db_nick(sm, uid: int) -> str:
    async with sm() as s:
        return (await s.get(User, uid)).nickname


async def test_happy_path_renames_db_sessions_and_connection():
    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)
    sid2, session2 = store.create("alice", "Alice", _T0)  # 同账号第二会话(另一设备)
    conn = Connection.create(nick="Alice", session_id=sid, ws=FakeWS(), session=session)
    conns.register(conn)  # 大厅 live 连接(加密路:带会话)
    resp = await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Neo"}))
    seq, data = _open_resp(session, resp)
    assert (seq, data) == (1, {"status": "ok", "nickname": "Neo"})
    assert await _db_nick(sm, 1) == "Neo"  # ① DB 已改
    assert session.nickname == "Neo" and session2.nickname == "Neo"  # ② 该账号全部会话联动
    assert conns.get("Neo") is conn and conns.get("Alice") is None and conn.nick == "Neo"  # ③ 连接键重挂


async def test_rename_without_live_connection_succeeds():
    # 大厅但未连 ws(纯 REST 会话)→ conns.rename no-op,DB/会话表照改。
    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)
    await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Neo"}))
    assert await _db_nick(sm, 1) == "Neo" and session.nickname == "Neo"


async def test_in_room_403_no_change():
    # 在房(world.users 有其 nick)→ 403,三处全不动(nickname 是 world 键,在用时改会键错乱)。
    sm = await _setup()
    world = make_world(
        rooms={"r1": room_with(users_in_room={"Alice": UserStatus.WATCHING})},
        users={"Alice": UserState(uid=1, nickname="Alice", points=100, room="r1")},
    )
    store = SessionStore(_TTL)
    presence, conns = _wiring(world)
    sid, session = store.create("alice", "Alice", _T0)
    with pytest.raises(HTTPException) as ei:
        await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Neo"}))
    assert ei.value.status_code == 403
    assert await _db_nick(sm, 1) == "Alice" and session.nickname == "Alice"  # 未动


async def test_taken_nickname_409():
    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)
    with pytest.raises(HTTPException) as ei:
        await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Bob"}))
    assert ei.value.status_code == 409
    assert await _db_nick(sm, 1) == "Alice"


async def test_race_integrity_error_maps_409(monkeypatch):
    # 预查与写之间的并发窗(0065 决策 3):预查放行、写时撞唯一约束 → IntegrityError 兜成 409(非 500)。
    # 用 monkeypatch 让预查失明(模拟「预查后、写前」另一请求占了名),逼出约束路径。
    import app.rest.profile as profile_mod

    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)

    async def _blind_precheck(sessionmaker, nickname):
        return False  # 预查看不见占用 → 放行到写

    monkeypatch.setattr(profile_mod, "nickname_taken", _blind_precheck)
    conn = Connection.create(nick="Alice", session_id=sid, ws=FakeWS(), session=session)
    conns.register(conn)
    with pytest.raises(HTTPException) as ei:
        await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Bob"}))  # Bob 已占
    assert ei.value.status_code == 409
    assert await _db_nick(sm, 1) == "Alice"  # 事务回滚,未改
    # DB 写失败 ⇒ 内存联动未执行(无半改,0065 决策 2):会话表与连接键都原封
    assert session.nickname == "Alice"
    assert conns.get("Alice") is conn and conns.get("Bob") is None and conn.nick == "Alice"


@pytest.mark.parametrize("bad", ["Alice", "", "   ", " Bob", "Bob ", "x" * 51, 5, ["Neo"], None])
async def test_bad_new_nickname_400(bad):
    # 同名(rename 语义须变)/ 空 / 超长(>50 对齐 models)/ 非串 / 缺参 → 400。
    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)
    params = {} if bad is None else {"new_nickname": bad}
    with pytest.raises(HTTPException) as ei:
        await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, params))
    assert ei.value.status_code == 400


async def test_unknown_sid_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    _, session = store.create("alice", "Alice", _T0)
    with pytest.raises(HTTPException) as ei:
        await _rename(store, sm, presence, conns, "bogus", _seal_req(session, 1, {"new_nickname": "Neo"}))
    assert ei.value.status_code == 401


async def test_db_error_500():
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)

    def _raising():
        raise RuntimeError("db down")

    router = make_nickname_router(_raising, store, lambda: presence, conns, now=lambda: _T0)
    with pytest.raises(HTTPException) as ei:
        await _endpoint(router)(SecureRequest(sid=sid, frame=_seal_req(session, 1, {"new_nickname": "Neo"})))
    assert ei.value.status_code == 500


async def test_session_name_without_db_row_500():
    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("ghost", "Ghost", _T0)
    with pytest.raises(HTTPException) as ei:
        await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Neo"}))
    assert ei.value.status_code == 500


async def test_presence_not_wired_500():
    # get_presence() 返 None(启动序错/未 setup)→ 基础设施 500,不误判大厅放行。
    sm = await _setup()
    store = SessionStore(_TTL)
    _, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)
    router = make_nickname_router(lambda: sm, store, lambda: None, conns, now=lambda: _T0)
    with pytest.raises(HTTPException) as ei:
        await _endpoint(router)(SecureRequest(sid=sid, frame=_seal_req(session, 1, {"new_nickname": "Neo"})))
    assert ei.value.status_code == 500


def test_create_app_registers_nickname_route():
    from app.shell.lifespan import create_app

    app = create_app()
    routes = [r for r in app.routes if getattr(r, "path", None) == "/user/nickname"]
    assert len(routes) == 1 and "POST" in routes[0].methods


async def test_cas_loser_409_no_memory_update(monkeypatch):
    # CAS(0065 自 review 抓修):同账号并发双改名,输者的 UPDATE(WHERE nickname=old)0 命中 → 409、
    # 且**跳过内存联动**——否则 DB/会话表/连接键各随一个赢家、永久发散。用 monkeypatch 让 load 返回
    # 陈旧 old_nick(模拟「读后、写前」另一请求已改走),逼出 CAS 0 命中路径。
    import app.rest.profile as profile_mod

    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)

    async def _stale_identity(sessionmaker, name):
        return (1, "Stale")  # DB 实际是 "Alice":CAS WHERE nickname='Stale' 必 0 命中

    monkeypatch.setattr(profile_mod, "load_identity_by_name", _stale_identity)
    with pytest.raises(HTTPException) as ei:
        await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Neo"}))
    assert ei.value.status_code == 409
    assert await _db_nick(sm, 1) == "Alice"  # DB 未被输者动
    assert session.nickname == "Alice"  # 内存联动被跳过


async def test_lobby_check_and_rekey_use_db_nickname_not_session(monkeypatch=None):
    # 决策 1 有测钉:会话表昵称**陈旧**("OldAlice")而 DB/world 是 "Alice" 时——
    # ① 在房判定按 DB 名 → 403(若按 session.nickname 查 "OldAlice" 会误判大厅放行);
    # ② 大厅时连接重挂也按 DB 名捕获(键 "Alice" 的 live 连接被 rekey,而非 miss)。
    sm = await _setup()
    store = SessionStore(_TTL)
    # ① 在房:world 键 "Alice"(DB 名),会话昵称陈旧
    world = make_world(
        rooms={"r1": room_with(users_in_room={"Alice": UserStatus.WATCHING})},
        users={"Alice": UserState(uid=1, nickname="Alice", points=100, room="r1")},
    )
    presence, conns = _wiring(world)
    sid, session = store.create("alice", "OldAlice", _T0)  # 陈旧会话昵称
    with pytest.raises(HTTPException) as ei:
        await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Neo"}))
    assert ei.value.status_code == 403  # 按 DB 名查到在房
    # ② 大厅:live 连接键于 DB 名 "Alice",会话昵称仍陈旧 → rekey 命中
    presence2, conns2 = _wiring()
    sid2, session2 = store.create("alice", "OldAlice", _T0)
    conn = Connection.create(nick="Alice", session_id=sid2, ws=FakeWS(), session=session2)
    conns2.register(conn)
    await _rename(store, sm, presence2, conns2, sid2, _seal_req(session2, 1, {"new_nickname": "Neo"}))
    assert conns2.get("Neo") is conn and conn.nick == "Neo"  # 按 DB 名捕获重挂,非按会话名 miss


# ── 0074·C:窗内进房 → 复查 + 回滚 DB + 403(修复前四处身份永久发散)──
async def test_join_room_during_await_window_reverts_and_403():
    # 「不在房」检查(profile.py:122)读 committed world,其后 nickname_taken / update_nickname 两次 DB await
    # 期间 GameLoop 完全可能提交该用户的 JoinRoom。修复前:照旧内存联动 → world 挂 old_nick 而 DB/会话/连接键
    # 变 new_nick,四处永久发散(旧 nick 幽灵成员、用户一切命令 NOT_IN_ROOM 无法自救、可二次进房复制积分)。
    # 修复后:窗后复查发现已在房 → 同款 CAS 把 DB 改回 → 403,四处仍一致为 old_nick。
    sm = await _setup()
    world = make_world(rooms={}, users={})  # 起初在大厅:窗前检查放行
    store = SessionStore(_TTL)
    presence, conns = _wiring(world)
    sid, session = store.create("alice", "Alice", _T0)
    conn = Connection.create(nick="Alice", session_id=sid, ws=FakeWS(), session=session)
    conns.register(conn)

    calls = {"n": 0}

    def sm_getter():
        calls["n"] += 1
        if calls["n"] == 2:  # 第 2 次取 sm = nickname_taken 时,即窗前检查已过 → 此刻模拟 GameLoop 提交 JoinRoom
            world.rooms["r1"] = room_with(users_in_room={"Alice": UserStatus.WATCHING})
            world.users["Alice"] = UserState(uid=1, nickname="Alice", points=100, room="r1")
        return sm

    router = make_nickname_router(sm_getter, store, lambda: presence, conns, now=lambda: _T0)
    with pytest.raises(HTTPException) as ei:
        await _endpoint(router)(
            SecureRequest(sid=sid, frame=_seal_req(session, 1, {"new_nickname": "Neo"}))
        )
    assert ei.value.status_code == 403  # 与「窗前就在房」同码
    assert await _db_nick(sm, 1) == "Alice"  # DB 已回滚(修复前会停在 Neo)
    assert session.nickname == "Alice"  # 会话表未动
    assert conns.get("Alice") is conn and conn.nick == "Alice"  # 连接键未动 → 广播仍能按 world 的 old_nick 找到他


# ── 改昵称窗内发生 ws 顶替(changes/0083 / BUG-4)──

async def test_ws_takeover_inside_db_window_rekeys_the_live_connection(monkeypatch):
    # 两次 DB await 的窗口里本人被 ws 顶替(另一设备登录):此前用窗前捕获的引用做 rekey,
    # 改的是**已死对象**,真正的活连接永久挂在旧 nick 键下 —— 用户在线却收不到任何消息。
    import app.rest.profile as profile_mod

    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)
    dead = Connection.create(nick="Alice", session_id=sid, ws=FakeWS(), session=session)
    conns.register(dead)
    live = Connection.create(nick="Alice", session_id="sid2", ws=FakeWS(), session=session)

    real_taken = profile_mod.nickname_taken

    async def taken_then_takeover(*args, **kwargs):
        conns.register(live)  # 窗内顶替:同 nick 新连接接管(旧的被静默关掉)
        return await real_taken(*args, **kwargs)

    monkeypatch.setattr(profile_mod, "nickname_taken", taken_then_takeover)
    await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Neo"}))
    assert await _db_nick(sm, 1) == "Neo"
    assert conns.get("Neo") is live  # 重挂的是**活**连接
    assert conns.get("Alice") is None  # 旧键不留残挂
    assert live.nick == "Neo"


async def test_rename_does_not_rekey_another_accounts_connection(monkeypatch):
    # 0065 早捕获本来要防的另一头:窗内**别人**改名占走了 old_nick 键,这时表里挂的是他人的连接,
    # 绝不能把它重挂到我的新昵称上(那等于把别人的连接抢过来)。晚查 + 归属校验两头都要堵住。
    import app.rest.profile as profile_mod

    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)
    mine = Connection.create(nick="Alice", session_id=sid, ws=FakeWS(), session=session)
    conns.register(mine)
    _, other_session = store.create("bob", "Alice", _T0)  # 另一账号,窗内改名占走了 "Alice" 这个键
    intruder = Connection.create(nick="Alice", session_id="sid-bob", ws=FakeWS(), session=other_session)

    real_taken = profile_mod.nickname_taken

    async def taken_then_intrude(*args, **kwargs):
        conns.unregister(mine)  # 我的连接此刻已断
        conns.register(intruder)  # 他人连接占走 old_nick 键
        return await real_taken(*args, **kwargs)

    monkeypatch.setattr(profile_mod, "nickname_taken", taken_then_intrude)
    await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Neo"}))
    assert await _db_nick(sm, 1) == "Neo"  # 我的改名照常完成
    assert conns.get("Alice") is intruder and intruder.nick == "Alice"  # 他人连接一动不动
    assert conns.get("Neo") is None  # 没把别人的连接挂到我的新昵称上


async def test_dev_plaintext_connection_is_matched_by_its_handshake_nick():
    # dev 明文连接没有会话,归属只能按 dev 端点自己的不变量判(session_id == 握手时的 ?nick=)。
    # 「无会话就当本人」不行:那是 TOCTOU —— DB 行是建连时查的,到 rekey 这一步 old_nick 名下早已没有行,
    # 一条搁浅在该键上的陌生 socket 会被认领成我的。
    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)
    dev_conn = Connection.create(nick="Alice", session_id="Alice", ws=FakeWS())  # dev:session_id = 握手 nick
    conns.register(dev_conn)
    await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Neo"}))
    assert conns.get("Neo") is dev_conn and conns.get("Alice") is None


async def test_sessionless_connection_on_a_foreign_key_is_not_claimed():
    # 同为无会话,但 session_id 对不上 old_nick(= 不是「以 Alice 身份握手」建的)→ 不认领。
    sm = await _setup()
    store = SessionStore(_TTL)
    presence, conns = _wiring()
    sid, session = store.create("alice", "Alice", _T0)
    stranded = Connection.create(nick="Alice", session_id="somebody-else", ws=FakeWS())
    conns.register(stranded)
    await _rename(store, sm, presence, conns, sid, _seal_req(session, 1, {"new_nickname": "Neo"}))
    assert conns.get("Alice") is stranded and conns.get("Neo") is None
