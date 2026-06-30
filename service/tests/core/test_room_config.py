"""P1:reduce 房间参数配置(0043;0044 放开授权)—— SetSmallBlind / SetBuyIn。

授权 = **任何在房成员**(含观战者;无房主,见 lobby.md / changes/0044);仅留两道非授权门:在房(NOT_IN_ROOM)+
非局中(HAND_IN_PROGRESS,correctness:改盲污染进行中手牌)。big_blind 派生 = 2×SB;房配不落库(storage.md)→
只 Broadcast(RoomConfigChanged) 全房对齐、无 Persist。上下限按 gameconfig 由 shell 防护(见
tests/shell/test_room_config_guard.py),core 兜结构(在房 / 非局中 / 正额)。SB=1、BB=2。
"""

import copy

from app.core.commands import JoinRoom, OpenFreeEntryVote, SetBuyIn, SetSmallBlind
from app.core.domain import UserState
from app.core.enums import HandStatus, UserStatus
from app.core.errors import ErrorCode
from app.core.events import Persist
from app.core.rules import blinds
from app.wire.server import RoomConfigChanged, StateSnapshot
from tests.builders import hand_world, make_table, player, run, seat


def _room(world, name="r1"):
    return world.rooms[name]


def _member_world(small_blind=1, buy_in=100):
    # A 占 0 号位、B 占 1 号位;两手之间(PENDING_START,无 hand)。无房主:A/B 皆普通在房成员。
    return make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        small_blind=small_blind,
        buy_in=buy_in,
    )


def _add_watcher(world, nick="W", name="r1"):
    world.rooms[name].users_in_room[nick] = UserStatus.WATCHING
    world.users[nick] = UserState(uid=99, nickname=nick, points=0, room=name)
    return world


def _config_msg(events):
    return next(e.msg for e in events if isinstance(e.msg, RoomConfigChanged))


# ════════ SetSmallBlind:任何在房成员可改 ════════
def test_set_small_blind_by_member_updates_and_broadcasts_derived_big_blind():
    world = _add_watcher(_member_world(small_blind=1))  # 含观战者 W:验广播全房(派发含观战者)
    world, events, err = run(world, SetSmallBlind(origin="A", amount=5))
    assert err is None
    assert _room(world).small_blind == 5
    assert blinds.BIG_BLIND_MULTIPLE * _room(world).small_blind == 10  # 大盲派生,无存储字段
    msg = _config_msg(events)
    assert msg.small_blind == 5 and msg.big_blind == 10 and msg.buy_in == 100  # 完整配置快照
    assert all(e.room == "r1" for e in events)  # 广播到房 r1(dispatch 按 users_in_room 发,含观战者 W)
    assert "W" in _room(world).users_in_room  # W 是在房观战者 ⇒ 在广播派发面内
    assert not any(isinstance(e, Persist) for e in events)  # 房配不落库


def test_set_small_blind_by_non_seat0_member_allowed():
    # 无房主:非 0 号位、非庄的在座成员(B@座1)也能改(0044 放开,原 0043 此处会 NOT_ROOM_OWNER)。
    world = _member_world()
    world, events, err = run(world, SetSmallBlind(origin="B", amount=7))
    assert err is None and _room(world).small_blind == 7


def test_set_small_blind_by_watcher_allowed():
    # 「每个人」= 在房即可,含观战者(用户明示)。W 观战也能改。
    world = _add_watcher(_member_world())
    world, events, err = run(world, SetSmallBlind(origin="W", amount=4))
    assert err is None and _room(world).small_blind == 4


def test_set_small_blind_seat0_empty_member_allowed():
    # 0 号位空也不妨碍:授权与座位号无关,任何在房成员可改(钉死「去 seat-0 依赖」)。
    world = make_table({1: seat("A", 100, new_here=False)})  # A 在 1 号位,0 号位空
    world, events, err = run(world, SetSmallBlind(origin="A", amount=6))
    assert err is None and _room(world).small_blind == 6


def test_set_small_blind_by_sitting_out_non_seat0_member_allowed():
    # 与就座状态无关 + 非 0 号位:坐出的非 0 号位成员(B@座1,SITTING_OUT)仍可配置。
    # 必须用非 0 号位的 B(0043 会 NOT_ROOM_OWNER),否则用 A@座0 在 0043 也通过 = 测不到放开(false-green)。
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        statuses={"B": UserStatus.SITTING_OUT},
    )
    world, events, err = run(world, SetSmallBlind(origin="B", amount=8))
    assert err is None and _room(world).small_blind == 8


def test_set_small_blind_non_member_rejected_not_in_room():
    world = _member_world()
    world, events, err = run(world, SetSmallBlind(origin="Z", amount=5))  # 不在任何房
    assert err is not None and err.code is ErrorCode.NOT_IN_ROOM and events == []


def test_set_small_blind_mid_hand_rejected():
    # correctness 门(非授权):small_blind 喂下盲 + 各处大盲派生,已在 StartHand 锁入本手,改之会污染。
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.FLOP,
        contributed={"A": 2, "B": 2},
    )
    before = _room(world).small_blind
    world, events, err = run(world, SetSmallBlind(origin="B", amount=5))  # 任何成员局中皆拒
    assert err is not None and err.code is ErrorCode.HAND_IN_PROGRESS and events == []
    assert _room(world).small_blind == before


