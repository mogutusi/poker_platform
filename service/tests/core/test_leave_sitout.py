"""P1:reduce 局中生命周期 —— LeaveRoom / SITTING_OUT / Disconnect / Cleanup(rules.md ④)。

经 reduce 编排测试:局中离桌即时 auto-fold + 手尾驱逐、坐出延到手尾、断线标 OFFLINE 保座、
清理 staleness。退筹/释座/驱逐的钱路与守恒重点核。SB=1、BB=2。
"""

from app.core.commands import Cleanup, Disconnect, LeaveRoom, PlayerAction, SetUserStatus, Timeout
from app.core.enums import HandStatus, PlayerActionType, PlayerStatus, RoomStatus, UserStatus
from app.core.errors import ErrorCode
from app.core.events import Broadcast, ClearAction, Personal, Persist, TurnChanged
from app.core.messages import HandEnded, HandShowDown, PlayerActed, UserLeft, UserStatusChanged
from app.core.records import PointsWrite
from app.core.domain import UserState
from tests.builders import (
    card,
    hand_world,
    make_table,
    make_world,
    player,
    room_with,
    run,
    seat,
)

FOLD = PlayerActionType.FOLD
CHECK = PlayerActionType.CHECK
BET = PlayerActionType.BET

BOARD = (card("Ah"), card("Kd"), card("Qc"), card("2s"), card("7h"))
FLOP, TURN, RIVER = (BOARD[0], BOARD[1], BOARD[2]), BOARD[3], BOARD[4]
TRIP_ACES = (card("Ac"), card("Ad"))  # 最强
TRIP_KINGS = (card("Kc"), card("Ks"))  # 次强
ACE_HIGH = (card("3c"), card("4d"))  # 最弱


def _room(world, name="r1"):
    return world.rooms[name]


def _persist_points(events):
    return [e.payload for e in events if isinstance(e, Persist) and isinstance(e.payload, PointsWrite)]


def _user_left(events):
    return [e for e in events if isinstance(getattr(e, "msg", None), UserLeft)]


# ════════ ④.2 局中离桌致单人剩余 → 手立即结束 + 驱逐 ════════
def test_leave_in_hand_to_one_ends_and_evicts():
    # heads-up:轮到 A,A LeaveRoom → 即时 auto-fold(即便能 check)→ 只剩 B → 手结束;A 手尾驱逐
    world = hand_world(
        [player("A", 90, seat=0), player("B", 90, seat=1)],
        button=0, status=HandStatus.FLOP, last_bet=0, acting_position=0,
        contributed={"A": 10, "B": 10},
    )
    world, events, err = run(world, LeaveRoom(origin="A"))
    assert err is None
    room = _room(world)
    assert room.hand is None and room.status is RoomStatus.PENDING_START
    # A 被驱逐:座位释放、移出 users_in_room、移出 world.users
    assert room.seats[0] is None and "A" not in room.users_in_room and "A" not in world.users
    # A 即时 auto-fold(即便 last_bet=0 能 check)→ PlayerActed(FOLD)
    acted = next(e.msg for e in events if isinstance(e.msg, PlayerActed))
    assert acted.action is FOLD and acted.nickname == "A"
    # B 收池、SITTING_IN、留座;无摊牌
    assert room.seats[1].points == 110 and room.users_in_room["B"] is UserStatus.SITTING_IN
    assert all(not isinstance(e.msg, HandShowDown) for e in events if isinstance(e, Broadcast))
    # 驱逐钱路:退 A 剩余栈 90 回全局积分(PointsWrite,按不可变 uid)+ UserLeft(Broadcast 给留下者 + Personal 回执本人)
    pw = _persist_points(events)
    assert len(pw) == 1 and pw[0].uid == 0 and pw[0].points == 90  # hand_world 给玩家 0=A uid 0
    left = _user_left(events)
    assert {type(e).__name__ for e in left} == {"Broadcast", "Personal"}
    assert all(e.msg.nickname == "A" and e.msg.seat_position == 0 for e in left)
    # 守恒:B 桌上 110 + A 退回全局 90 == 锁入 200
    assert room.seats[1].points + 90 == 200


