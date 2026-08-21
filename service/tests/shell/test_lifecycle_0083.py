# 连接生命周期缺陷回归(changes/0083):
# ① BUG-1 顶替链 A←B←C —— B 在 `_displace(A)` 的 await 窗内被 C 顶掉,恢复后不得复活用户、不得抹占座清理表。
# ② BUG-6 慢客户端被丢弃 —— drop 后 Receiver 必须真的停下,不再往 inbox 投命令(幽灵命令源)。
# 两条都必须在真协程下跑:同步单测拿不到「await 窗口」这个东西。

import asyncio

from app.core.domain import UserState
from app.core.enums import UserStatus
from app.shell import timer as timer_mod
from app.shell.connection import Connection
from app.shell.receiver import run_receiver
from app.core.events import Broadcast
from app.db.engine import make_engine, make_sessionmaker
from app.wire.server import UserStatusChanged
from sqlalchemy.pool import StaticPool
from tests.builders import make_world, room_with, seat
from tests.shell._fakes import FakeWS, Shell


class _SlowCloseWS(FakeWS):
    # close() 卡在一个可控闸门上:用来把「顶替旧连接」这一步**停在 await 窗口里**,
    # 好让第三条连接在窗内插进来顶掉正在顶替别人的那一条。
    def __init__(self) -> None:
        super().__init__()
        self.close_entered = asyncio.Event()  # 已进入 close(= 顶替者已停在窗口里)
        self.close_gate = asyncio.Event()  # 测试放行闸门

    async def close(self, code: int = 1000) -> None:
        self.close_entered.set()
        await self.close_gate.wait()
        await super().close(code)


def _sm():
    # 未建表的 sessionmaker:本文件不走 JoinRoom(不读 DB),够用。
    return make_sessionmaker(
        make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    )


def _seated_world():
    # alice 在座(断线才走「标 OFFLINE 保座」那条路,座位泄漏才有得测);bob 陪着,免得清理后房被销毁。
    return make_world(
        rooms={
            "r1": room_with(
                seats=[seat("alice", 50), seat("bob", 50)],
                users_in_room={"alice": UserStatus.SITTING_IN, "bob": UserStatus.SITTING_IN},
            )
        },
        users={
            "alice": UserState(uid=1, nickname="alice", points=500, room="r1"),
            "bob": UserState(uid=2, nickname="bob", points=500, room="r1"),
        },
    )


async def _settle(cond, timeout: float = 1.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.005)
    return cond()


async def _shutdown(tasks) -> None:
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass


# ── ① BUG-1:顶替链 A←B←C ──

