"""P1:reduce `_timeout`(行动超时默认动作)—— rules.md ④「行动超时默认动作」+ timer.md staleness。

超时:仍是该回合该玩家 → 默认动作「能 check 则 check,否则 fold」,复用 betting + 推进;
staleness(无手 / epoch 不符 / 行动者已变)→ 忽略(系统命令 origin=None,过期不报错、world 不动)。SB=1、BB=2。
"""

from app.core.commands import Timeout
from app.core.enums import HandStatus, PlayerActionType, PlayerStatus, RoomStatus
from app.core.events import Broadcast, ClearAction, Persist, TurnChanged
from app.core.messages import HandEnded, PlayerActed
from tests.builders import card, hand_world, make_table, player, run, seat

BOARD = (card("Ah"), card("Kd"), card("Qc"), card("2s"), card("7h"))
FLOP = (BOARD[0], BOARD[1], BOARD[2])


def _room(world, name="r1"):
    return world.rooms[name]


def _acting_seat(world):
    h = _room(world).hand
    return h.players[h.acting_position].seat_position


# ════════ 默认动作 ════════
def test_timeout_checks_when_no_bet_to_call():
    # postflop 无注可过(bet_amount == last_bet == 0)→ 默认 check → 换人 + epoch+1
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.FLOP, last_bet=0, acting_position=0,
        contributed={"A": 2, "B": 2}, flop=FLOP,
    )
    world, events, err = run(world, Timeout(origin=None, nick="A", epoch=0))
    assert err is None
    h = _room(world).hand
    acted = next(e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, PlayerActed))
    assert acted.action is PlayerActionType.CHECK and acted.nickname == "A"
    assert h.players[0].has_acted is True  # check 算自愿行动
    assert _acting_seat(world) == 1 and h.epoch == 1  # 换到 B、epoch 自增
    assert isinstance(events[-1], TurnChanged) and events[-1].epoch == 1


def test_timeout_folds_when_facing_bet():
    # 面对下注(bet_amount < last_bet)→ 默认 fold;3 人 → 弃后仍 2 人,换人继续
    world = hand_world(
        [
            player("A", 100, seat=0, bet_amount=10, has_acted=True),  # 下注者
            player("B", 100, seat=1),  # 轮到他、面对 10
            player("C", 100, seat=2),
        ],
        status=HandStatus.FLOP, last_bet=10, acting_position=1,
        contributed={"A": 2, "B": 2, "C": 2}, flop=FLOP,
    )
    world, events, err = run(world, Timeout(origin=None, nick="B", epoch=0))
    assert err is None
    h = _room(world).hand
    acted = next(e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, PlayerActed))
    assert acted.action is PlayerActionType.FOLD and acted.nickname == "B"
    assert h.players[1].status is PlayerStatus.FOLDED
    assert _acting_seat(world) == 2  # 换到 C 继续


def test_timeout_fold_to_one_ends_hand():
    # heads-up:轮到 B 面对 A 的下注 → 超时 fold → 只剩 A → 无摊牌结束、A 收池
    world = hand_world(
        [
            player("A", 80, seat=0, bet_amount=10, has_acted=True),  # flop 下 10
            player("B", 90, seat=1),  # 面对 10
        ],
        button=0, status=HandStatus.FLOP, last_bet=10, acting_position=1,
        contributed={"A": 10, "B": 10},  # preflop 各投 10
    )
    world, events, err = run(world, Timeout(origin=None, nick="B", epoch=0))
    assert err is None
    room = _room(world)
    assert room.hand is None and room.status is RoomStatus.PENDING_START
    kinds = [type(e.msg).__name__ for e in events if isinstance(e, Broadcast)]
    assert "HandShowDown" not in kinds and "HandEnded" in kinds
    assert any(isinstance(e, Persist) for e in events) and isinstance(events[-1], ClearAction)
    # 守恒:A 投 20(10+10)、B 投 10;未叫注 10 退还、被叫池 20 归 A → A=80+30=110,B=90
    assert room.seats[0].points == 110 and room.seats[1].points == 90
    assert sum(s.points for s in room.seats if s is not None) == 200  # 锁入 100×2


# ════════ staleness(过期忽略,world 不动,不报错)════════
def test_timeout_stale_epoch_ignored():
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.FLOP, last_bet=0, acting_position=0, contributed={"A": 2, "B": 2}, flop=FLOP,
    )
    world, events, err = run(world, Timeout(origin=None, nick="A", epoch=99))  # epoch 不符
    assert err is None and events == []
    h = _room(world).hand
    assert h.acting_position == 0 and h.players[0].status is PlayerStatus.ACTIVE  # world 未动


def test_timeout_wrong_actor_ignored():
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.FLOP, last_bet=0, acting_position=0, contributed={"A": 2, "B": 2}, flop=FLOP,
    )
    world, events, err = run(world, Timeout(origin=None, nick="B", epoch=0))  # 轮到 A,却给 B 超时
    assert err is None and events == []
    assert _room(world).hand.acting_position == 0


def test_timeout_no_hand_ignored():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=1)
    world, events, err = run(world, Timeout(origin=None, nick="A", epoch=0))
    assert err is None and events == []  # 两手之间无 hand → 过期忽略