# ════════ ④ 离桌者是唯一最高投入者(高注后离桌)→ 未叫注 forfeit 给在局者,不退离桌者 ════════
def test_leave_by_lone_high_bettor_forfeits_uncalled_bet():
    # A flop 下注 30(本街唯一最高)、C 已弃、轮到 B;A LeaveRoom 折掉 → 只剩 B → B 通吃,A 不退未叫注。
    # 防回归:旧 sidepot 会把 A 的未叫注 30 退回弃牌的 A、B 被少分 30(离桌反获利)。
    world = hand_world(
        [
            player("A", 60, seat=0, bet_amount=30, has_acted=True),  # 唯一最高投入者
            player("B", 90, seat=1, bet_amount=0),  # 轮到他、面对 30
            player("C", 90, seat=2, status=PlayerStatus.FOLDED),  # 先前已弃
        ],
        button=0, status=HandStatus.FLOP, last_bet=30, acting_position=1,
        contributed={"A": 10, "B": 10, "C": 10},
    )
    world, events, err = run(world, LeaveRoom(origin="A"))
    assert err is None
    room = _room(world)
    assert room.hand is None
    ended = next(e.msg for e in events if isinstance(getattr(e, "msg", None), HandEnded))
    assert {w.nickname: w.amount for w in ended.winnings} == {"B": 60}  # B 通吃全池(含 A forfeit 的 30)
    assert ended.refunds == ()  # 关键:离桌的唯一最高投入者**不退**未叫注
    pw = _persist_points(events)
    assert len(pw) == 1 and pw[0].points == 60  # A 仅带走开局剩余栈 60(未叫注 30 forfeit、不带走)
    assert room.seats[1].points == 150 and room.seats[2].points == 90  # B 赢 60、C 弃保留 90
    assert 60 + 150 + 90 == 300  # 守恒


# ════════ 多人同手离桌:手尾按 sorted(leaving) 确定序逐个驱逐 ════════
def test_two_leavers_both_evicted_in_sorted_order():
    # 3 人,轮到 B;A(非行动者)先离 → 标 leaving 不结束;再 B 离(行动者)→ 只剩 C → 结束,A、B 同手尾驱逐
    world = hand_world(
        [player("A", 90, seat=0), player("B", 90, seat=1), player("C", 90, seat=2)],
        button=0, status=HandStatus.FLOP, last_bet=0, acting_position=1,
        contributed={"A": 10, "B": 10, "C": 10},
    )
    world, _, err = run(world, LeaveRoom(origin="A"))  # A 非行动者:fold + 标 leaving,手不结束
    assert err is None and _room(world).hand is not None
    world, events, err = run(world, LeaveRoom(origin="B"))  # B 行动者:fold → 只剩 C → 结束
    assert err is None
    room = _room(world)
    assert room.hand is None
    # A、B 均驱逐;两笔 PointsWrite 按 sorted uid(A=0 在前、B=1 在后)、各退剩余栈 90
    assert "A" not in world.users and "B" not in world.users and room.seats[0] is None and room.seats[1] is None
    pw = _persist_points(events)
    assert [p.uid for p in pw] == [0, 1] and all(p.points == 90 for p in pw)  # 确定序、两笔
    left = _user_left(events)
    assert {e.msg.nickname for e in left} == {"A", "B"}  # 两人各有 UserLeft(Broadcast+Personal)
    assert room.seats[2].points == 120 and room.users_in_room["C"] is UserStatus.SITTING_IN  # C 通吃 120
    assert 90 + 90 + 120 == 300  # 守恒(两离桌者退回 + C 桌上)


