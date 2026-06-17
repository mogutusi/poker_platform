"""P1:下注规则(rules.md ② 穷举)—— 三动作、min-raise/重开、街关闭、街结算。

SB=1、BB=2(DEFAULT_SMALL_BLIND=1)。reduce 尚未落地,这里直接对 betting 纯函数
按动作序列驱动:手动选行动者调 apply_action,再断言状态 / street_closed。
"""

from app.core.enums import PlayerActionType, PlayerStatus
from app.core.errors import ErrorCode
from app.core.rules import betting
from tests.builders import hand, player

BB = 2

FOLD = PlayerActionType.FOLD
CHECK = PlayerActionType.CHECK
BET = PlayerActionType.BET


def _preflop_3p():
    # players[0]=SB(投1)、[1]=BB(投2)、[2]=UTG;last_bet=BB,盲注不置 has_acted
    players = [
        player("SB", 99, seat=0, bet_amount=1),
        player("BB", 98, seat=1, bet_amount=2),
        player("UTG", 100, seat=2),
    ]
    return hand(players, last_bet=BB, last_raise_size=BB, acting_position=2)


# ── 测试 ②.1 preflop 全跟 + BB check 收街 ──
def test_preflop_call_around_then_bb_check_closes():
    h = _preflop_3p()
    assert betting.apply_action(h, h.players[2], BET, 2, BB) is None  # UTG 跟
    assert betting.apply_action(h, h.players[0], BET, 2, BB) is None  # SB 补到 2
    assert betting.street_closed(h) is False  # BB 选择权未用,未关
    assert betting.apply_action(h, h.players[1], CHECK, None, BB) is None  # BB check
    assert betting.street_closed(h) is True


# ── 测试 ②.2 preflop 全跟 + BB 加注重开 ──
def test_preflop_bb_raise_reopens():
    h = _preflop_3p()
    betting.apply_action(h, h.players[2], BET, 2, BB)
    betting.apply_action(h, h.players[0], BET, 2, BB)
    assert betting.apply_action(h, h.players[1], BET, 4, BB) is None  # BB 加到 4
    assert h.last_bet == 4
    assert h.players[0].has_acted is False  # SB 被重开
    assert h.players[2].has_acted is False  # UTG 被重开
    assert h.players[1].has_acted is True
    assert betting.street_closed(h) is False


# ── 测试 ②.3 postflop 全 check ──
def test_postflop_all_check_closes():
    players = [player("A", 100, seat=0), player("B", 100, seat=1), player("C", 100, seat=2)]
    h = hand(players, last_bet=0, last_raise_size=BB, acting_position=0)
    betting.apply_action(h, h.players[0], CHECK, None, BB)
    assert betting.street_closed(h) is False
    betting.apply_action(h, h.players[1], CHECK, None, BB)
    assert betting.street_closed(h) is False
    betting.apply_action(h, h.players[2], CHECK, None, BB)
    assert betting.street_closed(h) is True


# ── 测试 ②.4 下注 + 跟注收街 ──
def test_postflop_bet_call_call_closes():
    players = [player("A", 100, seat=0), player("B", 100, seat=1), player("C", 100, seat=2)]
    h = hand(players, last_bet=0, last_raise_size=BB, acting_position=0)
    assert betting.apply_action(h, h.players[0], BET, 4, BB) is None  # A 下 4(重开)
    assert h.last_bet == 4
    assert h.players[1].has_acted is False and h.players[2].has_acted is False
    betting.apply_action(h, h.players[1], BET, 4, BB)  # B 跟
    betting.apply_action(h, h.players[2], BET, 4, BB)  # C 跟
    assert betting.street_closed(h) is True  # 回到 A 时已全 has_acted 且都=4


# ── 测试 ②.5 加注重开 ──
def test_postflop_raise_reopens_then_closes():
    players = [player("A", 100, seat=0), player("B", 100, seat=1), player("C", 100, seat=2)]
    h = hand(players, last_bet=0, last_raise_size=BB, acting_position=0)
    betting.apply_action(h, h.players[0], BET, 4, BB)  # A 下 4
    assert betting.apply_action(h, h.players[1], BET, 10, BB) is None  # B 加到 10(重开)
    assert h.last_bet == 10
    assert h.players[0].has_acted is False and h.players[2].has_acted is False
    betting.apply_action(h, h.players[2], BET, 10, BB)  # C 跟
    betting.apply_action(h, h.players[0], BET, 10, BB)  # A 跟
    assert betting.street_closed(h) is True


