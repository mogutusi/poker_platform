"""P1:边池分配(rules.md ③ 穷举)—— 退还未叫注、分层削池、判池 + 奇数零头、守恒。

牌力以合成名次传入(score 越小越强,score = 名次:1 最强)。座位 seat_of 仅奇数零头用。
"""

from app.core.rules import sidepot

SEAT_SIZE = 6


def _settle(contributed, live, strength, *, seat_of=None, button=0):
    seat_of = seat_of or {n: i for i, n in enumerate(contributed)}
    return sidepot.settle(contributed, set(live), strength, seat_of, button, SEAT_SIZE)


def _assert_conserved(payout, contributed):
    assert sum(payout.total.values()) == sum(contributed.values())


# ── 测试 ③.1 单池 ──
def test_single_pot_winner_takes_all():
    contributed = {"A": 100, "B": 100, "C": 100}
    payout = _settle(contributed, {"A", "B", "C"}, {"A": 2, "B": 1, "C": 3})
    assert payout.total == {"B": 300}
    _assert_conserved(payout, contributed)


# ── 测试 ③.2 基本边池 ──
def test_basic_side_pot():
    contributed = {"A": 50, "B": 100, "C": 100}
    payout = _settle(contributed, {"A", "B", "C"}, {"A": 1, "B": 2, "C": 3})
    # 主池 150{A,B,C} → A;边池 100{B,C} → B
    assert payout.total["A"] == 150
    assert payout.total["B"] == 100
    assert "C" not in payout.total
    assert len(payout.pots) == 2
    assert payout.pots[0].amount == 150 and set(payout.pots[0].eligible) == {"A", "B", "C"}
    assert payout.pots[1].amount == 100 and set(payout.pots[1].eligible) == {"B", "C"}
    _assert_conserved(payout, contributed)


# ── 测试 ③.3 未叫注退还 ──
def test_uncalled_bet_refunded():
    contributed = {"A": 100, "B": 60, "C": 0}
    payout = _settle(contributed, {"A", "B"}, {"A": 1, "B": 2})
    # h1=100(A 唯一)> h2=60 → 退 40 给 A、A 降到 60;单层 60{A,B} pot=120 → A
    assert payout.refunds == {"A": 40}
    assert payout.total["A"] == 160  # 退 40 + 赢 120
    assert "B" not in payout.total
    _assert_conserved(payout, contributed)


# ── 测试 ③.4 弃牌者投入计入低池 ──
def test_folded_contribution_counts_in_low_pot():
    contributed = {"A": 100, "B": 20, "C": 100}  # B 投 20 后弃
    payout = _settle(contributed, {"A", "C"}, {"A": 1, "C": 2})
    # levels [20,100]:L20 池=60{A,C}(含 B 的 20)、L100 池=160{A,C};A 全胜
    assert payout.pots[0].amount == 60 and set(payout.pots[0].eligible) == {"A", "C"}
    assert payout.pots[1].amount == 160 and set(payout.pots[1].eligible) == {"A", "C"}
    assert payout.total["A"] == 220
    _assert_conserved(payout, contributed)


# ── 测试 ③.5 奇数零头给最接近庄家左手者 ──
def test_odd_chip_goes_to_closest_left_of_button():
    # A=座1、B=座3、C=座5(弃,投1);button=0。A 比 B 更接近庄左
    contributed = {"A": 2, "B": 2, "C": 1}
    payout = _settle(
        contributed,
        {"A", "B"},
        {"A": 1, "B": 1},  # A、B 并列最强
        seat_of={"A": 1, "B": 3, "C": 5},
        button=0,
    )
    # L1 池=3{A,B} → 各 1,零头 1 给 A;L2 池=2{A,B} → 各 1。A=3、B=2
    assert payout.total["A"] == 3
    assert payout.total["B"] == 2
    _assert_conserved(payout, contributed)


# ── 测试 ③.5b 奇数零头 wrap-around(庄非 0,赢家座位号在庄之下)──
def test_odd_chip_wraps_around_nonzero_button():
    # button=座4;并列赢家 A=座1、B=座5。庄左最近 (seat-button)%seat_size:B=(5-4)%6=1 < A=(1-4)%6=3 → B 拿零头。
    # 若漏了 % seat_size,plain(seat-button)会误判 A(-3<1)→ 本例钉死环绕取模。
    contributed = {"A": 3, "B": 3, "C": 1}  # C 投 1 后弃,使低池金额为奇数
    payout = _settle(
        contributed,
        {"A", "B"},
        {"A": 1, "B": 1},  # A、B 并列最强
        seat_of={"A": 1, "B": 5, "C": 3},
        button=4,
    )
    assert payout.total["A"] == 3
    assert payout.total["B"] == 4  # 零头 1 归更接近庄左的 B(座5),非座号更小的 A
    _assert_conserved(payout, contributed)