# ════════ ④.1 局中(非行动者)离桌 → 即时 fold + 延到手尾驱逐 ════════
def test_leave_in_hand_non_acting_defers_then_evicts():
    # 3 人,轮到 B;A(非行动者)LeaveRoom → A 即时 fold、标 leaving,但牌局不推进(B 继续)
    world = hand_world(
        [player("A", 90, seat=0), player("B", 90, seat=1), player("C", 90, seat=2)],
        button=0, status=HandStatus.FLOP, last_bet=0, last_raise_size=2, acting_position=1,
        contributed={"A": 10, "B": 10, "C": 10},
    )
    world, events, err = run(world, LeaveRoom(origin="A"))
    assert err is None
    room = _room(world)
    h = room.hand
    assert h is not None  # 仍 2 人未弃,手未结束
    assert h.players[0].status is PlayerStatus.FOLDED  # A 即时 auto-fold
    assert "A" in room.leaving  # 标记手尾驱逐
    assert "A" in room.users_in_room and "A" in world.users  # 尚未驱逐(延到手尾)
    assert h.players[h.acting_position].nickname == "B"  # 非行动者离桌不推进 turn
    acted = next(e.msg for e in events if isinstance(e.msg, PlayerActed))
    assert acted.action is FOLD and acted.nickname == "A"
    assert not any(isinstance(e, TurnChanged) for e in events)  # turn 未变,不重起倒计时

    # 推进至手结束:B 下注、C 弃 → 只剩 B → 结算 + A 手尾驱逐
    world, _, err = run(world, PlayerAction(origin="B", action=BET, bet_amount=10))
    assert err is None
    world, events, err = run(world, PlayerAction(origin="C", action=FOLD))
    assert err is None
    room = _room(world)
    assert room.hand is None
    assert room.seats[0] is None and "A" not in room.users_in_room and "A" not in world.users  # A 驱逐
    pw = _persist_points(events)
    assert len(pw) == 1 and pw[0].points == 90  # A 剩余栈 90 退回全局
    assert room.users_in_room["B"] is UserStatus.SITTING_IN and room.users_in_room["C"] is UserStatus.SITTING_IN
    # 守恒:B 桌上 + C 桌上 + A 退回 90 == 锁入 300
    assert room.seats[1].points + room.seats[2].points + 90 == 300


def test_leave_non_acting_to_one_ends_immediately():
    # heads-up:轮到 B,A(非行动者)LeaveRoom → A 即时 fold → 只剩 B → 本手立即结束(非行动者 len==1 臂)
    world = hand_world(
        [player("A", 90, seat=0), player("B", 90, seat=1)],
        button=0, status=HandStatus.FLOP, last_bet=0, acting_position=1,
        contributed={"A": 10, "B": 10},
    )
    world, events, err = run(world, LeaveRoom(origin="A"))
    assert err is None
    room = _room(world)
    assert room.hand is None and room.status is RoomStatus.PENDING_START  # 立即结束
    acted = next(e.msg for e in events if isinstance(getattr(e, "msg", None), PlayerActed))
    assert acted.action is FOLD and acted.nickname == "A" and acted.acting_position is None
    assert room.seats[0] is None and "A" not in world.users  # A 驱逐
    pw = _persist_points(events)
    assert len(pw) == 1 and pw[0].points == 90
    assert room.seats[1].points == 110 and room.users_in_room["B"] is UserStatus.SITTING_IN
    assert 110 + 90 == 200  # 守恒


# ════════ ④ 两手之间(不在手内)离桌 → 立即驱逐 ════════
def test_leave_between_hands_immediate_evict():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0
    )
    world, events, err = run(world, LeaveRoom(origin="A"))
    assert err is None
    room = _room(world)
    assert room.seats[0] is None and "A" not in room.users_in_room and "A" not in world.users
    assert room.seats[1] is not None and "B" in world.users  # B 不受影响
    pw = _persist_points(events)
    assert len(pw) == 1 and pw[0].points == 100  # A 桌上 100 退回全局
    assert any(isinstance(e, Broadcast) and isinstance(e.msg, UserLeft) for e in events)


