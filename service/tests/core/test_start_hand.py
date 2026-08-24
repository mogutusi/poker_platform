"""P1:reduce `_start_hand`(开局)—— rules.md ① 开局那半,经 reduce 集成。

定位/下盲的纯函数已在 test_blinds 穷举;这里测 reduce 把它们接起来的整条开局:
入局资格(established / 付盲即玩 / bootstrap / 尊重 waive 快照 / 等大盲不发)+ 锁筹 + 发牌 +
置 PLAYING + 定行动者 + 事件投影 + 守恒/隐私。SB=1、BB=2。

固定牌堆 DECK 注入,玩家 j 得 (DECK[j], DECK[N+j])(轮转、不烧牌)。
等大盲「再入局时机」(①.7-①.10)穷举见 test_wait_for_big_blind(0023);免盲投票(①.12-①.15)见
test_free_entry_vote(0020)。本篇覆盖开局主路径 + 等大盲者「本手非大盲位 → 不发牌」这一侧。
"""

from app.core.enums import HandStatus, PlayerStatus, RoomStatus, UserStatus
from app.core.errors import ErrorCode
from app.core.events import Broadcast, ClearAction, Personal, Persist, TurnChanged
from app.core.commands import StartHand
from app.wire.server import HandStarted, HandStatusChanged, HoleCards
from tests.builders import DECK, T0, make_table, run, seat

SB = 1
BB = 2


def _start(world, origin, seat_idx, *, name="r1"):
    return run(world, StartHand(origin=origin, seat=seat_idx, started_at=T0, deck=DECK))


def _room(world, name="r1"):
    return world.rooms[name]


def _hand(world, name="r1"):
    return world.rooms[name].hand


def _by_seat(hand, idx):
    return next(p for p in hand.players if p.seat_position == idx)


# ── ①.1 3 人定位(经 reduce):初始 button=2 → 推进到 0 → SB=座1、BB=座2、UTG=座0 ──
def test_three_handed_positions():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False), 2: seat("C", 100, new_here=False)},
        button=2,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert _room(world).button_position == 0
    assert [p.seat_position for p in h.players] == [1, 2, 0]  # SB=座1、BB=座2、庄=座0 序尾
    assert _by_seat(h, 1).bet_amount == SB and _by_seat(h, 2).bet_amount == BB
    assert _by_seat(h, 0).bet_amount == 0 and _by_seat(h, 0).points == 100  # 已入局非盲位玩家免付(钉 not new_here 免付臂)
    assert h.players[h.acting_position].seat_position == 0  # preflop 首行动=UTG=庄=座0
    assert h.last_bet == BB and h.last_raise_size == BB


# ── ①.2 6 人定位:button=1 → 推进到 2 → SB=3、BB=4、UTG=5(preflop 首行动);postflop 首行动留 0011 街推进 ──
def test_six_handed_positions():
    world = make_table(
        {i: seat(chr(65 + i), 100, new_here=False) for i in range(6)},
        button=1,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert _room(world).button_position == 2
    assert [p.seat_position for p in h.players] == [3, 4, 5, 0, 1, 2]  # 庄=2 在序尾
    assert h.players[h.acting_position].seat_position == 5  # preflop 首行动=UTG=座5


# ── ①.3 heads-up 特例:button=1 → 推进到 0 → 庄=SB=座0、BB=座1、preflop 首行动=庄/SB ──
def test_heads_up_button_is_small_blind():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=1,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert _room(world).button_position == 0
    assert [p.seat_position for p in h.players] == [0, 1]  # 庄=SB=座0、BB=座1
    assert _by_seat(h, 0).bet_amount == SB and _by_seat(h, 1).bet_amount == BB
    assert h.players[h.acting_position].seat_position == 0  # preflop 庄/SB 先动


# ── ①.4 庄推进跳过非 ready(SITTING_OUT):座1 坐出 → SB 落到座2 ──
def test_button_advance_skips_sitting_out():
    world = make_table(
        {i: seat(chr(65 + i), 100, new_here=False) for i in range(4)},
        button=3,
        statuses={"B": UserStatus.SITTING_OUT},  # 座1 坐出,不在局
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert _room(world).button_position == 0
    assert [p.seat_position for p in h.players] == [2, 3, 0]  # 座1 跳过 → SB=座2
    assert all(p.seat_position != 1 for p in h.players)  # 坐出者不发牌


# ── ①.5 短码盲注 all-in(经 reduce):BB 玩家只剩 1<BB → 投 1 即 ALLIN、last_bet 仍 2 ──
def test_short_stack_blind_all_in():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 1, new_here=False)},
        button=1,  # → 推进到 0:庄/SB=座0、BB=座1
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    bb = _by_seat(h, 1)
    assert (bb.bet_amount, bb.points, bb.status) == (1, 0, PlayerStatus.ALLIN)
    assert h.last_bet == BB and h.last_raise_size == BB


