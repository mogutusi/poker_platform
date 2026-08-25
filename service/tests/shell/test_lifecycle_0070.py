# 连接与会话生命周期修理回归(changes/0070):
# ① 观战者断线即清出房间(末人离房销房);在座者断线仍标 OFFLINE 保座。
# ② Receiver:接入拆断线倒计时(cancel_cleanup)、退出装表(arm_cleanup);被顶替旧连接不装表。
# ③ A1 坏链回归:在线静默不产生任何 Cleanup;断线后满窗必产生(旧实现「收帧续命+触发即删」在此必红)。
# ④ B4:会话过期 → 收帧/出站两侧都关连接(4401);未过期照常。

import asyncio

import pytest

from app import gameconfig
from app.auth.session import Session, SessionStore
from app.core.commands import Cleanup, Connect, Disconnect
from app.core.domain import Room, Seat, UserState, World
from app.core.enums import UserStatus
from app.core.reduce import reduce
from app.shell.timer import Timer
from app.shell.world import checkout, commit
from app.wire.server import UserLeft


def _world_with(nick: str, status: UserStatus, seated: bool) -> World:
    room = Room(seats=[None] * 4, small_blind=1, buy_in=100)
    room.users_in_room[nick] = status
    if seated:
        room.seats[0] = Seat(nickname=nick, points=50)
    world = World()
    world.rooms["r"] = room
    world.users[nick] = UserState(uid=1, nickname=nick, points=100, room="r")
    return world


def _run(world: World, cmd) -> tuple[list, object]:
    work = checkout(world, cmd)
    events, err = reduce(work, cmd)
    if err is None:
        commit(world, work)
    return events, err


# ── ① 观战者断线即清 ──

def test_watcher_disconnect_evicted_and_room_destroyed():
    # 观战者(无座无筹码)断线 → 即时离场;他是末人 → 空房归一销毁(0070:不留 OFFLINE 幽灵拖住销毁)。
    world = _world_with("w", UserStatus.WATCHING, seated=False)
    events, err = _run(world, Disconnect(origin=None, nick="w"))
    assert err is None
    assert "w" not in world.users  # 驱逐出全局用户表(回大厅语义)
    assert "r" not in world.rooms  # 末人离房 → 房销毁
    assert any(getattr(ev, "msg", None) and isinstance(ev.msg, UserLeft) for ev in events)


def test_watcher_disconnect_with_others_room_stays():
    world = _world_with("w", UserStatus.WATCHING, seated=False)
    world.rooms["r"].users_in_room["other"] = UserStatus.SITTING_IN
    world.rooms["r"].seats[1] = Seat(nickname="other", points=30)
    world.users["other"] = UserState(uid=2, nickname="other", points=0, room="r")
    events, err = _run(world, Disconnect(origin=None, nick="w"))
    assert err is None
    assert "w" not in world.rooms["r"].users_in_room  # 观战者清走
    assert "r" in world.rooms  # 还有别人 → 房保留


def test_seated_disconnect_still_marks_offline_and_keeps_seat():
    # 在座者断线:照旧 OFFLINE 保座(占座窗口语义不变);Cleanup 满窗才退筹释座。
    world = _world_with("p", UserStatus.SITTING_IN, seated=True)
    _, err = _run(world, Disconnect(origin=None, nick="p"))
    assert err is None
    assert world.rooms["r"].users_in_room["p"] is UserStatus.OFFLINE
    assert world.rooms["r"].seats[0] is not None  # 座位保留
    # 满窗 Cleanup:OFFLINE → 退筹驱逐(既有语义回归)
    _, err = _run(world, Cleanup(origin=None, nick="p"))
    assert err is None and "p" not in world.users


# ── ②③ Timer × 断线装表 / 在线静默零触发(A1 坏链回归)──

