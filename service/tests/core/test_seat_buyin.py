"""P1:reduce 就座与买入(0015)—— SitDown / BuyIn / 起身(SetUserStatus→WATCHING)。

经 reduce 编排测试:观战→就座(new_here)、全局积分↔座位筹码转账(买入扣 / 起身还)、错误臂。
全局积分守恒(转账两端等额)重点核。SB=1、BB=2。
"""

from app.core.commands import BuyIn, PlayerAction, SetUserStatus, SitDown
from app.core.domain import UserState
from app.core.enums import HandStatus, PlayerStatus, UserStatus
from app.core.errors import ErrorCode
from app.core.events import Broadcast, Persist
from app.core.messages import PlayerBoughtIn, UserStatusChanged
from app.core.records import PointsWrite
from tests.builders import hand_world, make_table, make_world, player, room_with, run, seat


def _room(world, name="r1"):
    return world.rooms[name]


def _watching_world(points=100):
    # 一个房:A 在房观战(无座位、在 world.users);B 已就座占位(测占座冲突用)
    room = room_with(
        seats=[None, None, seat("B", 50, new_here=False)],
        users_in_room={"A": UserStatus.WATCHING, "B": UserStatus.SITTING_IN},
    )
    users = {
        "A": UserState(uid=0, nickname="A", points=points, room="r1"),
        "B": UserState(uid=1, nickname="B", points=0, room="r1"),
    }
    return make_world(rooms={"r1": room}, users=users)


def _seated_world(seat_chips=0, global_points=100, status=UserStatus.SITTING_IN):
    # A 已就座(seat 0),可设座位筹码 + 全局积分(make_table 默认全局积分 0,这里覆盖)
    world = make_table({0: seat("A", seat_chips, new_here=False)}, statuses={"A": status})
    world.users["A"].points = global_points
    return world


# ════════ SitDown:观战 → 就座 ════════
def test_sit_down_occupies_empty_seat_as_new_here():
    world = _watching_world()
    world, events, err = run(world, SitDown(origin="A", seat=0))
    assert err is None
    room = _room(world)
    s = room.seats[0]
    assert s is not None and s.nickname == "A" and s.points == 0
    assert s.new_here is True and s.wait_for_big_blind is False  # 默认付盲即玩(rules.md ①)
    assert room.users_in_room["A"] is UserStatus.SITTING_IN
    msg = next(e.msg for e in events if isinstance(e.msg, UserStatusChanged))
    assert msg.nickname == "A" and msg.status is UserStatus.SITTING_IN and msg.seat_position == 0


def test_sit_down_seat_taken():
    world = _watching_world()
    world, events, err = run(world, SitDown(origin="A", seat=2))  # 座 2 是 B
    assert err is not None and err.code is ErrorCode.SEAT_TAKEN and events == []
    assert _room(world).users_in_room["A"] is UserStatus.WATCHING  # 未动


def test_sit_down_out_of_range():
    world = _watching_world()
    world, events, err = run(world, SitDown(origin="A", seat=99))
    assert err is not None and err.code is ErrorCode.NOT_YOUR_SEAT and events == []


def test_sit_down_when_not_watching_rejected():
    # 已就座者再 SitDown → 仅观战者可入座
    world = _seated_world()
    world, events, err = run(world, SitDown(origin="A", seat=1))
    assert err is not None and err.code is ErrorCode.INVALID_STATUS_TRANSITION and events == []


def test_sit_down_not_in_room():
    world = _watching_world()
    world, events, err = run(world, SitDown(origin="ghost", seat=0))
    assert err is not None and err.code is ErrorCode.NOT_IN_ROOM and events == []


# ════════ BuyIn:全局积分 → 座位筹码 ════════
def test_buy_in_transfers_global_to_seat():
    world = _seated_world(seat_chips=0, global_points=100)
    world, events, err = run(world, BuyIn(origin="A", seat=0, amount=60))
    assert err is None
    room = _room(world)
    assert room.seats[0].points == 60 and world.users["A"].points == 40  # 转账
    assert world.users["A"].points + room.seats[0].points == 100  # 守恒(全局↔座位)
    pw = next(e.payload for e in events if isinstance(e, Persist) and isinstance(e.payload, PointsWrite))
    assert pw.uid == 0 and pw.points == 40  # 按 uid、落最新全局值
    bought = next(e.msg for e in events if isinstance(e.msg, PlayerBoughtIn))
    assert bought.nickname == "A" and bought.seat_position == 0 and bought.amount == 60 and bought.seat_points == 60


