"""端到端异步冒烟:run_receiver → inbox → GameLoop → dispatch → Sender → ws(全协程接通)。
验真实并发管线 + 明文帧解析/回发(sync 冒烟只走 GameLoop→dispatch,不含 receiver/sender 协程)。"""

import asyncio

from sqlalchemy.pool import StaticPool

from app.core.domain import UserState
from app.core.enums import UserStatus
from app.db.dm_records import DMWrite
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.shell.connection import Connection
from app.shell.receiver import run_receiver
from app.wire.server import (
    ChatMessage,
    DMDelivered,
    ErrorMessage,
    RoomChatHistory,
    StateSnapshot,
    UserStatusChanged,
)
from tests.builders import make_world, room_with
from tests.shell._fakes import FakeWS, Shell


def _non_snapshot(sent: list[str]) -> str:
    # alice 预置在房在线 → 初始 Connect 先回一帧 takeover StateSnapshot(0031,_connect 在房在线臂);
    # 取首个非快照帧 = 喂入命令的真正响应。
    return next(s for s in sent if '"type":"state_snapshot"' not in s)


def _world():
    return make_world(
        rooms={"r1": room_with(users_in_room={"alice": UserStatus.WATCHING})},
        users={"alice": UserState(uid=1, nickname="alice", points=500, room="r1")},
    )


def _sm():
    # 未配置的 sessionmaker:非 JoinRoom 帧不读 DB,够用(JoinRoom 路径不命中即不查)。
    return make_sessionmaker(
        make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    )


async def _seeded_sm(users: dict[str, tuple[int, int]]):
    # users: {nick: (uid, points)};建表 + 种子,供 JoinRoom 读 DB 富化。
    engine = make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            for nick, (uid, pts) in users.items():
                s.add(User(id=uid, nickname=nick, points=pts))
    return sm


async def _settle(cond, timeout: float = 1.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.005)
    return cond()


async def _run_one_frame(frame: str):
    # 起 GameLoop + Receiver(fake ws),喂一帧,返回 (world, shell, conn)。调用方负责断言 + 取消。
    world = _world()
    sh = Shell(world)
    gl = asyncio.create_task(sh.gameloop.run())
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, _sm(), sh.history, sh.persist))
    await asyncio.sleep(0)  # 让 receiver 登记 + 起 sender + 投 Connect,停在 receive_text
    conn.ws.feed(frame)
    # alice 预置在房在线 → 初始 Connect 先回一帧 takeover StateSnapshot(0031);等「喂入帧的响应」=
    # 首个非 state_snapshot 帧到达,再交回断言(避免只等到先行的快照帧)。
    await _settle(lambda: any('"type":"state_snapshot"' not in s for s in conn.ws.sent))
    return world, sh, conn, (gl, rx)


async def _shutdown(tasks):
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass


async def test_valid_frame_flows_through_to_ws():
    world, sh, conn, tasks = await _run_one_frame('{"type":"sit_down","seat":0}')
    try:
        assert world.rooms["r1"].users_in_room["alice"] is UserStatus.SITTING_IN  # reduce 真改了 world
        assert conn.ws.sent, "client received no frame"
        msg = UserStatusChanged.model_validate_json(_non_snapshot(conn.ws.sent))  # 明文 JSON 往返(跳过先行快照)
        assert msg.status is UserStatus.SITTING_IN and msg.seat_position == 0
    finally:
        await _shutdown(tasks)


async def test_room_chat_frame_passes_guard_and_broadcasts_chat_message():
    # 端到端:合法 room_chat 帧穿过 _guard_room_chat → inbox → _room_chat reduce → Broadcast(ChatMessage)
    # (验防护真接进 run_receiver 路径,非仅 sync 单测 _guard_room_chat)。alice 预置在房 WATCHING。
    world, sh, conn, tasks = await _run_one_frame('{"type":"room_chat","text":"hi all"}')
    try:
        msg = ChatMessage.model_validate_json(_non_snapshot(conn.ws.sent))  # 跳过先行的 takeover 快照
        assert msg.from_nick == "alice" and msg.text == "hi all"  # 身份盖连接 nick、原文广播
    finally:
        await _shutdown(tasks)


