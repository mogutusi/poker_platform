"""Dispatcher:事件 → 物理落点路由(connection.md「dispatch」)。直接派发事件,验各 sink。"""

import asyncio

from app.core.commands import Disconnect
from app.core.events import Broadcast, ClearAction, Personal, Persist, TurnChanged
from app.core.enums import UserStatus
from app.core.domain import UserState
from app.core.records import PointsWrite
from app.shell import timer as timer_mod
from app.wire.server import HoleCards, UserStatusChanged
from app.core.cards import Card, CardRank, CardSuit
from tests.builders import make_world, room_with
from tests.shell._fakes import Shell, drain, make_conn


def _world():
    return make_world(
        rooms={"r1": room_with(users_in_room={"alice": UserStatus.WATCHING, "bob": UserStatus.WATCHING})},
        users={
            "alice": UserState(uid=1, nickname="alice", points=500, room="r1"),
            "bob": UserState(uid=2, nickname="bob", points=500, room="r1"),
        },
    )


def _msg():
    return UserStatusChanged(nickname="alice", status=UserStatus.SITTING_IN, seat_position=0)


def test_broadcast_reaches_connected_members_only():
    world = _world()
    sh = Shell(world)
    conns = sh.connect("alice")  # bob 在房但**无连接**
    sh.dispatcher.dispatch(Broadcast(room="r1", msg=_msg()))
    assert len(drain(conns["alice"])) == 1  # 有连接者收到
    # bob 无连接:dispatch 里 conns.get("bob") 为 None,自动跳过(不报错)


def test_broadcast_to_destroyed_room_is_noop():
    world = _world()
    sh = Shell(world)
    sh.connect("alice")
    sh.dispatcher.dispatch(Broadcast(room="ghost", msg=_msg()))  # 房不存在 → 容错跳过,不抛


def test_personal_routes_to_single_nick():
    world = _world()
    sh = Shell(world)
    conns = sh.connect("alice", "bob")
    hc = HoleCards(cards=(Card(CardRank.ACE, CardSuit.SPADES), Card(CardRank.KING, CardSuit.HEARTS)))
    sh.dispatcher.dispatch(Personal(nick="alice", msg=hc))
    assert len(drain(conns["alice"])) == 1  # 仅本人
    assert drain(conns["bob"]) == []


def test_persist_goes_to_buffer():
    sh = Shell(_world())
    sh.dispatcher.dispatch(Persist(payload=PointsWrite(uid=1, points=400)))
    assert len(sh.persist) == 1
    assert sh.persist.snapshot()[0] == PointsWrite(uid=1, points=400)  # 非手牌记录不盖戳,原样入缓冲


def test_persist_hand_record_stamps_end_time():
    # 手牌记录的 end_time core 留 None,shell 在 dispatch 盖墙钟(注入定值时钟验确切值)。
    from datetime import datetime, timezone

    from app.core.records import HandRecordWrite
    from app.shell.dispatch import Dispatcher

    world = _world()
    sh = Shell(world)
    t = datetime(2026, 6, 1, tzinfo=timezone.utc)
    d = Dispatcher(world, sh.conns, sh.persist, sh.timer, sh.inbox, now=lambda: t)
    d.dispatch(Persist(payload=HandRecordWrite(dedupe_key="r1:1", start_time=t, final_pot=0, participants=())))
    buffered = sh.persist.snapshot()
    assert len(buffered) == 1
    assert buffered[0].end_time == t  # shell 盖了手结束墙钟


def test_turn_changed_and_clear_action_drive_timer(monkeypatch):
    # B 组:同步调 Timer。验 TurnChanged 排了行动倒计时(tick 触发 Timeout)、ClearAction 取消之。
    clock = type("C", (), {"t": 1000.0})()
    monkeypatch.setattr(timer_mod, "now", lambda: clock.t)
    sh = Shell(_world())
    sh.dispatcher.dispatch(TurnChanged(room="r1", acting_nick="alice", epoch=3))
    clock.t += 9999
    sh.timer.tick()
    fired = sh.inbox_drain()
    assert len(fired) == 1 and fired[0].nick == "alice" and fired[0].epoch == 3
    # ClearAction 取消:再排再清 → 不触发
    sh.dispatcher.dispatch(TurnChanged(room="r1", acting_nick="bob", epoch=4))
    sh.dispatcher.dispatch(ClearAction(room="r1"))
    clock.t += 9999
    sh.timer.tick()
    assert sh.inbox_drain() == []


def test_slow_client_dropped_and_disconnect_enqueued():
    # outbound 满 → 丢连接(unregister)+ 投 Disconnect,不阻塞。
    world = _world()
    sh = Shell(world)
    conns = sh.connect("alice")
    alice = conns["alice"]
    while not alice.outbound.full():  # 灌满 outbound(OUTBOUND_MAX)
        alice.outbound.put_nowait(_msg())
    sh.dispatcher.dispatch(Broadcast(room="r1", msg=_msg()))  # 再来一条 → QueueFull → drop
    assert sh.conns.get("alice") is None  # 已 unregister,停路由
    fired = sh.inbox_drain()
    assert any(isinstance(c, Disconnect) and c.nick == "alice" for c in fired)
