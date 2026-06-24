"""P1 余项:等大盲再入局时机 + 躲盲被堵(rules.md ①.7-①.10),经 reduce 集成。

等大盲者(READY + new_here + wait_for_big_blind)当且仅当本手会成为**大盲**(order[1])才免费入局,
posting 结构大盲即入局费(不额外 post)。庄位永在 core_dealt(非等大盲发牌集)上定 → waiter 永不持庄/小盲。
防躲盲:_start_hand 末尾把本手**未被发牌的在座者**置 new_here=True(「上一手没参与即算新人」),
故换座/退房再进/坐出再回/没 ready 干等/断线跨手都要重新付盲或等大盲。设计见 changes/0023。

SB=1、BB=2。make_table 的 button 是**上一手庄**(_start_hand 会 advance)。
"""

from app.core.commands import PlayerAction, SetUserStatus, SitDown, StartHand
from app.core.domain import UserState
from app.core.enums import PlayerActionType, PlayerStatus, UserStatus
from app.core.errors import ErrorCode
from tests.builders import DECK, T0, make_table, run, seat

SB = 1
BB = 2


def _start(world, origin, seat_idx):
    return run(world, StartHand(origin=origin, seat=seat_idx, started_at=T0, deck=DECK))


def _room(world, name="r1"):
    return world.rooms[name]


def _hand(world, name="r1"):
    return world.rooms[name].hand


def _seat_indices_in_hand(hand):
    return {p.seat_position for p in hand.players}


def _by_seat(hand, idx):
    return next(p for p in hand.players if p.seat_position == idx)


def _fold_current(world, name="r1"):
    # 驱动当前行动者 FOLD(heads-up 下小盲弃即可收手)
    h = world.rooms[name].hand
    actor = h.players[h.acting_position]
    return run(world, PlayerAction(origin=actor.nickname, action=PlayerActionType.FOLD))


# ── ①.7 等大盲入局:BB 推进到 waiter 座位那手免费入局,下结构大盲即入局费(不双重 post)──
def test_waiter_enters_as_big_blind_when_swept():
    # core={0,1,2} established;座3 等大盲。button=0 → 推进到 1:order=[2,3,0,1],BB=座3。
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=False),
            3: seat("D", 100, new_here=True, wait_for_big_blind=True),
        },
        button=0,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert _room(world).button_position == 1
    assert [p.seat_position for p in h.players] == [2, 3, 0, 1]  # SB=座2、BB=座3(waiter)、UTG=座0、庄=座1
    d = _by_seat(h, 3)
    assert d.bet_amount == BB and d.points == 98  # 只下一个结构大盲(非 98-2=96:未额外入局 post)
    assert d.status is PlayerStatus.ACTIVE
    assert _room(world).seats[3].new_here is False and _room(world).seats[3].wait_for_big_blind is False  # 已入局
    assert _room(world).users_in_room["D"] is UserStatus.PLAYING
    assert d.points + d.bet_amount == _room(world).seats[3].in_game_points == 100  # 逐玩家守恒


# ── 等大盲未到大盲位 → 本手不发牌(button=1 → 推进到 2:座3 会是小盲位,不入局)──
def test_waiter_not_dealt_when_would_be_small_blind():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=False),
            3: seat("D", 100, new_here=True, wait_for_big_blind=True),
        },
        button=1,  # → 推进到 2:order=seat_order(2,{0,1,2,3})=[3,0,1,2],座3=小盲、非大盲
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert 3 not in _seat_indices_in_hand(h)  # 座3 会是小盲位 → 不免费入局,继续等
    assert _room(world).seats[3].new_here is True and _room(world).seats[3].wait_for_big_blind is True
    assert _room(world).users_in_room["D"] is UserStatus.READY_TO_PLAY


# ── heads-up core 接纳 waiter:core={0,4} 翻成 3 人,waiter 当大盲,庄/小盲仍是 core 座 ──
def test_heads_up_core_admits_waiter_as_big_blind():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            4: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=True, wait_for_big_blind=True),
        },
        button=0,  # core={0,4} → button=advance(0,{0,4})=4;seat_order(4,{0,2,4})=[0,2,4]:SB=0、BB=2、庄=4
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert [p.seat_position for p in h.players] == [0, 2, 4]
    assert _room(world).button_position == 4  # 庄是 core 座,不是 waiter
    assert _by_seat(h, 0).bet_amount == SB  # 小盲是 core 座 0,非 waiter
    c = _by_seat(h, 2)
    assert c.bet_amount == BB and c.points == 98  # waiter 下结构大盲


# ── 单 established + 单 waiter:打 heads-up,waiter 当大盲下结构大盲(结构大盲即入局费,最反直觉但正确)──
def test_lone_established_plus_waiter_heads_up():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            5: seat("E", 100, new_here=True, wait_for_big_blind=True),
        },
        button=0,  # core={0} → button=0;seat_order(0,{0,5})=[0,5]:庄/小盲=0、大盲=座5
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert [p.seat_position for p in h.players] == [0, 5]
    assert _by_seat(h, 0).bet_amount == SB  # established 是庄/小盲
    e = _by_seat(h, 5)
    assert e.bet_amount == BB and e.points == 98  # 只下结构大盲(非 BB+entry)
    assert _room(world).seats[5].new_here is False