# ── 测试 ②.6 min-raise 非法 ──
def test_min_raise_violation_rejected():
    players = [player("A", 100, seat=0, bet_amount=4), player("B", 100, seat=1)]
    h = hand(players, last_bet=4, last_raise_size=2, acting_position=1)
    err = betting.apply_action(h, h.players[1], BET, 5, BB)  # 5 < 4 + max(2,2)=6
    assert err is not None and err.code is ErrorCode.ILLEGAL_ACTION
    assert h.players[1].bet_amount == 0  # 非法不改状态


# ── 测试 ②.7 短 all-in 不重开 ──
def test_short_all_in_does_not_reopen():
    players = [
        player("A", 7, seat=0),  # 只剩 7,跟不满 10
        player("B", 100, seat=1, bet_amount=10, has_acted=True),
        player("C", 100, seat=2, bet_amount=10, has_acted=True),
    ]
    h = hand(players, last_bet=10, last_raise_size=8, acting_position=0)
    assert betting.apply_action(h, h.players[0], BET, 7, BB) is None  # all-in 7 (<10)
    assert h.players[0].status is PlayerStatus.ALLIN
    assert h.players[0].has_acted is True  # rules.md ② 短 all-in 也置 has_acted
    assert h.players[0].bet_amount == 7
    assert h.last_bet == 10  # 不改 last_bet
    assert h.players[1].has_acted is True and h.players[2].has_acted is True  # 不被重开


# ── 测试 ②.8 all-in 超注重开(决策 2)──
def test_over_all_in_reopens_even_if_under_min_raise():
    players = [
        player("A", 14, seat=0),  # all-in 14 > last_bet 10,但 14-10=4 不足完整加注
        player("B", 100, seat=1, bet_amount=10, has_acted=True),
        player("C", 100, seat=2, bet_amount=10, has_acted=True),
    ]
    h = hand(players, last_bet=10, last_raise_size=8, acting_position=0)
    assert betting.apply_action(h, h.players[0], BET, 14, BB) is None
    assert h.players[0].status is PlayerStatus.ALLIN
    assert h.last_bet == 14
    assert h.last_raise_size == 8  # max(8, 14-10=4) 不缩小
    assert h.players[1].has_acted is False and h.players[2].has_acted is False  # 重开


# ── 测试 ②.9 heads-up preflop ──
def test_heads_up_preflop_button_acts_first():
    # players[0]=button=SB(投1)、[1]=BB(投2)
    players = [player("BTN", 99, seat=0, bet_amount=1), player("BB", 98, seat=1, bet_amount=2)]
    h = hand(players, last_bet=BB, last_raise_size=BB, acting_position=0)
    assert betting.apply_action(h, h.players[0], BET, 2, BB) is None  # button/SB 跟到 2
    assert betting.street_closed(h) is False  # BB 选择权
    betting.apply_action(h, h.players[1], CHECK, None, BB)
    assert betting.street_closed(h) is True


def test_heads_up_preflop_bb_raise_continues():
    players = [player("BTN", 99, seat=0, bet_amount=1), player("BB", 98, seat=1, bet_amount=2)]
    h = hand(players, last_bet=BB, last_raise_size=BB, acting_position=0)
    betting.apply_action(h, h.players[0], BET, 2, BB)
    assert betting.apply_action(h, h.players[1], BET, 4, BB) is None  # BB 加注
    assert h.last_bet == 4
    assert h.players[0].has_acted is False
    assert betting.street_closed(h) is False


# ── 动作合法性边界 ──
def test_fold_illegal_when_can_check():
    players = [player("A", 100, seat=0)]
    h = hand(players, last_bet=0, acting_position=0)
    err = betting.apply_action(h, h.players[0], FOLD, None, BB)
    assert err is not None and err.code is ErrorCode.ILLEGAL_ACTION
    assert h.players[0].status is PlayerStatus.ACTIVE


def test_fold_legal_when_facing_bet():
    players = [player("A", 100, seat=0)]
    h = hand(players, last_bet=2, acting_position=0)
    assert betting.apply_action(h, h.players[0], FOLD, None, BB) is None
    assert h.players[0].status is PlayerStatus.FOLDED


