"""端到端异步冒烟:run_receiver → inbox → GameLoop → dispatch → Sender → ws(全协程接通)。
验真实并发管线 + 明文帧解析/回发(sync 冒烟只走 GameLoop→dispatch,不含 receiver/sender 协程)。"""

import asyncio

from app.core.domain import UserState
from app.core.enums import UserStatus
from app.shell.connection import Connection
from app.shell.receiver import run_receiver
from app.wire.server import ErrorMessage, UserStatusChanged
from tests.builders import make_world, room_with
from tests.shell._fakes import FakeWS, Shell


def _world():
    return make_world(
        rooms={"r1": room_with(users_in_room={"alice": UserStatus.WATCHING})},
        users={"alice": UserState(uid=1, nickname="alice", points=500, room="r1")},
    )


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
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer))
    await asyncio.sleep(0)  # 让 receiver 登记 + 起 sender + 投 Connect,停在 receive_text
    conn.ws.feed(frame)
    await _settle(lambda: len(conn.ws.sent) >= 1)
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
        msg = UserStatusChanged.model_validate_json(conn.ws.sent[0])  # 明文 JSON 往返
        assert msg.status is UserStatus.SITTING_IN and msg.seat_position == 0
    finally:
        await _shutdown(tasks)


async def test_invalid_frame_returns_error_and_leaves_world_untouched():
    world, sh, conn, tasks = await _run_one_frame('{"type":"nonsense"}')
    try:
        msg = ErrorMessage.model_validate_json(conn.ws.sent[0])
        assert msg.code.value == "INVALID_MESSAGE"  # 解析层直接回发,不进 reduce
        assert world.rooms["r1"].users_in_room["alice"] is UserStatus.WATCHING  # world 未动
    finally:
        await _shutdown(tasks)


async def test_async_displacement_old_connection_exits_silently():
    # 顶替语义(connection.md):同 nick 新连接接管;旧连接被关闭、其 Sender 被 cancel,旧 Receiver
    # 退出时 is_current=False → **不投 Disconnect**(否则会把刚上位的新连接误标 OFFLINE)。
    world = _world()
    sh = Shell(world)
    gl = asyncio.create_task(sh.gameloop.run())
    c1 = Connection.create(nick="alice", session_id="s1", ws=FakeWS())
    rx1 = asyncio.create_task(run_receiver(c1, sh.conns, sh.inbox, sh.timer))
    await _settle(lambda: sh.conns.is_current(c1))

    c2 = Connection.create(nick="alice", session_id="s2", ws=FakeWS())
    rx2 = asyncio.create_task(run_receiver(c2, sh.conns, sh.inbox, sh.timer))
    await _settle(lambda: sh.conns.is_current(c2) and c1.ws.closed and rx1.done())
    try:
        assert sh.conns.is_current(c2)  # 新连接上位
        assert c1.ws.closed  # 旧 ws 被关
        assert c1.sender_task.cancelled() or c1.sender_task.done()  # 旧 Sender 被 cancel
        assert rx1.done() and rx1.exception() is None  # 旧 Receiver 干净退出
        # 关键:旧连接退出未把 alice 标 OFFLINE(顶替静默);新连接仍可正常收发
        await asyncio.sleep(0.02)
        assert world.rooms["r1"].users_in_room["alice"] is UserStatus.WATCHING
        c2.ws.feed('{"type":"sit_down","seat":0}')
        await _settle(lambda: len(c2.ws.sent) >= 1)
        assert world.rooms["r1"].users_in_room["alice"] is UserStatus.SITTING_IN
    finally:
        await _shutdown((rx2, gl))