# ── 两个相邻 waiter:只有最靠小盲那个本手入局(当大盲),另一个保持等待 ──
def test_two_waiters_only_sb_closest_enters():
    # core={0,1};座2、座3 等大盲。button=1(make_table)→ advance(1,{0,1})=0。
    # seat_order(0,{0,1,2,3})=[1,2,3,0]:大盲=座2 → 座2 入局,座3 仍等。
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=True, wait_for_big_blind=True),
            3: seat("D", 100, new_here=True, wait_for_big_blind=True),
        },
        button=1,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert 2 in _seat_indices_in_hand(h) and 3 not in _seat_indices_in_hand(h)  # 仅座2 入局
    assert h.players[1].seat_position == 2  # 座2 是大盲(order[1])
    assert _by_seat(h, 2).bet_amount == BB  # 下结构大盲
    assert _room(world).seats[3].new_here is True and _room(world).seats[3].wait_for_big_blind is True  # 座3 仍等


# ── 杀「min(qualifiers)」变异:多候选时入局者是「真正大盲(order[1])」,而非最小座号 ──
def test_two_waiters_entrant_is_big_blind_not_smallest_seat():
    # core={0};座1、座2 等大盲。button=0 → seat_order(0,{0,1,2})=[1,2,0]:真正大盲=座2(order[1]),非最小座号座1。
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("C", 100, new_here=True, wait_for_big_blind=True),
            2: seat("D", 100, new_here=True, wait_for_big_blind=True),
        },
        button=0,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert 2 in _seat_indices_in_hand(h) and 1 not in _seat_indices_in_hand(h)  # 座2 入局,非最小座号座1
    assert h.players[1].seat_position == 2 and _by_seat(h, 2).bet_amount == BB  # 座2 是大盲、下结构大盲
    assert _room(world).seats[1].new_here is True  # 座1 仍等下手


# ── 同样阵容、不同庄:庄推进到使靠后的 waiter 成为大盲 → 它入局(不饿死、不瞬移)──
def test_far_waiter_enters_when_bb_reaches_it():
    # core={0,1};仅座3 等大盲。button=0(make_table)→ advance(0,{0,1})=1。
    # seat_order(1,{0,1,3})=[3,0,1]:大盲=座0 → 座3 是小盲、不入局。换 button 验证它能入局:
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            3: seat("D", 100, new_here=True, wait_for_big_blind=True),
        },
        button=1,  # → advance(1,{0,1})=0;seat_order(0,{0,1,3})=[1,3,0]:大盲=座3 → 入局
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert 3 in _seat_indices_in_hand(h)
    assert h.players[1].seat_position == 3  # 座3 是大盲
    assert _by_seat(h, 3).bet_amount == BB


# ── FIX-1:唯一 established 坐出、只剩 READY 的等大盲者 → core 为空 → NOT_ENOUGH_PLAYERS,不崩溃 ──
def test_empty_core_with_ready_waiters_not_enough():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),  # established 但坐出 → 不在 core(挡 bootstrap)
            5: seat("E", 100, new_here=True, wait_for_big_blind=True),  # 唯一 READY,但等大盲
        },
        button=0,
        statuses={"A": UserStatus.SITTING_OUT},
    )
    # 发起人须 READY:只有 E 是 READY;E 在座5
    world, events, err = _start(world, "E", 5)
    assert err is not None and err.code is ErrorCode.NOT_ENOUGH_PLAYERS  # 不抛 IndexError(空集 advance_button)
    assert _room(world).hand is None  # 工作副本丢弃,world 不动


# ── PART B:本手未被发牌的在座者(坐出 / 没 ready 干等)统一被重标 new_here=True(防躲盲)──
def test_remark_not_dealt_seated_become_new_here():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),  # 发牌
            1: seat("B", 100, new_here=False),  # 发牌
            2: seat("C", 100, new_here=False),  # established 坐出 → 未发牌
            3: seat("D", 100, new_here=False),  # established 仅 SITTING_IN(没 ready 干等)→ 未发牌
        },
        button=0,
        statuses={"C": UserStatus.SITTING_OUT, "D": UserStatus.SITTING_IN},
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    assert _seat_indices_in_hand(h) == {0, 1}  # 仅 A、B 发牌
    assert _room(world).seats[0].new_here is False and _room(world).seats[1].new_here is False  # 参与者仍 established
    assert _room(world).seats[2].new_here is True  # 坐出者:上一手没参与 → 重标新人(回来要付盲)
    assert _room(world).seats[3].new_here is True  # 干等者:同样重标(堵「没 ready 躲盲」)


# ── PART B 键于发牌集:本手被发牌、手尾才请求坐出者,本手算参与 → 仍 new_here=False ──
def test_dealt_player_requesting_sitout_stays_established_this_hand():
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)},
        button=0,
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    # 局中请求坐出(延到手尾);本手已被发牌 → 不被 PART B 重标
    world, _, err = run(world, SetUserStatus(origin="A", status=UserStatus.SITTING_OUT))
    assert err is None
    assert _room(world).seats[0].new_here is False  # 本手参与了 → 仍 established(到下手真没发牌才翻 True)