async def test_fetch_room_chat_returns_recent_history_in_order():
    # 端到端:连聊两句(广播 → 入环形缓冲)→ fetch_room_chat → shell 直服务回 RoomChatHistory(旧→新有序)。
    world = _world()  # alice WATCHING in r1
    sh = Shell(world)
    gl = asyncio.create_task(sh.gameloop.run())
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, _sm(), sh.history, sh.persist))
    await asyncio.sleep(0)
    conn.ws.feed('{"type":"room_chat","text":"hello"}')  # → reduce → Broadcast(ChatMessage) → 入缓冲
    conn.ws.feed('{"type":"room_chat","text":"world"}')
    await _settle(lambda: sum('"type":"chat_message"' in s for s in conn.ws.sent) >= 2)  # 两条都已广播+入缓冲
    conn.ws.feed('{"type":"fetch_room_chat","room":"r1"}')  # → shell 直服务(不进 GameLoop)
    await _settle(lambda: any('"type":"room_chat_history"' in s for s in conn.ws.sent))
    try:
        hist = RoomChatHistory.model_validate_json(
            next(s for s in conn.ws.sent if '"type":"room_chat_history"' in s)
        )
        assert hist.room == "r1"
        # 旧→新有序(端到端穿 reduce→buffer→Sender),非只单条
        assert tuple((m.from_nick, m.text) for m in hist.messages) == (("alice", "hello"), ("alice", "world"))
    finally:
        await _shutdown((rx, gl))


async def test_fetch_room_chat_serves_room_not_a_member_of():
    # v1 不校验成员资格(changes/0036 决策 5):alice 在 r1,却能拉到 r2 的历史(房聊公开非敏感)。
    world = _world()  # alice WATCHING in r1(不在 r2)
    sh = Shell(world)
    sh.history.append("r2", ChatMessage(from_nick="bob", text="hi r2"))  # r2 已有历史,alice 非成员
    gl = asyncio.create_task(sh.gameloop.run())
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, _sm(), sh.history, sh.persist))
    await asyncio.sleep(0)
    conn.ws.feed('{"type":"fetch_room_chat","room":"r2"}')  # 拉非自己房的历史
    await _settle(lambda: any('"type":"room_chat_history"' in s for s in conn.ws.sent))
    try:
        hist = RoomChatHistory.model_validate_json(
            next(s for s in conn.ws.sent if '"type":"room_chat_history"' in s)
        )
        assert hist.room == "r2" and tuple((m.from_nick, m.text) for m in hist.messages) == (("bob", "hi r2"),)
    finally:
        await _shutdown((rx, gl))


async def test_fetch_room_chat_unknown_room_returns_empty():
    # 拉无记录的房(从未聊过 / 不存在)→ 空历史(shell 不读 world,不校验房存在;benign 空回)。
    world = _world()
    sh = Shell(world)
    gl = asyncio.create_task(sh.gameloop.run())
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, _sm(), sh.history, sh.persist))
    await asyncio.sleep(0)
    conn.ws.feed('{"type":"fetch_room_chat","room":"ghost"}')
    await _settle(lambda: any('"type":"room_chat_history"' in s for s in conn.ws.sent))
    try:
        hist = RoomChatHistory.model_validate_json(
            next(s for s in conn.ws.sent if '"type":"room_chat_history"' in s)
        )
        assert hist.room == "ghost" and hist.messages == ()
    finally:
        await _shutdown((rx, gl))


async def test_room_chat_empty_frame_rejected_in_guard_before_reduce():
    # 空文本帧被 Receiver 文本防护拦下(回 INVALID_MESSAGE),根本不进 reduce/world。
    world, sh, conn, tasks = await _run_one_frame('{"type":"room_chat","text":"   "}')
    try:
        assert ErrorMessage.model_validate_json(_non_snapshot(conn.ws.sent)).code.value == "INVALID_MESSAGE"
    finally:
        await _shutdown(tasks)


async def test_room_chat_too_long_frame_rejected_through_pipeline():
    # 超长帧端到端走 run_receiver → 防护拒 → outbound → Sender,回 MESSAGE_TOO_LONG。
    over = "x" * 600  # > ROOM_CHAT_MAX_TEXT_LEN(500)
    world, sh, conn, tasks = await _run_one_frame(f'{{"type":"room_chat","text":"{over}"}}')
    try:
        assert ErrorMessage.model_validate_json(_non_snapshot(conn.ws.sent)).code.value == "MESSAGE_TOO_LONG"
    finally:
        await _shutdown(tasks)


async def test_invalid_frame_returns_error_and_leaves_world_untouched():
    world, sh, conn, tasks = await _run_one_frame('{"type":"nonsense"}')
    try:
        msg = ErrorMessage.model_validate_json(_non_snapshot(conn.ws.sent))  # 跳过先行的 takeover 快照
        assert msg.code.value == "INVALID_MESSAGE"  # 解析层直接回发,不进 reduce
        assert world.rooms["r1"].users_in_room["alice"] is UserStatus.WATCHING  # world 未动
    finally:
        await _shutdown(tasks)