def test_idle_online_user_never_fires_cleanup(monkeypatch):
    # A1 回归主钉:在线用户不进保活表 → 静默任意久都不产生 Cleanup(旧实现每帧续命+自燃即删,在此必红)。
    inbox: "asyncio.Queue" = asyncio.Queue()
    t = Timer(inbox)
    base = {"t": 1000.0}
    monkeypatch.setattr("app.shell.timer.now", lambda: base["t"])
    t.cancel_cleanup("alice")  # 接入:拆表(本就无表,幂等)
    base["t"] += gameconfig.LIVENESS_TIMEOUT * 100  # 在线静默极久
    t.tick()
    assert inbox.empty()  # 无任何 Cleanup
    t.arm_cleanup("alice")  # 此刻断线:装表
    base["t"] += gameconfig.LIVENESS_TIMEOUT + 1
    t.tick()
    cmd = inbox.get_nowait()
    assert isinstance(cmd, Cleanup) and cmd.nick == "alice"  # 断线后满窗必触发


# ── ④ B4:会话过期强制断开 ──

class _FakeWS:
    # 最小 fake:记录 close 码;receive_bytes 给一帧占位(过期检查在 open 之前即拦)。
    def __init__(self):
        self.closed_code: int | None = None

    async def receive_bytes(self) -> bytes:
        return b"\x00" * 64

    async def send_bytes(self, data: bytes) -> None:
        pass

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


def _expired_conn():
    from app.auth.channel import SecureChannel
    from app.shell.connection import Connection

    store = SessionStore(ttl_seconds=60)
    _, session = store.create("alice", "Alice", now=1000.0)  # exp = 1060
    channel = SecureChannel.derive(session.token, gameconfig.WS_FRAME_MAX_BYTES)
    return Connection.create(nick="Alice", session_id="sid", ws=_FakeWS(), channel=channel, session=session)


async def test_recv_frame_closes_expired_session(monkeypatch):
    from app.shell import receiver

    conn = _expired_conn()
    monkeypatch.setattr(receiver.time, "time", lambda: 2000.0)  # now > exp
    assert await receiver._recv_frame(conn) is None
    assert conn.ws.closed_code == 4401  # 同握手拒码:须重登


async def test_sender_closes_expired_session(monkeypatch):
    from app.shell import sender
    from app.wire.server import ErrorMessage

    conn = _expired_conn()
    monkeypatch.setattr(sender.time, "time", lambda: 2000.0)
    conn.outbound.put_nowait(ErrorMessage(code="INTERNAL", detail=""))
    await sender.sender_loop(conn)  # 过期:发送前拦截 → close(4401) → return
    assert conn.ws.closed_code == 4401


async def test_recv_frame_ok_before_expiry(monkeypatch):
    # 未过期:过期检查放行,进入正常 open 流程(此帧是垃圾 → FrameError → 4400,证明走到了 open)。
    from app.shell import receiver

    conn = _expired_conn()
    monkeypatch.setattr(receiver.time, "time", lambda: 1030.0)  # now < exp
    assert await receiver._recv_frame(conn) is None
    assert conn.ws.closed_code == 4400  # 不是 4401:过期检查放行,倒在 MAC(垃圾帧)


async def test_revoked_session_closes_the_live_connection(monkeypatch):
    # 0097(BUG-8)的整条链:吊销 → 那条**已经连着**的 ws 在下一帧被 4401 关掉。
    # 这是吊销「真的生效」的判据。只把表项 pop 掉是不够的:conn 持有的是 Session 对象与从它派生的
    # channel,收发两侧都只比对 conn.session.expires_at、从不回头查表(所以 revoke 必须判死对象)。
    from app.shell import receiver
    from app.auth.channel import SecureChannel
    from app.shell.connection import Connection

    store = SessionStore(ttl_seconds=60)
    sid, session = store.create("alice", "Alice", now=1000.0)  # exp = 1060,尚未过期
    channel = SecureChannel.derive(session.token, gameconfig.WS_FRAME_MAX_BYTES)
    conn = Connection.create(nick="Alice", session_id=sid, ws=_FakeWS(), channel=channel, session=session)

    monkeypatch.setattr(receiver.time, "time", lambda: 1030.0)  # now < exp:不吊销的话这帧走到 open
    store.revoke(sid)

    assert await receiver._recv_frame(conn) is None
    assert conn.ws.closed_code == 4401  # 不是 4400:是被判死拦在 open 之前,不是烂帧
