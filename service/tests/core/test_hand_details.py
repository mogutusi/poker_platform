"""手牌明细进记录(0110):公共牌、每人底牌、VPIP、谁摊牌亮了牌、庄位/大盲。"""

from app.core.commands import PlayerAction
from app.core.enums import HandStatus, PlayerActionType
from app.core.events import Persist
from app.core.records import HandRecordWrite
from tests.builders import card, hand_world, player, run

BOARD = (card("Ah"), card("Kd"), card("Qc"), card("2s"), card("7h"))
KK = (card("Kc"), card("Ks"))
AA = (card("Ac"), card("Ad"))
LOW = (card("3c"), card("4d"))


def _record(events) -> HandRecordWrite:
    return next(e.payload for e in events if isinstance(e, Persist) and isinstance(e.payload, HandRecordWrite))


def test_showdown_record_has_board_cards_and_showed():
    world = hand_world(
        [
            player("A", 50, seat=0, has_acted=True, hole=KK),
            player("B", 50, seat=1, has_acted=True, hole=AA),
            player("C", 50, seat=2, has_acted=False, hole=LOW),
        ],
        button=0, status=HandStatus.RIVER, acting_position=2,
        contributed={"A": 100, "B": 100, "C": 100},
        flop=BOARD[:3], turn=BOARD[3], river=BOARD[4],
    )
    world, events, err = run(world, PlayerAction(origin="C", action=PlayerActionType.CHECK))
    assert err is None
    rec = _record(events)
    assert rec.board == "AhKdQc2s7h"
    assert rec.button_position == 0 and rec.big_blind == 2
    by_seat = {p.seat_position: p for p in rec.participants}
    assert by_seat[0].hole_cards == "KcKs" and by_seat[1].hole_cards == "AcAd" and by_seat[2].hole_cards == "3c4d"
    assert all(p.showed for p in rec.participants)  # 三人都摊了牌


def test_fold_win_nobody_showed_and_vpip_only_for_voluntary_preflop_money():
    # 3 人 preflop:A(庄,先行动)加注到 6 → VPIP;B(小盲)弃牌;C(大盲)只投了盲注、面对加注弃牌 → 都不算 VPIP。
    world = hand_world(
        [
            player("A", 100, seat=0, hole=KK),
            player("B", 99, seat=1, bet_amount=1, hole=AA),
            player("C", 98, seat=2, bet_amount=2, hole=LOW),
        ],
        button=0, status=HandStatus.PRE_FLOP, last_bet=2, last_raise_size=2, acting_position=0,
    )
    world, _, err = run(world, PlayerAction(origin="A", action=PlayerActionType.BET, bet_amount=6))
    assert err is None
    world, _, err = run(world, PlayerAction(origin="B", action=PlayerActionType.FOLD))
    assert err is None
    world, events, err = run(world, PlayerAction(origin="C", action=PlayerActionType.FOLD))
    assert err is None
    rec = _record(events)
    vpip = {p.seat_position: p.vpip for p in rec.participants}
    assert vpip == {0: True, 1: False, 2: False}
    assert not any(p.showed for p in rec.participants)  # 没摊牌,谁的牌都不公开
    assert rec.board == ""  # 翻牌前就结束,没有公共牌


def test_action_log_classifies_raise_call_fold():
    # 同上一局:A 开池加注到 6(翻牌前大盲已是第一注 → raise),B 弃,C 弃。记录里按 uid 存。
    world = hand_world(
        [
            player("A", 100, seat=0, hole=KK),
            player("B", 99, seat=1, bet_amount=1, hole=AA),
            player("C", 98, seat=2, bet_amount=2, hole=LOW),
        ],
        button=0, status=HandStatus.PRE_FLOP, last_bet=2, last_raise_size=2, acting_position=0,
    )
    uid = {n: world.users[n].uid for n in "ABC"}
    world, _, _ = run(world, PlayerAction(origin="A", action=PlayerActionType.BET, bet_amount=6))
    world, _, _ = run(world, PlayerAction(origin="B", action=PlayerActionType.FOLD))
    world, events, _ = run(world, PlayerAction(origin="C", action=PlayerActionType.FOLD))
    rec = _record(events)
    assert rec.actions == (
        ("pre_flop", uid["A"], "raise", 6),
        ("pre_flop", uid["B"], "fold", 1),
        ("pre_flop", uid["C"], "fold", 2),
    )