async def test_join_room_frame_loads_user_from_db():
    # per-join 载入(0030):alice 连接进大厅(world 无 alice)→ join_room → Receiver 读 DB 富化 uid/loaded →
    # reduce _join_room 装入 world(WATCHING)+ 私发快照/广播 UserJoined。
    world = make_world(rooms={"dev": room_with(users_in_room={})}, users={})  # 空 dev 房
    sh = Shell(world)
    sm = await _seeded_sm({"alice": (7, 888)})  # DB 里 alice uid=7 points=888
    gl = asyncio.create_task(sh.gameloop.run())
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, sm, sh.history, sh.persist))
    await asyncio.sleep(0)  # 登记 + 投 Connect(大厅 no-op)
    conn.ws.feed('{"type":"join_room","room":"dev"}')
    await _settle(lambda: "alice" in world.users)
    try:
        assert world.users["alice"].uid == 7  # 读 DB 富化(不信报文)
        assert world.users["alice"].points == 888  # 从 DB 载入积分
        assert world.rooms["dev"].users_in_room["alice"] is UserStatus.WATCHING
        assert conn.ws.sent  # UserJoined 广播 + StateSnapshot 私发 → 收到帧
    finally:
        await _shutdown((rx, gl))


async def test_join_room_unknown_user_errors_internal_keeps_conn():
    # nick 在连接但 DB 无此行(只种子 bob,无 alice)→ 回 INTERNAL,连接不被 drop、world 未动。
    world = make_world(rooms={"dev": room_with(users_in_room={})}, users={})
    sh = Shell(world)
    sm = await _seeded_sm({"bob": (2, 100)})
    gl = asyncio.create_task(sh.gameloop.run())
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, sm, sh.history, sh.persist))
    await asyncio.sleep(0)
    conn.ws.feed('{"type":"join_room","room":"dev"}')
    await _settle(lambda: len(conn.ws.sent) >= 1)
    try:
        assert ErrorMessage.model_validate_json(conn.ws.sent[0]).code.value == "INTERNAL"
        assert "alice" not in world.users  # 没装入
        assert sh.conns.is_current(conn)  # 连接仍在(没被 drop)
    finally:
        await _shutdown((rx, gl))


async def test_join_room_db_error_errors_internal_keeps_conn():
    # DB 读抛(sessionmaker 未建表 → select(User) 报 no-such-table)→ _build_join 兜成 INTERNAL,连接保活。
    world = make_world(rooms={"dev": room_with(users_in_room={})}, users={})
    sh = Shell(world)
    sm = _sm()  # 未 create_all:查 User 表即抛
    gl = asyncio.create_task(sh.gameloop.run())
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, sm, sh.history, sh.persist))
    await asyncio.sleep(0)
    conn.ws.feed('{"type":"join_room","room":"dev"}')
    await _settle(lambda: len(conn.ws.sent) >= 1)
    try:
        assert ErrorMessage.model_validate_json(conn.ws.sent[0]).code.value == "INTERNAL"
        assert sh.conns.is_current(conn)  # DB 抖动没拖垮连接
    finally:
        await _shutdown((rx, gl))


async def test_join_room_nonexistent_room_errors_to_origin():
    # 富化成功(alice 在 DB)但目标房不存在 → reduce 回 NO_SUCH_ROOM,经 GameLoop 回发本人。
    world = make_world(rooms={"dev": room_with(users_in_room={})}, users={})
    sh = Shell(world)
    sm = await _seeded_sm({"alice": (1, 500)})
    gl = asyncio.create_task(sh.gameloop.run())
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, sm, sh.history, sh.persist))
    await asyncio.sleep(0)
    conn.ws.feed('{"type":"join_room","room":"ghost"}')  # world 无 ghost 房
    await _settle(lambda: len(conn.ws.sent) >= 1)
    try:
        assert ErrorMessage.model_validate_json(conn.ws.sent[0]).code.value == "NO_SUCH_ROOM"
        assert "alice" not in world.users
    finally:
        await _shutdown((rx, gl))


async def test_join_room_twice_errors_already_in_room():
    # 进房成功后再 join → reduce 单房间约束回 ALREADY_IN_ROOM,经 GameLoop 回发本人(world 不变)。
    world = make_world(rooms={"dev": room_with(users_in_room={})}, users={})
    sh = Shell(world)
    sm = await _seeded_sm({"alice": (1, 500)})
    gl = asyncio.create_task(sh.gameloop.run())
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, sm, sh.history, sh.persist))
    await asyncio.sleep(0)
    conn.ws.feed('{"type":"join_room","room":"dev"}')  # 第一次:成功装入
    await _settle(lambda: "alice" in world.users)
    conn.ws.feed('{"type":"join_room","room":"dev"}')  # 第二次:已在房
    await _settle(lambda: any("ALREADY_IN_ROOM" in s for s in conn.ws.sent))
    try:
        assert any(
            ErrorMessage.model_validate_json(s).code.value == "ALREADY_IN_ROOM"
            for s in conn.ws.sent
            if "ALREADY_IN_ROOM" in s
        )
    finally:
        await _shutdown((rx, gl))