def test_set_small_blind_non_positive_rejected():
    # core 结构兜底(shell 已按 gameconfig 防过,但 core 自保不信外部):≤0 拒。
    for bad in (0, -5):
        world = _member_world()
        before = _room(world).small_blind
        world, events, err = run(world, SetSmallBlind(origin="A", amount=bad))
        assert err is not None and err.code is ErrorCode.INVALID_SMALL_BLIND and events == []
        assert _room(world).small_blind == before


def test_config_allowed_during_open_vote_and_vote_untouched():
    # 决策 2 双向钉死:免盲投票进行中**不** gate 房配(同处 PENDING_START 窗口);且房配路径**不**碰投票
    # (不 resolve、不动 candidates/approvals/waive)——防未来误把房配接进 _maybe_resolve_entry_vote。
    world = make_table(
        {
            0: seat("A", 100, new_here=False),  # 合格投票人
            1: seat("B", 100, new_here=False),  # 合格投票人
            2: seat("C", 100, new_here=True),  # new_here 候选
        }
    )
    world, _, err = run(world, OpenFreeEntryVote(origin="A"))
    assert err is None and _room(world).entry_vote is not None
    before = _room(world).entry_vote
    cand, appr = frozenset(before.candidates), set(before.approvals)
    for cmd in (SetSmallBlind(origin="A", amount=5), SetBuyIn(origin="A", amount=300)):
        world, events, err = run(world, cmd)
        assert err is None and any(isinstance(e.msg, RoomConfigChanged) for e in events)  # 房配未被投票 gate
        vote = _room(world).entry_vote
        assert vote is not None and vote.candidates == cand and vote.approvals == appr  # 投票原封不动
        assert vote.rejected is False and _room(world).waive_entry_for == set()  # 未 resolve、未免人


# ════════ SetBuyIn:任何在房成员改房间默认买入 ════════
def test_set_buy_in_by_member_updates_and_broadcasts():
    world = _member_world(small_blind=1, buy_in=100)
    world, events, err = run(world, SetBuyIn(origin="A", amount=250))
    assert err is None
    assert _room(world).buy_in == 250
    msg = _config_msg(events)
    assert msg.buy_in == 250 and msg.small_blind == 1 and msg.big_blind == 2  # 改买入,盲注不变随快照带回
    assert not any(isinstance(e, Persist) for e in events)


def test_set_buy_in_by_watcher_allowed():
    world = _add_watcher(_member_world())
    world, events, err = run(world, SetBuyIn(origin="W", amount=400))
    assert err is None and _room(world).buy_in == 400


def test_set_buy_in_by_non_seat0_member_allowed():
    # 与 SetSmallBlind 对称:非 0 号位在座成员(B@座1)也能改买入(直接钉死 SetBuyIn 路径的放开)。
    world = _member_world()
    world, events, err = run(world, SetBuyIn(origin="B", amount=400))
    assert err is None and _room(world).buy_in == 400


def test_set_buy_in_mid_hand_rejected():
    world = hand_world([player("A", 100, seat=0), player("B", 100, seat=1)], status=HandStatus.FLOP)
    before = _room(world).buy_in
    world, events, err = run(world, SetBuyIn(origin="A", amount=250))
    assert err is not None and err.code is ErrorCode.HAND_IN_PROGRESS and events == []
    assert _room(world).buy_in == before


def test_set_buy_in_non_positive_rejected():
    world = _member_world()
    world, events, err = run(world, SetBuyIn(origin="A", amount=0))
    assert err is not None and err.code is ErrorCode.INVALID_BUY_IN and events == []


def test_set_buy_in_non_member_rejected_not_in_room():
    world = _member_world()
    world, events, err = run(world, SetBuyIn(origin="Z", amount=250))
    assert err is not None and err.code is ErrorCode.NOT_IN_ROOM and events == []


def test_set_buy_in_visible_in_subsequent_state_snapshot():
    # 决策 6:StateSnapshot 携 buy_in ⇒ SetBuyIn 改后,(重)连/进房的快照能拿到当前值。
    world = _member_world(buy_in=100)
    world, _, _ = run(world, SetBuyIn(origin="A", amount=250))
    world, events, err = run(world, JoinRoom(origin="C", room="r1", uid=99, loaded=500))
    assert err is None
    snap = next(e.msg for e in events if isinstance(e.msg, StateSnapshot))
    assert snap.buy_in == 250  # 进房快照反映改后的房间默认买入


# ════════ 守恒 / 只读:房配不碰积分、不触手牌、不落库 ════════
def test_room_config_moves_no_points_and_persists_nothing():
    world = _member_world()
    before_users = copy.deepcopy(world.users)
    before_seats = copy.deepcopy(_room(world).seats)
    before_hand = copy.deepcopy(_room(world).hand)  # 两手之间恒 None
    world, ev1, _ = run(world, SetSmallBlind(origin="A", amount=7))
    world, ev2, _ = run(world, SetBuyIn(origin="A", amount=300))
    assert world.users == before_users  # 无全局积分移动
    assert _room(world).seats == before_seats  # 不碰座位筹码 / in_game_points
    assert _room(world).hand == before_hand  # 不触手牌(仍 None)
    assert not any(isinstance(e, Persist) for e in ev1 + ev2)  # 无落库