# ── ①.6 付盲即玩(默认):座3 新入座、未设等大盲 → 投一个 BB 入局(live)、new_here 清掉、立刻能玩 ──
def test_new_player_posts_to_play():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=False),
            3: seat("D", 100, new_here=True),  # 新人,付盲即玩
        },
        button=3,  # → 推进到 0:SB=座1、BB=座2、座3=UTG(非盲位)、庄=座0
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    d = _by_seat(h, 3)
    assert d.bet_amount == BB and d.points == 98  # 入局 post 一个 BB(live)
    assert _room(world).seats[3].new_here is False  # 已入局
    assert _room(world).users_in_room["D"] is UserStatus.PLAYING  # 立刻在局
    assert h.last_bet == BB  # 入局 post 不抬注


# ── ①.11 bootstrap:空桌两新人坐下 → 无已入局玩家 → 直接都发牌、只下常规盲注(不收入局 post) ──
def test_bootstrap_no_entry_post():
    world = make_table(
        {0: seat("A", 100, new_here=True), 1: seat("B", 100, new_here=True)},  # 全 new_here
        button=1,  # → 推进到 0:庄/SB=座0、BB=座1
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert _by_seat(h, 0).bet_amount == SB and _by_seat(h, 1).bet_amount == BB  # 仅结构盲注
    assert _by_seat(h, 0).points == 99 and _by_seat(h, 1).points == 98  # 无额外入局 post
    assert all(not _room(world).seats[i].new_here for i in (0, 1))  # 入局后清 new_here


# ── 尊重 waive 快照(①.12 前置):new_here 在 waive_entry_for 里 → 免费正常入局、不 post、清快照 ──
def test_waive_entry_snapshot_honored():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=True),  # 新人,但在 waive 快照里
        },
        button=1,  # → 推进到 2:SB=座0、BB=座1、座2=庄(非盲位)
        waive={"C"},
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    c = _by_seat(h, 2)
    assert c.bet_amount == 0 and c.points == 100  # 免费入局,不 post
    assert _room(world).seats[2].new_here is False
    assert _room(world).waive_entry_for == set()  # 快照已消费


# ── 等大盲:本手非大盲位 → 不发牌(button=0 → 推进到 1,座2 会是小盲位;入局时机穷举见 test_wait_for_big_blind)──
def test_wait_for_big_blind_not_dealt():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=True, wait_for_big_blind=True),  # 选等大盲
        },
        button=0,  # → 推进到 1:seat_order(1,{0,1,2})=[2,0,1],座2=小盲位、非大盲 → 不入局
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert all(p.seat_position != 2 for p in h.players)  # 本手不发牌(未被 BB 扫到)
    assert _room(world).users_in_room["C"] is UserStatus.READY_TO_PLAY  # 仍 ready,等下手
    assert _room(world).seats[2].new_here is True and _room(world).seats[2].points == 100  # 未锁筹


# ── 锁筹:Seat.points→Player.points,存 in_game_points 快照,Seat.points 清零 ──
def test_chips_locked_into_hand():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 80, new_here=False)},
        button=1,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    room = _room(world)
    for idx, orig in ((0, 100), (1, 80)):
        assert room.seats[idx].points == 0  # 锁入后桌上清零
        assert room.seats[idx].in_game_points == orig  # 快照保留


# ── 守恒(testing.md 强制):每个 Player 的 points+bet_amount == 其 in_game_points 快照 ──
def test_chip_conservation_per_player():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=False),
            3: seat("D", 100, new_here=True),  # 入局 post 也出自本人锁入栈
        },
        button=3,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    room, h = _room(world), _hand(world)
    for p in h.players:
        locked = room.seats[p.seat_position].in_game_points
        assert p.points + p.bet_amount == locked  # 投入只来自自己锁入的栈
    assert h.contributed == {}  # 街未结束,未并入 contributed