async def test_direct_message_frame_delivers_to_online_recipient():
    # 端到端:alice 发 direct_message 帧 → shell DM 路由(不进 GameLoop)→ 在线 bob 的 ws 收到 dm_delivered + 落库 DMWrite。
    world = _world()  # alice 预置在房;bob 仅需连接 + DB 行(DM 不碰 world)
    sh = Shell(world)
    sm = await _seeded_sm({"alice": (1, 500), "bob": (2, 500)})
    gl = asyncio.create_task(sh.gameloop.run())
    a = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    b = Connection.create(nick="bob", session_id="bob", ws=FakeWS())
    rxa = asyncio.create_task(run_receiver(a, sh.conns, sh.inbox, sh.timer, sm, sh.history, sh.persist))
    rxb = asyncio.create_task(run_receiver(b, sh.conns, sh.inbox, sh.timer, sm, sh.history, sh.persist))
    await _settle(lambda: sh.conns.is_current(a) and sh.conns.is_current(b))
    a.ws.feed('{"type":"direct_message","to_nick":"bob","text":"hey bob"}')
    await _settle(lambda: any('"type":"dm_delivered"' in s for s in b.ws.sent))
    try:
        dm = DMDelivered.model_validate_json(next(s for s in b.ws.sent if '"type":"dm_delivered"' in s))
        assert dm.from_nick == "alice" and dm.text == "hey bob" and dm.msg_id  # 身份盖连接 nick、原文
        assert any(isinstance(p, DMWrite) for p in sh.persist.snapshot())  # 必落库(未读)
    finally:
        await _shutdown((rxa, rxb, gl))


async def test_async_displacement_old_connection_exits_silently():
    # 顶替语义(connection.md):同 nick 新连接接管;旧连接被关闭、其 Sender 被 cancel,旧 Receiver
    # 退出时 is_current=False → **不投 Disconnect**(否则会把刚上位的新连接误标 OFFLINE)。
    world = _world()
    sh = Shell(world)
    sm = _sm()
    gl = asyncio.create_task(sh.gameloop.run())
    c1 = Connection.create(nick="alice", session_id="s1", ws=FakeWS())
    rx1 = asyncio.create_task(run_receiver(c1, sh.conns, sh.inbox, sh.timer, sm, sh.history, sh.persist))
    await _settle(lambda: sh.conns.is_current(c1))

    c2 = Connection.create(nick="alice", session_id="s2", ws=FakeWS())
    rx2 = asyncio.create_task(run_receiver(c2, sh.conns, sh.inbox, sh.timer, sm, sh.history, sh.persist))
    await _settle(lambda: sh.conns.is_current(c2) and c1.ws.closed and rx1.done())
    try:
        assert sh.conns.is_current(c2)  # 新连接上位
        assert c1.ws.closed  # 旧 ws 被关
        assert c1.sender_task.cancelled() or c1.sender_task.done()  # 旧 Sender 被 cancel
        assert rx1.done() and rx1.exception() is None  # 旧 Receiver 干净退出
        # 关键:旧连接退出未把 alice 标 OFFLINE(顶替静默);新连接仍可正常收发
        await asyncio.sleep(0.02)
        assert world.rooms["r1"].users_in_room["alice"] is UserStatus.WATCHING
        # 0031:顶替后新连接(c2)经 Connect → _connect 在房在线臂收到 takeover StateSnapshot 对齐桌面
        await _settle(lambda: any('"type":"state_snapshot"' in s for s in c2.ws.sent))
        snap_frame = next(s for s in c2.ws.sent if '"type":"state_snapshot"' in s)
        StateSnapshot.model_validate_json(snap_frame)  # 合法整桌快照报文
        # 新连接仍能正常发命令收响应(等非快照帧 = sit_down 的 user_status_changed)
        c2.ws.feed('{"type":"sit_down","seat":0}')
        await _settle(lambda: any('"type":"user_status_changed"' in s for s in c2.ws.sent))
        assert world.rooms["r1"].users_in_room["alice"] is UserStatus.SITTING_IN
    finally:
        await _shutdown((rx2, gl))
