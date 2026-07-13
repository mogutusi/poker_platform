"""P5 ws 安全信道接线(changes/0061):Receiver/Sender 逐帧加解密 + `/ws?sid=` 握手 + 逐会话 seq。
验加密路端到端(Sender seal→客户端 open / 客户端 seal→Receiver open→穿 GameLoop)、FrameError 关连接、
会话信道缓存(seq 逐会话连续 → 跨重连挡重放)、`/ws` 路由注册。明文 dev 路不受影响(既有 test_receiver 覆盖)。"""

import asyncio
import secrets
import time

from sqlalchemy.pool import StaticPool

from app import gameconfig
from app.auth.channel import SecureChannel
from app.auth.session import Session
from app.core.commands import Connect, Disconnect
from app.core.domain import UserState
from app.core.enums import UserStatus
from app.db.engine import make_engine, make_sessionmaker
from app.shell.connection import Connection
from app.shell.lifespan import _channel_for, create_app
from app.shell.receiver import run_receiver
from app.shell.sender import sender_loop
from app.wire.server import StateSnapshot, UserStatusChanged
from tests.builders import make_world, room_with
from tests.shell._fakes import FakeWS, Shell

_MAX = gameconfig.WS_FRAME_MAX_BYTES
_FAR_FUTURE = 1e12  # 会话 exp:测试期不过期


def _world():
    return make_world(
        rooms={"r1": room_with(users_in_room={"alice": UserStatus.WATCHING})},
        users={"alice": UserState(uid=1, nickname="alice", points=500, room="r1")},
    )


def _sm():
    # 未配置的 sessionmaker:非 JoinRoom 帧不读 DB(catch-up best-effort 吞 DB 错),够用。
    return make_sessionmaker(
        make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    )


def _client_channel(token: bytes) -> SecureChannel:
    # 客户端侧信道(同会话 token → 与服务端密钥一致;各自计双向 seq,同 crypto/test_channel._pair)。
    return SecureChannel.derive(token, _MAX)


def _open_ordered(client_ch: SecureChannel, frames: list[bytes]) -> list[str]:
    # 按发出顺序逐帧 open(client 入站 seq 单调,须顺序解);返回明文 JSON 串列表。
    return [client_ch.open(f).decode("utf-8") for f in frames]


async def _settle(cond, timeout: float = 1.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.005)
    return cond()


async def _shutdown(*tasks):
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass


# ── Sender:出站 seal + 保序 ──


async def test_sender_seals_outbound_and_client_opens_in_order():
    token = secrets.token_bytes(32)
    server_ch = SecureChannel.derive(token, _MAX)
    client_ch = _client_channel(token)
    conn = Connection.create(nick="alice", session_id="s1", ws=FakeWS(), channel=server_ch)
    for i in range(10):
        conn.outbound.put_nowait(UserStatusChanged(nickname="alice", status=UserStatus.SITTING_IN, seat_position=i))
    task = asyncio.create_task(sender_loop(conn))
    try:
        await _settle(lambda: len(conn.ws.sent_bytes) >= 10)
        assert conn.ws.sent == []  # 加密路不走 send_text
        # 客户端按序解密还原 → 座位号 0..9 严格保序(seal 逐帧 seq 递增,client open 校验单调)
        seats = [UserStatusChanged.model_validate_json(j).seat_position for j in _open_ordered(client_ch, conn.ws.sent_bytes)]
        assert seats == list(range(10))
    finally:
        await _shutdown(task)


# ── Receiver:入站 open → 穿 GameLoop → 出站 seal ──


async def test_encrypted_frame_flows_through_pipeline():
    token = secrets.token_bytes(32)
    server_ch = SecureChannel.derive(token, _MAX)
    client_ch = _client_channel(token)
    world = _world()
    sh = Shell(world)
    gl = asyncio.create_task(sh.gameloop.run())
    conn = Connection.create(nick="alice", session_id="s1", ws=FakeWS(), channel=server_ch)
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, _sm(), world, sh.persist))
    try:
        # 初始 Connect(alice 预置在房在线)→ 顶替快照;等它先 seal 出去(sent_bytes[0])
        await _settle(lambda: len(conn.ws.sent_bytes) >= 1)
        conn.ws.feed_bytes(client_ch.seal(b'{"type":"sit_down","seat":0}'))  # 客户端加密一帧
        await _settle(lambda: len(conn.ws.sent_bytes) >= 2)
        assert world.rooms["r1"].users_in_room["alice"] is UserStatus.SITTING_IN  # reduce 真改 world(帧被正确解密)
        decoded = _open_ordered(client_ch, conn.ws.sent_bytes)  # 按序解密两帧
        StateSnapshot.model_validate_json(decoded[0])  # 首帧 = 顶替快照
        status = UserStatusChanged.model_validate_json(decoded[1])  # 次帧 = sit_down 响应
        assert status.status is UserStatus.SITTING_IN and status.seat_position == 0
    finally:
        await _shutdown(rx, gl)