# ── ①.10 端到端:坐出一手再回来 → 算「上一手没参与」→ 回来必须付入局大盲(躲盲被堵)──
def test_sit_out_then_return_must_post_entry():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            2: seat("C", 100, new_here=False),
        },
        button=0,
    )
    # C 先坐出(READY→SITTING_OUT),手1 只发 A、B
    world, _, err = run(world, SetUserStatus(origin="C", status=UserStatus.SITTING_OUT))
    assert err is None
    world, _, err = _start(world, "A", 0)  # core={0,1},button=advance(0)=1 heads-up
    assert err is None
    assert 2 not in _seat_indices_in_hand(_hand(world))
    assert _room(world).seats[2].new_here is True  # 关键:坐出致「没参与上一手」→ 重标新人

    # 手1 收手(heads-up 小盲弃)
    world, _, err = _fold_current(world)
    assert err is None
    assert _room(world).hand is None  # 手已结束

    # C 回来 ready,A、B 重新 ready
    world, _, err = run(world, SetUserStatus(origin="C", status=UserStatus.SITTING_IN))
    assert err is None
    world, _, err = run(world, SetUserStatus(origin="C", status=UserStatus.READY_TO_PLAY))
    assert err is None
    for nick in ("A", "B"):
        world, _, err = run(world, SetUserStatus(origin=nick, status=UserStatus.READY_TO_PLAY))
        assert err is None

    # 手2:C 现在 new_here=True 且非等大盲 → 付盲即玩。button 上一手=1 → advance(1,{0,1,2})=2 → C(座2)=庄(非盲位)
    world, _, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    c = _by_seat(h, 2)
    assert c.bet_amount == BB and c.points == 98  # 回来付了一个入局大盲(躲盲被堵)
    assert _room(world).seats[2].new_here is False


# ── waive 优先于 wait_for_big_blind:免盲快照里的等大盲者直接免费正常入局(走 core,不走 sweep 路径)──
def test_waive_dominates_wait_for_big_blind():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            1: seat("B", 100, new_here=False),
            3: seat("D", 100, new_here=True, wait_for_big_blind=True),  # 既等大盲、又在 waive 快照
        },
        button=1,  # core={0,1,3}(D 因 waive 入 core)→ advance(1,{0,1,3})=3;seat_order(3,{0,1,3})=[0,1,3]:D(座3)=庄
        waive={"D"},
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    d = _by_seat(h, 3)
    assert d.bet_amount == 0 and d.points == 100  # 免费正常入局(非盲位 → 不 post),不被当 waiter 强塞大盲
    assert _room(world).seats[3].new_here is False and _room(world).seats[3].wait_for_big_blind is False
    assert _room(world).waive_entry_for == set()  # 快照已消费


# ── 短码 waiter 当大盲:points<BB → 投不满即 ALLIN,守恒 ──
def test_short_stack_waiter_all_in_as_big_blind():
    world = make_table(
        {
            0: seat("A", 100, new_here=False),
            4: seat("B", 100, new_here=False),
            2: seat("E", 1, new_here=True, wait_for_big_blind=True),  # 只 1 筹码
        },
        button=0,  # core={0,4} → button=4;seat_order(4,{0,2,4})=[0,2,4]:座2=大盲
    )
    world, events, err = _start(world, "A", 0)
    assert err is None
    h = _hand(world)
    e = _by_seat(h, 2)
    assert (e.bet_amount, e.points, e.status) == (1, 0, PlayerStatus.ALLIN)  # 投 1 即 all-in
    assert h.last_bet == BB  # last_bet 仍是完整 BB
    assert e.points + e.bet_amount == _room(world).seats[2].in_game_points == 1  # 守恒


# ── PART C(wire 切片):SitDown 带 wait_for_big_blind=True → Seat 透传该标志 ──
def test_sit_down_with_wait_for_big_blind_flag():
    world = make_table(
        {0: seat("A", 100, new_here=False)},
        button=0,
        max_seats=4,
    )
    # 新观战者进房 → 就座并选「等大盲」
    world.rooms["r1"].users_in_room["W"] = UserStatus.WATCHING
    world.users["W"] = UserState(uid=99, nickname="W", points=0, room="r1")
    world, events, err = run(world, SitDown(origin="W", seat=2, wait_for_big_blind=True))
    assert err is None
    s = world.rooms["r1"].seats[2]
    assert s.new_here is True and s.wait_for_big_blind is True  # 选了等大盲


def test_sit_down_default_pays_blind_to_play():
    world = make_table({0: seat("A", 100, new_here=False)}, button=0, max_seats=4)
    world.rooms["r1"].users_in_room["W"] = UserStatus.WATCHING
    world.users["W"] = UserState(uid=99, nickname="W", points=0, room="r1")
    world, events, err = run(world, SitDown(origin="W", seat=2))  # 不带标志
    assert err is None
    s = world.rooms["r1"].seats[2]
    assert s.new_here is True and s.wait_for_big_blind is False  # 默认付盲即玩