def test_leave_watching_no_seat_no_pointswrite():
    # 观战者(无座位)离房 → 驱逐但无退筹 Persist(无座位筹码)
    room = room_with(
        seats=[seat("B", 100, new_here=False)],
        users_in_room={"B": UserStatus.READY_TO_PLAY, "A": UserStatus.WATCHING},
    )
    world = make_world(
        rooms={"r1": room},
        users={
            "A": UserState(uid=10, nickname="A", points=5, room="r1"),
            "B": UserState(uid=11, nickname="B", points=0, room="r1"),
        },
    )
    world, events, err = run(world, LeaveRoom(origin="A"))
    assert err is None
    assert "A" not in _room(world).users_in_room and "A" not in world.users
    assert _persist_points(events) == []  # 无座位 → 不动全局积分、不产 PointsWrite
    left = _user_left(events)
    assert any(e.msg.seat_position is None for e in left)


def test_leave_not_in_room_errors():
    world = make_table({0: seat("A", 100, new_here=False)}, button=0)
    world, events, err = run(world, LeaveRoom(origin="ghost"))
    assert err is not None and err.code is ErrorCode.NOT_IN_ROOM and events == []


# ════════ ④ 局中离桌的 ALLIN 者仍可赢、带走奖金 ════════
def test_leave_allin_player_can_still_win_then_evicted():
    # A 已全押(不能再 fold),LeaveRoom 只标 leaving;摊牌 A 三条 A 通吃 → 带奖金被驱逐
    world = hand_world(
        [
            player("A", 0, seat=0, status=PlayerStatus.ALLIN, has_acted=True, hole=TRIP_ACES),
            player("B", 50, seat=1, has_acted=False, hole=TRIP_KINGS),
        ],
        button=0, status=HandStatus.RIVER, last_bet=0, acting_position=1,
        contributed={"A": 50, "B": 50}, flop=FLOP, turn=TURN, river=RIVER,
    )
    world, events, err = run(world, LeaveRoom(origin="A"))
    assert err is None and events == []  # ALLIN 不能 fold,仅标 leaving、无即时事件
    room = _room(world)
    assert "A" in room.leaving and room.hand.players[0].status is PlayerStatus.ALLIN
    assert "A" in world.users  # 尚未驱逐

    world, events, err = run(world, PlayerAction(origin="B", action=CHECK))  # 收尾 → 摊牌
    assert err is None
    room = _room(world)
    assert room.hand is None
    assert room.seats[0] is None and "A" not in world.users  # A 驱逐
    pw = _persist_points(events)
    assert len(pw) == 1 and pw[0].points == 100  # A 赢得整池 100 带走
    assert room.seats[1].points == 50 and room.users_in_room["B"] is UserStatus.SITTING_IN
    # 隐私:UserLeft / PointsWrite 不含底牌
    assert all(not hasattr(e.msg, "hole_cards") for e in _user_left(events))
    assert not hasattr(pw[0], "hole_cards")
    # 守恒:A 退回全局 100 + B 桌上 50 == 锁入 150
    assert 100 + room.seats[1].points == 150


# ════════ ④.3 局中坐出 → 延到本手结束才转 SITTING_OUT ════════
def test_sitting_out_in_hand_defers_to_hand_end():
    world = hand_world(
        [
            player("A", 50, seat=0, has_acted=False, hole=ACE_HIGH),
            player("B", 50, seat=1, has_acted=False, hole=TRIP_KINGS),
        ],
        button=0, status=HandStatus.RIVER, last_bet=0, acting_position=0,
        contributed={"A": 50, "B": 50}, flop=FLOP, turn=TURN, river=RIVER,
    )
    world, events, err = run(world, SetUserStatus(origin="A", status=UserStatus.SITTING_OUT))
    assert err is None and events == []  # 延到手尾,本手 PLAYING 不变、无即时事件
    room = _room(world)
    assert "A" in room.sitting_out_next
    assert room.users_in_room["A"] is UserStatus.PLAYING  # 本手仍在玩
    assert room.hand.players[0].status is PlayerStatus.ACTIVE  # 坐出不 fold

    world, _, err = run(world, PlayerAction(origin="A", action=CHECK))
    world, events, err = run(world, PlayerAction(origin="B", action=CHECK))  # river 关 → 摊牌
    assert err is None
    room = _room(world)
    assert room.hand is None
    assert room.users_in_room["A"] is UserStatus.SITTING_OUT  # 手尾转坐出
    assert room.users_in_room["B"] is UserStatus.SITTING_IN
    assert "A" in world.users and room.seats[0] is not None  # 坐出 ≠ 离桌:留座留账号
    assert room.sitting_out_next == set()  # 已清
    # B 三条 K 胜 → 收池 100;A 留剩余栈 50
    assert room.seats[1].points == 150 and room.seats[0].points == 50