async def test_forged_frame_closes_connection_and_posts_disconnect():
    # 伪造/损坏帧(MAC 验不过)= 安全信号:Receiver 关 ws(4400)+ 退出;当前连接 → finally 投 Disconnect。
    token = secrets.token_bytes(32)
    server_ch = SecureChannel.derive(token, _MAX)
    world = _world()
    sh = Shell(world)  # 不起 gameloop:让 Connect/Disconnect 留在 inbox 供断言
    conn = Connection.create(nick="alice", session_id="s1", ws=FakeWS(), channel=server_ch)
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, _sm(), world, sh.persist))
    await _settle(lambda: sh.conns.is_current(conn))  # 已登记
    conn.ws.feed_bytes(secrets.token_bytes(64))  # 结构合法(64B)但 MAC 必不过 → bad_mac
    await _settle(lambda: rx.done())
    try:
        assert rx.done() and rx.exception() is None  # 干净退出
        assert conn.ws.closed and conn.ws.close_code == 4400  # 拒帧即关连接
        assert not sh.conns.is_current(conn)  # finally unregister
        cmds = sh.inbox_drain()
        assert any(isinstance(c, Connect) for c in cmds)  # 起始 Connect
        assert any(isinstance(c, Disconnect) and c.nick == "alice" for c in cmds)  # 关连接投 Disconnect(当前连接)
    finally:
        await _shutdown(rx)


# ── 会话信道缓存 + 逐会话 seq(跨重连挡重放)──


def test_channel_for_caches_on_session():
    # _channel_for 首次派生并缓存在 Session 上,再取复用同一实例(seq 逐会话连续,不逐连接重置)。
    token = secrets.token_bytes(32)
    session = Session(name="alice", nickname="alice", token=token, expires_at=_FAR_FUTURE)
    assert session.channel is None
    ch1 = _channel_for(session)
    assert session.channel is ch1  # 缓存
    assert _channel_for(session) is ch1  # 复用,不重派生
    # 密钥源自会话 token:同 token 的客户端信道封帧,服务端信道能解
    client = _client_channel(token)
    assert ch1.open(client.seal(b"hi")) == b"hi"


def test_replay_across_reconnect_blocked_by_session_seq():
    # 逐会话 seq 的价值:重连复用同一会话信道 ⇒ 旧帧 seq 不新被 stale_seq 挡(若逐连接重置则可重放)。
    token = secrets.token_bytes(32)
    session = Session(name="alice", nickname="alice", token=token, expires_at=_FAR_FUTURE)
    client = _client_channel(token)
    server_ch = _channel_for(session)  # 首连
    frame1 = client.seal(b'{"type":"leave_room"}')
    assert server_ch.open(frame1) == b'{"type":"leave_room"}'  # 首连收下,server _in_seq→1
    # 「重连」:同 sid → _channel_for 复用同一信道实例(非重派生)
    server_reconnect = _channel_for(session)
    assert server_reconnect is server_ch
    try:
        server_reconnect.open(frame1)  # 重放捕获的旧帧
        assert False, "replayed frame should be rejected"
    except Exception as e:
        assert getattr(e, "reason", None) == "stale_seq"  # seq ≤ 已见 → 拒(逐会话连续 seq 兜住)


# ── 端点布线(/ws 握手)──


def _ws_endpoint(app):
    # 取 /ws 的原始 handler(FastAPI ws 路由 .endpoint 是用户函数;同 test_login 取 POST handler)。
    routes = [r for r in app.routes if getattr(r, "path", None) == "/ws"]
    assert len(routes) == 1, "/ws 路由应恰好注册一条"
    return routes[0].endpoint


def test_secure_ws_route_registered():
    # create_app 注册加密端点 /ws(与 dev 明文 /dev/ws 并存)。
    app = create_app()
    ws_paths = {getattr(r, "path", None) for r in app.routes}
    assert "/ws" in ws_paths and "/dev/ws" in ws_paths


async def test_secure_ws_unknown_sid_rejected():
    # 未知/过期/伪 sid → 查会话 None → accept 后关闭码 4401 拒,绝不建 Connection(connection.md 步 1)。
    app = create_app()
    shell = app.state.shell  # session_store 空(未 create 任何会话)
    ws = FakeWS()
    await _ws_endpoint(app)(ws, sid="bogus-sid")  # 直调 handler(显式 sid 覆盖 Query 默认)
    assert ws.accepted and ws.closed and ws.close_code == 4401  # 先 accept 再拒
    assert shell.conns.online_nicks() == set()  # 未建/登记任何连接


async def test_secure_ws_valid_sid_builds_encrypted_connection():
    # 有效 sid → 查会话 → get-or-derive 会话信道 → 建 Connection(channel 非 None)→ run_receiver 登记。
    app = create_app()
    shell = app.state.shell
    sid, session = shell.session_store.create("alice", "alice", time.time())  # 铸一条会话
    ws = FakeWS()
    task = asyncio.create_task(_ws_endpoint(app)(ws, sid=sid))
    try:
        await _settle(lambda: shell.conns.get("alice") is not None)  # 连接已登记
        conn = shell.conns.get("alice")
        assert conn is not None and conn.channel is not None  # 加密连接(非明文)
        assert conn.channel is session.channel  # 引用会话信道(_channel_for 缓存,跨重连复用)
        assert ws.accepted and not ws.closed  # 已 accept、未被拒
    finally:
        await ws.close()  # 唤醒 receive_bytes → run_receiver 干净退出
        await _shutdown(task)