# ── 发牌:轮转、不烧牌、底牌确定,余牌存 hand.deck ──
def test_hole_cards_dealt_round_robin():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=1,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    n = len(h.players)
    for j, p in enumerate(h.players):
        assert p.hole_cards == (DECK[j], DECK[n + j])  # 玩家 j 得 DECK[j]、DECK[n+j]
    assert h.deck == DECK[2 * n:]  # 余牌留作后续街


# ── HandStarted 带开局底池(0087):盲注已下,底池不是 0;与 StateSnapshot.pot 同一口径 ──
def test_hand_started_carries_pot_of_blinds():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False), 2: seat("C", 100, new_here=False)},
        button=2,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    started = events[0].msg
    # 开局 contributed 还是空的,所以底池就是各家本街投入之和 = SB + BB
    assert started.pot == SB + BB
    assert started.pot == sum(v.bet_amount for v in started.players)


# ── 事件投影 + 隐私:HandStarted → 每人 HoleCards → HandStatusChanged → TurnChanged;广播不含底牌 ──
def test_events_and_privacy():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False), 2: seat("C", 100, new_here=False)},
        button=2,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)

    assert isinstance(events[0], Broadcast) and isinstance(events[0].msg, HandStarted)
    started = events[0].msg
    assert started.hand_seq == h.seq and started.button_position == 0
    assert started.small_blind == SB and started.big_blind == BB
    assert started.acting_position == h.acting_position

    # 每个玩家一条 Personal(HoleCards)
    hole = {e.nick for e in events if isinstance(e, Personal) and isinstance(e.msg, HoleCards)}
    assert hole == {"A", "B", "C"}

    # HandStatusChanged(PRE_FLOP、空 board) + TurnChanged(行动者、epoch=0)
    status_msgs = [e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, HandStatusChanged)]
    assert len(status_msgs) == 1 and status_msgs[0].status is HandStatus.PRE_FLOP and status_msgs[0].board == ()
    # 开局这条**不是**零起点:盲注已经下了(0087——客户端照「换街即清零」推断,preflop 的 Call 因此全废)
    assert status_msgs[0].last_bet == BB
    assert {v.nickname: v.bet_amount for v in status_msgs[0].players} == {"A": 0, "B": SB, "C": BB}
    turn = [e for e in events if isinstance(e, TurnChanged)]
    assert len(turn) == 1 and turn[0].epoch == 0
    assert turn[0].acting_nick == h.players[h.acting_position].nickname

    # 顺序契约(core.md §事件):HandStarted(0) → HoleCards* → HandStatusChanged → TurnChanged(末位)
    hsc_idx = next(i for i, e in enumerate(events) if isinstance(e, Broadcast) and isinstance(e.msg, HandStatusChanged))
    hole_idxs = [i for i, e in enumerate(events) if isinstance(e, Personal) and isinstance(e.msg, HoleCards)]
    assert hole_idxs and all(0 < i < hsc_idx for i in hole_idxs)  # 底牌夹在 HandStarted 与 HandStatusChanged 之间
    assert isinstance(events[-1], TurnChanged)  # 行动倒计时事件殿后

    # 隐私:广播载荷不含底牌字段;无任何载荷泄露 deck
    broadcasts = [e for e in events if isinstance(e, Broadcast)]
    assert all(not isinstance(b.msg, HoleCards) for b in broadcasts)
    assert all(not hasattr(v, "hole_cards") for v in started.players)
    assert all(not hasattr(e.msg, "deck") for e in events if isinstance(e, (Broadcast, Personal)))


# ── 状态机:开局后 RoomStatus=HAND_STARTED、参与者 PLAYING ──
def test_room_and_user_status_after_start():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=1,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    assert _room(world).status is RoomStatus.HAND_STARTED
    assert _room(world).users_in_room == {"A": UserStatus.PLAYING, "B": UserStatus.PLAYING}


# ── 错误臂(失败丢工作副本,world 不动)──
def test_err_hand_in_progress():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=1,
    )
    world, _, err = _start(world, "A", 0)
    assert err is None
    seq_after_first = _hand(world).seq
    world, events, err = _start(world, "A", 0)  # 再开一手
    assert err is not None and err.code is ErrorCode.HAND_IN_PROGRESS
    assert events == [] and _hand(world).seq == seq_after_first  # world 未动