# ── 测试 ③.6 全 all-in 三档分池 ──
def test_three_way_all_in_layers():
    contributed = {"A": 30, "B": 60, "C": 100}
    payout = _settle(contributed, {"A", "B", "C"}, {"A": 3, "B": 2, "C": 1})
    # h1=100(C 唯一)> h2=60 → 退 40 给 C、C 降到 60
    assert payout.refunds == {"C": 40}
    # levels [30,60]:L30 池=90{A,B,C} → C;L60 池=60{B,C} → C
    assert payout.pots[0].amount == 90
    assert payout.pots[1].amount == 60
    assert payout.total["C"] == 40 + 90 + 60
    _assert_conserved(payout, contributed)


# ── 测试 ③.7 无摊牌(一人未弃)走同一算法 ──
def test_no_showdown_single_live_collects_all():
    contributed = {"A": 50, "B": 2, "C": 1}  # A 加到 50,B/C 弃(留下盲注)
    payout = _settle(contributed, {"A"}, {"A": 1})
    # h1=50(A 唯一)> h2=2 → 退 48 给 A、A 降到 2;低池全归唯一 eligible A
    assert payout.refunds == {"A": 48}
    assert payout.total["A"] == 53  # 等价收走全部 contributed
    _assert_conserved(payout, contributed)


# ── 测试 ③.8 守恒(综合)──
def test_conservation_across_shapes():
    cases = [
        ({"A": 100, "B": 100, "C": 100}, {"A", "B", "C"}, {"A": 1, "B": 2, "C": 3}),
        ({"A": 50, "B": 100, "C": 100}, {"A", "B"}, {"A": 1, "B": 2}),
        ({"A": 33, "B": 33, "C": 34}, {"A", "B", "C"}, {"A": 1, "B": 1, "C": 1}),
    ]
    for contributed, live, strength in cases:
        payout = _settle(contributed, live, strength)
        _assert_conserved(payout, contributed)


# ── 退化:某子池无 eligible 时按本档退回 contributor,守住守恒 ──
def test_degenerate_empty_eligible_refunds_contributors():
    # A(live)投 50;B、C 都投 100 后弃 → 顶层 100 无未弃牌者(并列最高、非未叫注)
    contributed = {"A": 50, "B": 100, "C": 100}
    payout = _settle(contributed, {"A"}, {"A": 1})
    # B、C 并列最高 → 无第 1 步未叫注;L50 池=150 → A;L100 池本应 100 无 eligible 且非未叫注 → 退 B、C 各 50
    assert payout.total["A"] == 150
    assert payout.refunds.get("B") == 50 and payout.refunds.get("C") == 50
    _assert_conserved(payout, contributed)


# ── 测试 ③.9 弃牌的唯一最高投入者(0014 离桌/清理 auto-fold)→ 未叫注 forfeit 归 live,不退本人 ──
def test_folded_lone_high_bettor_forfeits_uncalled_to_live():
    # A 投 40(本手唯一最高,但 LeaveRoom auto-fold 折掉)、B 投 10 在局、C 投 10 弃
    contributed = {"A": 40, "B": 10, "C": 10}
    payout = _settle(contributed, {"B"}, {"B": 1})
    # 第 1 步:A 唯一最高(40>10)但 A 不在 live → 未叫注 30 forfeit(不退 A)、A 降到 10;
    # L10 池=30{B};死钱 30 归最高 live 子池 → B 独得 60。A/C 一无所得。
    assert payout.total == {"B": 60}
    assert "A" not in payout.refunds and "A" not in payout.winnings  # 离桌者未叫注未退回(forfeit)
    _assert_conserved(payout, contributed)


def test_folded_lone_high_bettor_mixed_with_unreachable_side_pot():
    # A 投 40(弃,唯一最高)、B 投 30(弃)、C 投 20 在局:未叫注归 C;C 够不着的 20-30 档退回 A/B
    contributed = {"A": 40, "B": 30, "C": 20}
    payout = _settle(contributed, {"C"}, {"C": 1})
    # 第 1 步:A 未叫注 10(40→30)forfeit;分层 {A:30,B:30,C:20}:
    #   L20 池=60{C};L30 档(A,B,均弃)无 eligible 且非未叫注 → 退 A、B 各 10;死钱 10 归 L20 池 → C 得 70
    assert payout.total["C"] == 70
    assert payout.refunds.get("A") == 10 and payout.refunds.get("B") == 10
    assert "A" not in payout.winnings  # A 的未叫注 forfeit、被匹配档退回(非赢取)
    _assert_conserved(payout, contributed)
