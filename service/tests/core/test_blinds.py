"""P1:开局定位与下盲(rules.md ① 穷举)—— 定庄/盲位/heads-up/排座 + 下盲短码 all-in。

SB=1、BB=2。advance_button/seat_order 是纯座位下标运算;post_blinds 操作 Hand 工作副本。
首行动位不在 blinds 内:preflop = betting.next_active_position(hand, 1)(大盲下一位),
postflop = next_active_position(hand, 庄在 players 的下标)——这里顺带断言以锁住接缝。
"""

from app.core.enums import PlayerStatus
from app.core.rules import betting, blinds
from tests.builders import hand, player

SB = 1
BB = 2


def _seated(order):
    # 按 seat_order 给出的下标建 ACTIVE 玩家,seat_position 即座位号
    players = [player(str(s), 100, seat=s) for s in order]
    return players, hand(players, last_bet=BB, last_raise_size=BB)


# ── 测试 ①.1 3 人定位 ──
def test_three_handed_positions():
    order = blinds.seat_order(button=0, eligible={0, 1, 2})
    assert order == [1, 2, 0]  # SB=座1、BB=座2、UTG=庄=座0
    players, h = _seated(order)
    assert players[betting.next_active_position(h, 1)].seat_position == 0  # preflop 首行动=UTG=座0


# ── 测试 ①.2 6 人定位 ──
def test_six_handed_positions():
    order = blinds.seat_order(button=2, eligible={0, 1, 2, 3, 4, 5})
    assert order == [3, 4, 5, 0, 1, 2]  # SB=3、BB=4、UTG=5、庄=2 在序尾
    players, h = _seated(order)
    assert players[betting.next_active_position(h, 1)].seat_position == 5  # preflop 首行动=UTG=座5
    btn_idx = order.index(2)
    assert players[betting.next_active_position(h, btn_idx)].seat_position == 3  # postflop 首行动=SB=座3


# ── 测试 ①.3 heads-up 特例(庄=小盲、preflop 先动、postflop 后动)──
def test_heads_up_button_is_small_blind():
    order = blinds.seat_order(button=0, eligible={0, 1})
    assert order == [0, 1]  # 庄=SB=座0、BB=座1
    players, h = _seated(order)
    assert players[betting.next_active_position(h, 1)].seat_position == 0  # preflop 首行动=庄/SB=座0
    assert players[betting.next_active_position(h, 0)].seat_position == 1  # postflop 首行动=BB=座1


# ── 测试 ①.4 庄推进跳过非 ready + 环形回绕 ──
def test_button_advance_skips_non_eligible():
    assert blinds.advance_button(0, {0, 2, 3}) == 2  # 座1 非在局 → 跳到座2
    assert blinds.advance_button(5, {0, 2, 3}) == 0  # 无更大下标 → 环形回到座0
    order = blinds.seat_order(button=0, eligible={0, 2, 3, 4, 5})
    assert order[0] == 2  # 座1 出局 → SB 落到座2


def test_advance_button_basic():
    assert blinds.advance_button(0, {0, 1, 2}) == 1
    assert blinds.advance_button(2, {0, 1, 2}) == 0  # 回绕


# ── 测试 ①.5 短码盲注 all-in ──
def test_short_stack_blind_is_all_in():
    sb = player("SB", 100, seat=0)
    bb = player("BB", 1, seat=1)  # 只剩 1 < BB=2
    h = hand([sb, bb], acting_position=0)
    blinds.post_blinds(h, SB)
    assert (sb.bet_amount, sb.points, sb.status) == (1, 99, PlayerStatus.ACTIVE)
    assert (bb.bet_amount, bb.points) == (1, 0)  # 投 1 即耗尽
    assert bb.status is PlayerStatus.ALLIN
    assert h.last_bet == BB and h.last_raise_size == BB  # last_bet 仍是完整 BB=2


# ── 下盲常规:扣筹码、置 last_bet/last_raise_size、不置 has_acted ──
def test_post_blinds_normal():
    sb = player("SB", 100, seat=0)
    bb = player("BB", 100, seat=1)
    h = hand([sb, bb], acting_position=0)
    blinds.post_blinds(h, SB)
    assert (sb.bet_amount, sb.points) == (1, 99)
    assert (bb.bet_amount, bb.points) == (2, 98)
    assert h.last_bet == BB and h.last_raise_size == BB
    assert sb.has_acted is False and bb.has_acted is False  # 盲注不算自愿行动
    assert sb.status is PlayerStatus.ACTIVE and bb.status is PlayerStatus.ACTIVE