def test_err_not_ready_initiator():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False), 2: seat("C", 100, new_here=False)},
        button=2,
        statuses={"A": UserStatus.SITTING_IN},  # 发起人未 ready
    )
    world, events, err = _start(world, "A", 0)
    assert err is not None and err.code is ErrorCode.NOT_READY
    assert _room(world).hand is None and _room(world).status is RoomStatus.PENDING_START


def test_err_not_enough_players():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=1,
        statuses={"B": UserStatus.SITTING_OUT},  # 仅 A 在局
    )
    world, events, err = _start(world, "A", 0)
    assert err is not None and err.code is ErrorCode.NOT_ENOUGH_PLAYERS
    assert _room(world).hand is None


def test_err_not_your_seat():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=1,
    )
    world, events, err = _start(world, "A", 1)  # A 声称坐在座1(实为 B)
    assert err is not None and err.code is ErrorCode.NOT_YOUR_SEAT
    assert _room(world).hand is None


# ── ①.11 bootstrap(可分辨版,≥3 人):非盲位的 bootstrap 玩家也免付 → 钉死 bootstrap 分支 ──
def test_bootstrap_three_new_no_entry_post():
    world = make_table(
        {i: seat(chr(65 + i), 100, new_here=True) for i in range(3)},  # 3 个新人空桌
        button=2,  # → 推进到 0:SB=座1、BB=座2、UTG=座0(非盲位)
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert _by_seat(h, 0).bet_amount == 0 and _by_seat(h, 0).points == 100  # 非盲位 bootstrap 玩家免付
    assert _by_seat(h, 1).bet_amount == SB and _by_seat(h, 2).bet_amount == BB  # 仅结构盲注


# ── 防躲盲:唯一已入局玩家坐出(仍占座)→ 非 bootstrap → 新人 ready 仍须付入局 BB(rules.md ①「入局与防躲盲」)──
def test_sitting_out_established_blocks_free_entry():
    world = make_table(
        {
            0: seat("E", 100, new_here=False),  # 唯一已入局玩家,但坐出(不在 ready 子集)
            1: seat("A", 100, new_here=True),
            2: seat("B", 100, new_here=True),
            3: seat("C", 100, new_here=True),
        },
        button=2,  # → 推进到 3:SB=座1、BB=座2、座3(C)=庄(非盲位)
        statuses={"E": UserStatus.SITTING_OUT},
    )
    world, events, err = _start(world, "A", 1)
    assert err is None
    h = _hand(world)
    assert all(p.seat_position != 0 for p in h.players)  # 坐出者不发牌
    c = _by_seat(h, 3)
    assert c.bet_amount == BB and c.points == 98  # 整桌有已入局玩家 → C 付入局 BB(否则=躲盲漏洞)


# ── born-all-in:全员投盲即 all-in、无人可行动 → 不等动作,立即跑公共牌摊牌结算(0011 接住 0010 §6)──
def test_born_all_in_runs_out_and_settles():
    world = make_table(
        {0: seat("A", 1, new_here=False), 1: seat("B", 1, new_here=False)},  # 双方 <BB,投盲即 all-in
        button=1,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    room = _room(world)
    # 手牌已跑完公共牌、结算、收尾(不再卡在 HAND_STARTED)
    assert room.hand is None and room.status is RoomStatus.PENDING_START
    assert not any(isinstance(e, TurnChanged) for e in events)  # 无人可行动,不起倒计时
    kinds = [type(e.msg).__name__ for e in events if isinstance(e, Broadcast)]
    assert "HandShowDown" in kinds and "HandEnded" in kinds  # 摊牌 + 结束
    assert any(isinstance(e, Persist) for e in events)  # 手牌记录(事件写)
    assert any(isinstance(e, ClearAction) for e in events)  # 停行动倒计时
    # 守恒:两人各锁入 1,结算后还回座位总额不变(此局 board 成顺/同花,平分各得 1)
    assert room.seats[0].points + room.seats[1].points == 2
    assert room.seats[0].in_game_points == 0 and room.seats[1].in_game_points == 0


# ── 注入牌堆过短 → 返 Err(守 helper「绝不 raise」),工作副本丢弃、world 不动 ──
def test_err_injected_deck_too_short():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=1,
    )
    world, events, err = run(world, StartHand(origin="A", seat=0, started_at=T0, deck=DECK[:3]))
    assert err is not None and err.code is ErrorCode.INTERNAL
    assert events == [] and _room(world).hand is None and _room(world).status is RoomStatus.PENDING_START
