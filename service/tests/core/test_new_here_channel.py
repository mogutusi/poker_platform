"""0084:`new_here` 的传达渠道 —— `UserStatusChanged.new_here` + `_start_hand` 末尾对变了的座位补广播。

此前这个标志只活在 `StateSnapshot.SeatView` 里,而它恰恰在 `_start_hand` 末尾被重标,**没有任何事件承载**
(0082·A 记的缺口),客户端那份打完一手就过期。规则本身见 rules.md ①「入局与防躲盲」,本篇只测「怎么传达」。

判据统一是「值真的变了才发」:稳态牌桌每手 0 条,不刷屏。
"""

from app.core.commands import Disconnect, SetUserStatus, SitDown, StartHand
from app.core.enums import UserStatus
from app.core.events import Broadcast, Personal
from app.wire.server import HandStarted, HoleCards, UserStatusChanged
from tests.builders import DECK, T0, make_table, make_world, room_with, run, seat
from app.core.domain import UserState


def _status_msgs(events) -> list[UserStatusChanged]:
    return [e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, UserStatusChanged)]


def _start(world, origin, seat_idx):
    return run(world, StartHand(origin=origin, seat=seat_idx, started_at=T0, deck=DECK))


# ── ① 开局:被发牌者清 new_here → 必须有事件说 ──

def test_start_hand_broadcasts_cleared_new_here_for_dealt_players():
    # 两个新人首次开局(bootstrap 全员免付发牌):两人的 new_here 都 True→False,各来一条。
    world = make_table({0: seat("A", 100), 1: seat("B", 100)})
    world, events, err = _start(world, "A", 0)
    assert err is None
    msgs = _status_msgs(events)
    assert {(m.nickname, m.seat_position, m.new_here) for m in msgs} == {("A", 0, False), ("B", 1, False)}
    assert all(m.status is UserStatus.PLAYING for m in msgs)  # 顺带把「进手了」这个状态也如实带上
    # 广播内容与 world 一致(不是凭空造的值)
    for m in msgs:
        assert world.rooms["r1"].seats[m.seat_position].new_here is False


def test_start_hand_broadcasts_re_marked_new_here_for_players_who_missed_the_hand():
    # C 坐出 → 本手不发牌 → 末尾被重标 new_here=True(防躲盲)。这一条此前完全没有传达渠道。
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False), 2: seat("C", 100, new_here=False)},
        statuses={"C": UserStatus.SITTING_OUT},
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    msgs = _status_msgs(events)
    # A/B 本就 new_here=False 且被发牌 → 没变 → 不发;只有 C 变了
    assert [(m.nickname, m.seat_position, m.new_here, m.status) for m in msgs] == [
        ("C", 2, True, UserStatus.SITTING_OUT)
    ]
    assert world.rooms["r1"].seats[2].new_here is True  # 与 world 一致


def test_start_hand_says_nothing_when_no_seat_changed():
    # 稳态牌桌(全员上手都在局、本手都被发牌):一条都不发——「只发真的变了的」正是防刷屏的判据。
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    assert _status_msgs(events) == []


def test_start_hand_status_broadcast_comes_after_hand_started_and_hole_cards():
    # 次序同手尾状态广播(0082):先知道这手怎么开的,再知道各座位落到什么状态。
    world = make_table({0: seat("A", 100), 1: seat("B", 100)})
    world, events, err = _start(world, "A", 0)
    assert err is None
    kinds = [
        "hand_started" if isinstance(e, Broadcast) and isinstance(e.msg, HandStarted)
        else "hole_cards" if isinstance(e, Personal) and isinstance(e.msg, HoleCards)
        else "status" if isinstance(e, Broadcast) and isinstance(e.msg, UserStatusChanged)
        else "other"
        for e in events
    ]
    first_status = kinds.index("status")
    assert kinds.index("hand_started") < first_status
    assert max(i for i, k in enumerate(kinds) if k == "hole_cards") < first_status


# ── ② 其余四处构造点:`new_here` 必须如实填,不能让前端去猜 ──

def test_sit_down_states_new_here_instead_of_leaving_the_client_to_guess():
    # 前端此前在收到这条时硬写 new_here=true(替服务器裁定规则)。现在由服务器说。
    world = make_world(
        rooms={"r1": room_with(users_in_room={"W": UserStatus.WATCHING})},
        users={"W": UserState(uid=1, nickname="W", points=500, room="r1")},
    )
    world, events, err = run(world, SitDown(origin="W", seat=2, wait_for_big_blind=False))
    assert err is None
    (msg,) = _status_msgs(events)
    assert (msg.seat_position, msg.new_here) == (2, True)  # 新座位欠一个入局费(rules.md ①)


def test_standing_up_reports_new_here_none_like_seat_position():
    # 起身 → 观战:座位没了,两个字段一起为 None(同一语义,不留「无座却还报 new_here」的怪态)。
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)})
    world, events, err = run(world, SetUserStatus(origin="A", status=UserStatus.WATCHING))
    assert err is None
    (msg,) = _status_msgs(events)
    assert (msg.seat_position, msg.new_here) == (None, None)


def test_disconnect_keeps_reporting_the_seats_new_here():
    # 在座断线:保座,所以 new_here 照旧有值(不是 None)——重连方要能据此对齐。
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)})
    world, events, err = run(world, Disconnect(origin=None, nick="A"))
    assert err is None
    (msg,) = _status_msgs(events)
    assert (msg.status, msg.seat_position, msg.new_here) == (UserStatus.OFFLINE, 0, False)


def test_hand_end_status_broadcast_carries_new_here_too():
    # 0082 的手尾状态广播同样要带:那批人刚打完一手 ⇒ new_here=False。
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)})
    world, _, err = _start(world, "A", 0)
    assert err is None
    from app.core.commands import PlayerAction
    from app.core.enums import PlayerActionType

    # 两人局:该行动的人弃牌 → 只剩一人 → 立即结算收尾(走 _finalize_hand 的状态广播那条路)。
    # 行动者从 hand 里取,不硬写 —— 座位号与 players 下标是两回事(0078 踩过)。
    hand = world.rooms["r1"].hand
    actor = hand.players[hand.acting_position].nickname
    world, events, err = run(world, PlayerAction(origin=actor, action=PlayerActionType.FOLD))
    assert err is None
    msgs = _status_msgs(events)
    assert msgs and all(m.new_here is False for m in msgs)
    for m in msgs:
        assert world.rooms["r1"].seats[m.seat_position].new_here is False