def test_sitting_out_in_hand_rejects_non_sitting_out_target():
    # 局中(PLAYING)只接受 SITTING_OUT 延迟;请求 READY_TO_PLAY 等其它目标 → INVALID_STATUS_TRANSITION
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.FLOP, last_bet=0, acting_position=0, contributed={"A": 2, "B": 2}, flop=FLOP,
    )
    world, events, err = run(world, SetUserStatus(origin="A", status=UserStatus.READY_TO_PLAY))
    assert err is not None and err.code is ErrorCode.INVALID_STATUS_TRANSITION and events == []
    room = _room(world)
    assert room.users_in_room["A"] is UserStatus.PLAYING  # 未改
    assert "A" not in room.sitting_out_next  # 未误标坐出


# ════════ ④.4 断线 → 标 OFFLINE 保座(对比主动离桌的即时释座)════════
def test_disconnect_marks_offline_keeps_seat():
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.FLOP, last_bet=0, acting_position=0, contributed={"A": 2, "B": 2}, flop=FLOP,
    )
    world, events, err = run(world, Disconnect(origin=None, nick="A"))
    assert err is None
    room = _room(world)
    assert room.users_in_room["A"] is UserStatus.OFFLINE  # 标 OFFLINE
    assert room.seats[0] is not None and "A" in world.users  # 保座、保账号(等重连)
    assert room.hand.players[0].status is PlayerStatus.ACTIVE  # 仍在手内,轮到他由超时 fold
    # 断线不推进 turn(牌局不卡靠超时,不靠断线):acting_position 不变、无 TurnChanged
    assert room.hand.acting_position == 0 and not any(isinstance(e, TurnChanged) for e in events)
    msg = next(e.msg for e in events if isinstance(getattr(e, "msg", None), UserStatusChanged))
    assert msg.nickname == "A" and msg.status is UserStatus.OFFLINE and msg.seat_position == 0


def test_disconnect_already_offline_is_idempotent():
    # 重复 / 顶替触发的 Disconnect:已 OFFLINE → 幂等 no-op,不再广播
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.FLOP, last_bet=0, acting_position=0, contributed={"A": 2, "B": 2}, flop=FLOP,
    )
    world, _, err = run(world, Disconnect(origin=None, nick="A"))
    assert err is None and _room(world).users_in_room["A"] is UserStatus.OFFLINE
    world, events, err = run(world, Disconnect(origin=None, nick="A"))  # 第二次
    assert err is None and events == []  # 幂等:无变化、无事件


def test_disconnect_then_timeout_autofolds_keeps_seat():
    # 断线后轮到他 → 行动倒计时超时,默认动作 fold/check,仍保座(清理等 Cleanup)
    world = hand_world(
        [player("A", 100, seat=0, bet_amount=0), player("B", 100, seat=1), player("C", 100, seat=2)],
        status=HandStatus.FLOP, last_bet=10, acting_position=0,
        contributed={"A": 2, "B": 2, "C": 2}, flop=FLOP,
    )
    # A 面对 10、bet 0;先有人下注?这里直接令 acting=A 面对 last_bet=10(B 已下,简化为初始态)
    world, _, err = run(world, Disconnect(origin=None, nick="A"))
    world, events, err = run(world, Timeout(origin=None, nick="A", epoch=0))
    assert err is None
    room = _room(world)
    assert room.hand.players[0].status is PlayerStatus.FOLDED  # 超时默认 fold(面对注)
    assert room.users_in_room["A"] is UserStatus.OFFLINE  # 超时不改 UserStatus
    assert room.seats[0] is not None and "A" in world.users  # 仍保座(未到 Cleanup)