def test_buy_in_adds_to_existing_stack():
    world = _seated_world(seat_chips=20, global_points=100)
    world, _, err = run(world, BuyIn(origin="A", seat=0, amount=30))
    assert err is None
    room = _room(world)
    assert room.seats[0].points == 50 and world.users["A"].points == 70  # 叠加到已有筹码


def test_buy_in_insufficient_points():
    world = _seated_world(seat_chips=0, global_points=40)
    world, events, err = run(world, BuyIn(origin="A", seat=0, amount=60))
    assert err is not None and err.code is ErrorCode.INSUFFICIENT_POINTS and events == []
    assert world.users["A"].points == 40 and _room(world).seats[0].points == 0  # 未动


def test_buy_in_non_positive_amount():
    # 0 与负额都非法(负额尤其危险:user.points -= 负 会凭空加分),都走 INVALID_BUY_IN
    world = _seated_world(seat_chips=0, global_points=100)
    for bad in (0, -10):
        w, events, err = run(world, BuyIn(origin="A", seat=0, amount=bad))
        assert err is not None and err.code is ErrorCode.INVALID_BUY_IN and events == []
    assert world.users["A"].points == 100 and _room(world).seats[0].points == 0  # 未动


def test_buy_in_not_own_seat():
    world = _watching_world()  # 座 2 是 B、A 观战
    world, events, err = run(world, BuyIn(origin="A", seat=2, amount=10))
    assert err is not None and err.code is ErrorCode.NOT_YOUR_SEAT and events == []


def test_buy_in_out_of_range_seat():
    world = _seated_world(seat_chips=0, global_points=100)
    world, events, err = run(world, BuyIn(origin="A", seat=99, amount=10))
    assert err is not None and err.code is ErrorCode.NOT_YOUR_SEAT and events == []


def test_buy_in_during_hand_rejected():
    # 局中(PLAYING)筹码已锁入本手 → 不可买入
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.FLOP, last_bet=0, acting_position=0, contributed={"A": 2, "B": 2},
    )
    world.users["A"].points = 100  # 给点全局积分,确保拒绝不是因积分不足
    world, events, err = run(world, BuyIn(origin="A", seat=0, amount=10))
    assert err is not None and err.code is ErrorCode.HAND_IN_PROGRESS and events == []


# ════════ 起身(SetUserStatus → WATCHING):腾座 + 退筹回全局 ════════
def test_stand_up_releases_seat_and_returns_chips():
    world = _seated_world(seat_chips=80, global_points=20)
    world, events, err = run(world, SetUserStatus(origin="A", status=UserStatus.WATCHING))
    assert err is None
    room = _room(world)
    assert room.users_in_room["A"] is UserStatus.WATCHING and room.seats[0] is None  # 腾座
    assert world.users["A"].points == 100  # 座位筹码 80 退回全局(20→100)
    pw = next(e.payload for e in events if isinstance(e, Persist) and isinstance(e.payload, PointsWrite))
    assert pw.uid == 0 and pw.points == 100
    msg = next(e.msg for e in events if isinstance(getattr(e, "msg", None), UserStatusChanged))
    assert msg.nickname == "A" and msg.status is UserStatus.WATCHING and msg.seat_position is None
    assert "A" in world.users  # 起身 ≠ 离房:仍在 world.users(回观战、未驱逐)


def test_stand_up_while_playing_rejected():
    # 局中(PLAYING)不可起身离座(只能请求坐出延手尾,见 0014)
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.FLOP, last_bet=0, acting_position=0, contributed={"A": 2, "B": 2},
    )
    world, events, err = run(world, SetUserStatus(origin="A", status=UserStatus.WATCHING))
    assert err is not None and err.code is ErrorCode.INVALID_STATUS_TRANSITION and events == []
    assert _room(world).users_in_room["A"] is UserStatus.PLAYING and _room(world).seats[0] is not None