async def test_displacement_chain_does_not_revive_offline_user(monkeypatch):
    # B 登记后卡在 `_displace(A)` 的 await 里,窗内被 C 顶掉;C 随后断线(alice 转 OFFLINE、占座窗口起算)。
    # B 这时才恢复——它必须认出「我已经不是当前连接」就地退出:
    #   不投 Connect(否则把已 OFFLINE 的 alice 复活成在线,而 `_cleanup` 只回收 OFFLINE 座位);
    #   不 cancel_cleanup(否则抹掉 C 刚装的占座定时项,清理再也不触发)。
    # 两条任一失守,座位与桌上筹码就永久泄漏。
    clock = type("C", (), {"t": 1000.0})()
    monkeypatch.setattr(timer_mod, "now", lambda: clock.t)
    world = _seated_world()
    sh = Shell(world)
    sm = _sm()
    gl = asyncio.create_task(sh.gameloop.run())
    a = Connection.create(nick="alice", session_id="s1", ws=_SlowCloseWS())
    rxa = asyncio.create_task(run_receiver(a, sh.conns, sh.inbox, sh.timer, sm, world, sh.persist))
    await _settle(lambda: sh.conns.is_current(a))

    b = Connection.create(nick="alice", session_id="s2", ws=FakeWS())
    rxb = asyncio.create_task(run_receiver(b, sh.conns, sh.inbox, sh.timer, sm, world, sh.persist))
    await _settle(lambda: a.ws.close_entered.is_set())
    assert sh.conns.is_current(b) and b.sender_task is None  # B 已登记、还卡在顶替 A 的窗口里

    c = Connection.create(nick="alice", session_id="s3", ws=FakeWS())
    rxc = asyncio.create_task(run_receiver(c, sh.conns, sh.inbox, sh.timer, sm, world, sh.persist))
    await _settle(lambda: sh.conns.is_current(c) and b.ws.closed)  # C 顶掉 B(B 仍卡着)

    await c.ws.close()  # C 断线 → arm_cleanup + Disconnect
    await _settle(lambda: rxc.done() and world.rooms["r1"].users_in_room["alice"] is UserStatus.OFFLINE)
    assert world.rooms["r1"].seats[0] is not None  # 座位保留,等占座窗口满由 Cleanup 回收

    a.ws.close_gate.set()  # 放行:B 从 `_displace(A)` 的 await 里恢复
    try:
        assert await _settle(lambda: rxb.done())
        # —— 先断言「损害没有发生」(这两条才是缺陷本体)——
        await asyncio.sleep(0.02)  # 给「若真投了 Connect」留出被处理的时间
        assert world.rooms["r1"].users_in_room["alice"] is UserStatus.OFFLINE  # 没被复活
        clock.t += 9999  # 占座窗口满
        sh.timer.tick()
        assert await _settle(lambda: world.rooms["r1"].seats[0] is None)  # 清理照常触发,座位真的回收了
        assert "alice" not in world.users  # 驱逐回大厅
        # 桌上那 50 分随退筹 PointsWrite 回到全局积分(500 + 50),没有跟着座位一起泄漏
        alice_writes = [w for w in sh.persist.snapshot() if getattr(w, "uid", None) == 1]
        assert alice_writes and alice_writes[-1].points == 550
        # —— 再断言退出姿势:就地返回、没起 Sender(不留悬空协程)——
        assert rxb.exception() is None and b.sender_task is None
    finally:
        await _shutdown((rxa, rxb, rxc, gl))


# ── ② BUG-6:慢客户端被丢弃后不得继续投命令 ──

async def test_dropped_slow_client_stops_feeding_inbox():
    # outbound 满 → dispatch 丢连接。此前只 unregister,ws 还开着、Receiver 还阻塞在 receive:
    # 客户端继续发帧就继续往 inbox 投命令(一条「已经不存在」的连接仍在驱动状态机),
    # 且它重连时同一 nick 会同时挂两个 Receiver。drop 必须把它的协程一并终结。
    world = _seated_world()
    sh = Shell(world)
    conn = Connection.create(nick="alice", session_id="s1", ws=FakeWS())
    rx = asyncio.create_task(run_receiver(conn, sh.conns, sh.inbox, sh.timer, _sm(), world, sh.persist))
    await _settle(lambda: sh.conns.is_current(conn) and conn.receiver_task is not None)
    sh.inbox_drain()  # 清掉接入时的 Connect(本用例不起 GameLoop,手工记账)

    msg = UserStatusChanged(nickname="alice", status=UserStatus.SITTING_IN, seat_position=0)
    while not conn.outbound.full():  # 灌满 outbound = 慢客户端的判定条件
        conn.outbound.put_nowait(msg)
    sh.dispatcher.dispatch(Broadcast(room="r1", msg=msg))  # 再来一条 → QueueFull → drop

    assert sh.conns.get("alice") is None  # 停路由
    assert await _settle(lambda: rx.done())
    assert rx.cancelled()  # Receiver 真的停了(此前它还阻塞在 receive 上)
    assert conn.sender_task is None or conn.sender_task.cancelled() or conn.sender_task.done()
    sh.inbox_drain()  # 清掉 drop 投的 Disconnect

    conn.ws.feed('{"type":"sit_down","seat":0}')  # 幽灵客户端继续发帧
    await asyncio.sleep(0.05)
    assert sh.inbox_drain() == []  # 不再有任何命令进 inbox