def test_disconnect_in_lobby_noop():
    world = make_table({0: seat("A", 100, new_here=False)}, button=0)
    world, events, err = run(world, Disconnect(origin=None, nick="lobby_user"))
    assert err is None and events == []  # 不在房 → 无 world 变化


# ════════ Cleanup(占座到期)── staleness:仅 OFFLINE 才退筹释座 ════════
def test_cleanup_offline_evicts():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=0, statuses={"A": UserStatus.OFFLINE},
    )
    world, events, err = run(world, Cleanup(origin=None, nick="A"))
    assert err is None
    room = _room(world)
    assert room.seats[0] is None and "A" not in room.users_in_room and "A" not in world.users
    pw = _persist_points(events)
    assert len(pw) == 1 and pw[0].points == 100  # 退座位筹码回全局


def test_cleanup_reconnected_ignored():
    # 已重连(非 OFFLINE)→ staleness 忽略,不退筹释座
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=0, statuses={"A": UserStatus.SITTING_IN},
    )
    world, events, err = run(world, Cleanup(origin=None, nick="A"))
    assert err is None and events == []
    assert _room(world).seats[0] is not None and "A" in world.users  # 安然无恙


def test_cleanup_in_hand_offline_defers_eviction():
    # 局中 OFFLINE 者(已被超时 fold)Cleanup → 标 leaving、延到手尾驱逐(不抽池中筹码)
    world = hand_world(
        [player("A", 90, seat=0, status=PlayerStatus.FOLDED), player("B", 50, seat=1), player("C", 50, seat=2)],
        button=0, status=HandStatus.FLOP, last_bet=0, acting_position=1, contributed={"A": 10, "B": 10, "C": 10},
    )
    _room(world).users_in_room["A"] = UserStatus.OFFLINE
    world, events, err = run(world, Cleanup(origin=None, nick="A"))
    assert err is None and events == []  # FOLDED:无 fold、仅标 leaving
    room = _room(world)
    assert "A" in room.leaving and room.hand is not None and "A" in world.users  # 延到手尾


# ════════ SetUserStatus 就座内切换(out-of-hand)+ 未实现边界 ════════
def test_set_status_ready_toggle_out_of_hand():
    world = make_table(
        {0: seat("A", 100, new_here=False)}, button=0, statuses={"A": UserStatus.SITTING_IN}
    )
    world, events, err = run(world, SetUserStatus(origin="A", status=UserStatus.READY_TO_PLAY))
    assert err is None
    assert _room(world).users_in_room["A"] is UserStatus.READY_TO_PLAY
    msg = next(e.msg for e in events if isinstance(e.msg, UserStatusChanged))
    assert msg.nickname == "A" and msg.status is UserStatus.READY_TO_PLAY and msg.seat_position == 0


def test_set_status_standup_not_yet_implemented():
    # 起身离座(→WATCHING)归后续座位簇,本簇占位 INTERNAL(不误判为合法)
    world = make_table(
        {0: seat("A", 100, new_here=False)}, button=0, statuses={"A": UserStatus.SITTING_IN}
    )
    world, events, err = run(world, SetUserStatus(origin="A", status=UserStatus.WATCHING))
    assert err is not None and err.code is ErrorCode.INTERNAL and events == []
    assert _room(world).users_in_room["A"] is UserStatus.SITTING_IN  # 未改


def test_set_status_illegal_transition():
    world = make_table(
        {0: seat("A", 100, new_here=False)}, button=0, statuses={"A": UserStatus.SITTING_OUT}
    )
    # SITTING_OUT → READY_TO_PLAY 不在自助转移表 → INVALID_STATUS_TRANSITION
    world, events, err = run(world, SetUserStatus(origin="A", status=UserStatus.READY_TO_PLAY))
    assert err is not None and err.code is ErrorCode.INVALID_STATUS_TRANSITION and events == []