def test_check_illegal_when_facing_bet():
    players = [player("A", 100, seat=0)]
    h = hand(players, last_bet=2, acting_position=0)
    err = betting.apply_action(h, h.players[0], CHECK, None, BB)
    assert err is not None and err.code is ErrorCode.ILLEGAL_ACTION


def test_bet_over_stack_rejected():
    players = [player("A", 5, seat=0)]
    h = hand(players, last_bet=0, last_raise_size=BB, acting_position=0)
    err = betting.apply_action(h, h.players[0], BET, 6, BB)
    assert err is not None and err.code is ErrorCode.ILLEGAL_ACTION


def test_bet_without_amount_rejected():
    players = [player("A", 100, seat=0)]
    h = hand(players, last_bet=0, last_raise_size=BB, acting_position=0)
    err = betting.apply_action(h, h.players[0], BET, None, BB)
    assert err is not None and err.code is ErrorCode.ILLEGAL_ACTION


def test_non_active_player_cannot_act():
    players = [player("A", 100, seat=0, status=PlayerStatus.FOLDED)]
    h = hand(players, last_bet=0, acting_position=0)
    err = betting.apply_action(h, h.players[0], CHECK, None, BB)
    assert err is not None and err.code is ErrorCode.ILLEGAL_ACTION


def test_call_exactly_all_in_sets_allin():
    # 跟注恰好用尽栈 → ALLIN(amount == stack == last_bet)
    players = [player("A", 2, seat=0)]
    h = hand(players, last_bet=2, last_raise_size=BB, acting_position=0)
    assert betting.apply_action(h, h.players[0], BET, 2, BB) is None
    assert h.players[0].status is PlayerStatus.ALLIN
    assert h.players[0].bet_amount == 2 and h.players[0].points == 0


# ── 街结算 settle_street ──
def test_settle_street_folds_bets_and_resets():
    players = [
        player("A", 96, seat=0, bet_amount=4, has_acted=True),
        player("B", 96, seat=1, bet_amount=4, has_acted=True),
        player("C", 100, seat=2, status=PlayerStatus.FOLDED),
    ]
    h = hand(players, last_bet=4, last_raise_size=4, contributed={"A": 0, "B": 0})
    betting.settle_street(h, BB)
    assert h.contributed == {"A": 4, "B": 4}  # bet_amount 并入,C 无投入
    assert all(p.bet_amount == 0 for p in h.players)
    assert all(p.has_acted is False for p in h.players)
    assert h.last_bet == 0
    assert h.last_raise_size == BB


def test_settle_street_merges_folded_players_mid_street_bet():
    # 跨街守恒关键路径(rules.md ③.4):弃牌者本街已投入的 bet_amount 也要并入 contributed
    players = [
        player("A", 90, seat=0, bet_amount=10, has_acted=True),
        player("B", 96, seat=1, bet_amount=4, status=PlayerStatus.FOLDED),  # 跟 4 后被加注弃牌
    ]
    h = hand(players, last_bet=10, last_raise_size=10)
    betting.settle_street(h, BB)
    assert h.contributed == {"A": 10, "B": 4}  # 弃牌者的 4 不能丢


def test_settle_street_accumulates_existing_contributed():
    players = [player("A", 90, seat=0, bet_amount=10, has_acted=True)]
    h = hand(players, last_bet=10, contributed={"A": 5})
    betting.settle_street(h, BB)
    assert h.contributed["A"] == 15  # 5(往街)+ 10(本街)


# ── next_active_position ──
def test_next_active_position_skips_folded_and_allin():
    players = [
        player("A", 100, seat=0),
        player("B", 100, seat=1, status=PlayerStatus.FOLDED),
        player("C", 100, seat=2, status=PlayerStatus.ALLIN),
        player("D", 100, seat=3),
    ]
    h = hand(players, acting_position=0)
    assert betting.next_active_position(h, 0) == 3  # 跳过 B(fold)、C(allin)
    assert betting.next_active_position(h, 3) == 0  # 环形回到 A


def test_next_active_position_none_when_no_other_active():
    players = [player("A", 100, seat=0), player("B", 100, seat=1, status=PlayerStatus.FOLDED)]
    h = hand(players, acting_position=0)
    assert betting.next_active_position(h, 0) is None
