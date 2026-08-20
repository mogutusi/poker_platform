"""P1:reduce `_player_action` + 街推进 + 摊牌 + 结算(0011)—— 经 reduce 的**编排**测试。

下注规则(rules.md ②)与边池(rules.md ③)的内部已在 test_betting / test_sidepot 穷举;
这里只测 reduce 把它们接起来的整条流:动作校验臂、街内换人、preflop 大盲选择权、街关闭→进街
(发公共牌/postflop 首行动)、摊牌(HandShowDown+HandEnded+Persist+ClearAction、还座、守恒、隐私)、
无摊牌结束、all-in 跑公共牌、边池分配经 reduce 正确还座。SB=1、BB=2。
"""

from app.core.commands import PlayerAction, StartHand
from app.core.enums import HandStatus, PlayerActionType, PlayerStatus, RoomStatus, UserStatus
from app.core.errors import ErrorCode
from app.core.events import Broadcast, ClearAction, Persist, TurnChanged
from app.wire.server import (
    HandEnded,
    HandShowDown,
    HandStarted,
    HandStatusChanged,
    PlayerActed,
    UserStatusChanged,
)
from app.core.records import HandRecordWrite
from tests.builders import DECK, T0, card, hand_world, make_table, player, run, seat

FOLD = PlayerActionType.FOLD
CHECK = PlayerActionType.CHECK
BET = PlayerActionType.BET

# 一副不会人手一同花/顺子的固定牌面:board = A♥ K♦ Q♣ 2♠ 7♥(无三同花、无顺)
BOARD = (card("Ah"), card("Kd"), card("Qc"), card("2s"), card("7h"))
FLOP, TURN, RIVER = (BOARD[0], BOARD[1], BOARD[2]), BOARD[3], BOARD[4]
TRIP_ACES = (card("Ac"), card("Ad"))  # + A♥ board → 三条 A(最强)
TRIP_KINGS = (card("Kc"), card("Ks"))  # + K♦ board → 三条 K(次强)
ACE_HIGH = (card("3c"), card("4d"))  # 不成对 → A 高(最弱)


def _room(world, name="r1"):
    return world.rooms[name]


def _acting_seat(world):
    h = _room(world).hand
    return h.players[h.acting_position].seat_position


def _acting_nick(world):
    h = _room(world).hand
    return h.players[h.acting_position].nickname


# ════════ 校验臂(失败丢工作副本、world 不动)════════
def test_no_hand_when_pending():
    world = make_table({0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=1)
    world, events, err = run(world, PlayerAction(origin="A", action=CHECK))
    assert err is not None and err.code is ErrorCode.NO_HAND and events == []


def test_not_your_turn():
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.PRE_FLOP, last_bet=2, acting_position=0,
    )
    world, events, err = run(world, PlayerAction(origin="B", action=FOLD))  # 轮到 A,B 抢动作
    assert err is not None and err.code is ErrorCode.NOT_YOUR_TURN and events == []
    assert _room(world).hand.acting_position == 0  # world 未动


def test_illegal_action_passthrough():
    # 面对下注却 CHECK → betting 返 ILLEGAL_ACTION,reduce 透传、丢工作副本
    world = hand_world(
        [player("A", 100, seat=0), player("B", 100, seat=1)],
        status=HandStatus.PRE_FLOP, last_bet=2, acting_position=0,
    )
    world, events, err = run(world, PlayerAction(origin="A", action=CHECK))
    assert err is not None and err.code is ErrorCode.ILLEGAL_ACTION and events == []
    assert _room(world).hand is not None and _room(world).hand.acting_position == 0


# ════════ 街内推进 / 进街(从 StartHand 驱动,全牌堆)════════
def _start_three(button=2):
    # 3 人桌经 StartHand;button=2 → 推进到 0:players 序 [SB=座1, BB=座2, 座0=庄/UTG]
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False), 2: seat("C", 100, new_here=False)},
        button=button,
    )
    world, _, err = run(world, StartHand(origin="A", seat=0, started_at=T0, deck=DECK))
    assert err is None
    return world


def test_call_advances_to_next_actor():
    world = _start_three()
    assert _acting_seat(world) == 0  # preflop 首行动 = UTG = 庄 = 座0(A)
    epoch0 = _room(world).hand.epoch
    world, events, err = run(world, PlayerAction(origin="A", action=BET, bet_amount=2))  # UTG 跟 2
    assert err is None
    assert _acting_seat(world) == 1  # 轮到 SB(座1)
    assert _room(world).hand.epoch == epoch0 + 1
    assert isinstance(events[0], Broadcast) and isinstance(events[0].msg, PlayerActed)
    assert events[0].msg.action is BET and events[0].msg.acting_position == _room(world).hand.acting_position
    assert isinstance(events[-1], TurnChanged) and events[-1].epoch == _room(world).hand.epoch


def test_preflop_bb_option_then_flop():
    world = _start_three()
    world, _, err = run(world, PlayerAction(origin="A", action=BET, bet_amount=2))  # UTG 跟
    world, _, err = run(world, PlayerAction(origin="B", action=BET, bet_amount=2))  # SB 补到 2
    assert _acting_seat(world) == 2  # 轮到 BB(座2):大盲选择权,街未关
    assert _room(world).hand.status is HandStatus.PRE_FLOP
    world, events, err = run(world, PlayerAction(origin="C", action=CHECK))  # BB check → 街关闭
    assert err is None
    h = _room(world).hand
    assert h.status is HandStatus.FLOP and len(h.flop) == 3  # 发 flop
    assert h.contributed == {"A": 2, "B": 2, "C": 2}  # 本街投入已并入
    assert _acting_seat(world) == 1  # postflop 首行动 = SB(座1)
    status_msgs = [e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, HandStatusChanged)]
    assert len(status_msgs) == 1 and status_msgs[0].status is HandStatus.FLOP and len(status_msgs[0].board) == 3
    assert isinstance(events[-1], TurnChanged)


def test_fold_to_one_ends_without_showdown():
    # UTG 跟、SB 跟、BB 加注、UTG 弃、SB 弃 → 只剩 BB 未弃 → 无摊牌结束
    world = _start_three()
    run(world, PlayerAction(origin="A", action=BET, bet_amount=2))  # UTG 跟
    run(world, PlayerAction(origin="B", action=BET, bet_amount=2))  # SB 补
    run(world, PlayerAction(origin="C", action=BET, bet_amount=6))  # BB 加注到 6(重开)
    run(world, PlayerAction(origin="A", action=FOLD))  # UTG 弃
    world, events, err = run(world, PlayerAction(origin="B", action=FOLD))  # SB 弃 → 只剩 C
    assert err is None
    room = _room(world)
    assert room.hand is None and room.status is RoomStatus.PENDING_START
    kinds = [type(e.msg).__name__ for e in events if isinstance(e, Broadcast)]
    assert "HandShowDown" not in kinds and "HandEnded" in kinds  # 无摊牌
    assert any(isinstance(e, Persist) for e in events) and any(isinstance(e, ClearAction) for e in events)
    # 守恒:三人各锁入 100,BB(C)收下底池,总额不变
    assert sum(s.points for s in room.seats if s is not None) == 300
    assert room.seats[2].points == 300 - room.seats[0].points - room.seats[1].points
    assert room.seats[2].points > 100  # C 赢了别人投入的注


def test_checks_advance_through_all_streets_to_showdown():
    # 全程 check 把一手从 preflop 推到 river 摊牌:验证多街自然推进(board 3→4→5、街切换)
    world = _start_three()
    run(world, PlayerAction(origin="A", action=BET, bet_amount=2))  # UTG 跟
    run(world, PlayerAction(origin="B", action=BET, bet_amount=2))  # SB 补
    run(world, PlayerAction(origin="C", action=CHECK))  # BB check → FLOP
    for status, board_len in ((HandStatus.FLOP, 3), (HandStatus.TURN, 4), (HandStatus.RIVER, 5)):
        assert _room(world).hand.status is status and len(_room(world).hand.flop) == 3
        board = _room(world).hand
        assert len([c for c in (*(board.flop or ()), board.turn, board.river) if c is not None]) == board_len
        for _ in range(3):  # 三人依次 check
            world, events, err = run(world, PlayerAction(origin=_acting_nick(world), action=CHECK))
            assert err is None
    room = _room(world)
    assert room.hand is None and room.status is RoomStatus.PENDING_START  # river 关闭 → 摊牌结束
    assert any(isinstance(e, Broadcast) and isinstance(e.msg, HandShowDown) for e in events)
    assert sum(s.points for s in room.seats if s is not None) == 300  # 守恒(无人下注,平/分后总额不变)


def test_headsup_sb_open_fold_ends_hand():
    # heads-up 经 StartHand:SB(先行动)直接弃 → 只剩 BB。回归:BB has_acted=False 使 street_closed 为假,
    # 全靠 _advance 的 len(live)==1 短路结束本手(否则旧逻辑会卡着叫唯一存活者行动)。
    world = make_table(
        {0: seat("A", 100, new_here=False), 1: seat("B", 100, new_here=False)}, button=0
    )
    world, _, err = run(world, StartHand(origin="A", seat=0, started_at=T0, deck=DECK))
    assert err is None
    sb = _acting_nick(world)  # heads-up:button=SB 先行动
    world, events, err = run(world, PlayerAction(origin=sb, action=FOLD))  # SB 自愿开弃
    assert err is None
    room = _room(world)
    assert room.hand is None and room.status is RoomStatus.PENDING_START  # 本手结束,不卡
    assert all(not isinstance(e.msg, HandShowDown) for e in events if isinstance(e, Broadcast))
    assert sum(s.points for s in room.seats if s is not None) == 200  # 守恒:BB 收下 SB 的盲注
    bb_seat = 1 if sb == "A" else 0
    assert room.seats[bb_seat].points == 101  # BB 净赢 1(SB 盲注),SB 余 99


# ════════ 摊牌 + 边池结算(hand_world 直接驱动收尾动作,可控底牌)════════
def test_showdown_single_pot_high_hand_wins():
    # 3 人各投 100 到 RIVER,最后一人 check → 摊牌;B 三条 K 独得 300
    world = hand_world(
        [
            player("A", 50, seat=0, has_acted=True, hole=TRIP_KINGS),  # A 三条 K
            player("B", 50, seat=1, has_acted=True, hole=TRIP_ACES),  # B 三条 A(最强)
            player("C", 50, seat=2, has_acted=False, hole=ACE_HIGH),  # C A 高(最弱)
        ],
        button=0, status=HandStatus.RIVER, last_bet=0, acting_position=2,
        contributed={"A": 100, "B": 100, "C": 100}, flop=FLOP, turn=TURN, river=RIVER,
    )
    world, events, err = run(world, PlayerAction(origin="C", action=CHECK))  # 收尾 check → 摊牌
    assert err is None
    room = _room(world)
    assert room.hand is None and room.status is RoomStatus.PENDING_START
    # B(seat1)拿三条 A 最强 → 独得 300;A、C 各剩 50
    assert room.seats[1].points == 350 and room.seats[0].points == 50 and room.seats[2].points == 50
    assert sum(s.points for s in room.seats if s is not None) == 450  # 守恒(锁入 150×3)
    assert all(s.in_game_points == 0 for s in room.seats if s is not None)
    assert room.users_in_room == {"A": UserStatus.SITTING_IN, "B": UserStatus.SITTING_IN, "C": UserStatus.SITTING_IN}

    # 事件:PlayerActed → HandShowDown(揭示 3 人底牌、5 张 board)→ HandEnded → Persist → ClearAction
    showdown = next(e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, HandShowDown))
    assert len(showdown.board) == 5 and {r.nickname for r in showdown.reveals} == {"A", "B", "C"}
    ended = next(e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, HandEnded))
    assert {w.nickname: w.amount for w in ended.winnings} == {"B": 300}
    assert isinstance(events[-1], ClearAction)
    # 隐私:仅 HandShowDown 带底牌;PlayerActed / HandEnded 不含
    assert not hasattr(next(e.msg for e in events if isinstance(e.msg, PlayerActed)), "hole_cards")
    assert not hasattr(ended, "hole_cards")


def test_hand_record_persisted():
    world = hand_world(
        [
            player("A", 50, seat=0, has_acted=True, hole=TRIP_KINGS),
            player("B", 50, seat=1, has_acted=True, hole=TRIP_ACES),
            player("C", 50, seat=2, has_acted=False, hole=ACE_HIGH),
        ],
        button=0, status=HandStatus.RIVER, last_bet=0, acting_position=2,
        contributed={"A": 100, "B": 100, "C": 100}, flop=FLOP, turn=TURN, river=RIVER,
    )
    world, events, err = run(world, PlayerAction(origin="C", action=CHECK))
    rec = next(e.payload for e in events if isinstance(e, Persist))
    assert isinstance(rec, HandRecordWrite)
    assert rec.dedupe_key == "r1:1" and rec.final_pot == 300  # 单池 300
    assert rec.start_time == T0  # 开局墙钟(core 携带,不读时钟)
    finals = {p.uid: (p.initial_points, p.final_points) for p in rec.participants}
    assert len(finals) == 3
    assert all(init == 150 for init, _ in finals.values())  # 各锁入 150
    assert sum(fin for _, fin in finals.values()) == 450  # 还回总额守恒


def test_side_pot_all_in_split():
    # A all-in 50(主池),B/C 投 100;A 三条 A 得主池 150,B 三条 K 得边池 100,C 0
    world = hand_world(
        [
            player("A", 0, seat=0, status=PlayerStatus.ALLIN, has_acted=True, hole=TRIP_ACES),
            player("B", 50, seat=1, has_acted=True, hole=TRIP_KINGS),
            player("C", 50, seat=2, has_acted=False, hole=ACE_HIGH),
        ],
        button=0, status=HandStatus.RIVER, last_bet=0, acting_position=2,
        contributed={"A": 50, "B": 100, "C": 100}, flop=FLOP, turn=TURN, river=RIVER,
    )
    world, events, err = run(world, PlayerAction(origin="C", action=CHECK))
    assert err is None
    room = _room(world)
    # 主池 150 → A;边池 100 → B;C 0
    assert room.seats[0].points == 150 and room.seats[1].points == 150 and room.seats[2].points == 50
    assert sum(s.points for s in room.seats if s is not None) == 350  # 守恒(50+150+150)
    ended = next(e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, HandEnded))
    assert {w.nickname: w.amount for w in ended.winnings} == {"A": 150, "B": 100}


def test_all_in_call_runs_out_board():
    # flop 上 A 已 all-in 50,B 跟成 all-in → 无人可行动 → 跑完 turn/river 摊牌
    world = hand_world(
        [
            player("A", 0, seat=0, status=PlayerStatus.ALLIN, bet_amount=50, has_acted=True, hole=TRIP_ACES),
            player("B", 50, seat=1, bet_amount=0, has_acted=False, hole=TRIP_KINGS),
        ],
        button=0, status=HandStatus.FLOP, last_bet=50, acting_position=1,
        contributed={"A": 10, "B": 10}, flop=FLOP, turn=None, river=None, deck=[TURN, RIVER],
    )
    world, events, err = run(world, PlayerAction(origin="B", action=BET, bet_amount=50))  # B 跟成 all-in
    assert err is None
    room = _room(world)
    assert room.hand is None and room.status is RoomStatus.PENDING_START
    showdown = next(e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, HandShowDown))
    assert showdown.board == BOARD  # turn/river 已跑齐成完整 5 张
    assert room.seats[0].points == 120 and room.seats[1].points == 0  # A 三条 A 通吃 120
    assert sum(s.points for s in room.seats if s is not None) == 120  # 守恒(锁入 60+60)
    assert not any(isinstance(e, TurnChanged) for e in events)  # 无人可行动,不再起倒计时


def test_uncalled_bet_refunded_no_showdown():
    # heads-up:A 下 40 全弃 → A 唯一未弃 → 未叫注退还 + 收下被叫部分(经边池算法)
    world = hand_world(
        [
            player("A", 60, seat=0, bet_amount=40, has_acted=True, hole=TRIP_ACES),
            player("B", 100, seat=1, bet_amount=0, has_acted=False),
        ],
        button=0, status=HandStatus.FLOP, last_bet=40, acting_position=1,
        contributed={"A": 10, "B": 10},
    )
    world, events, err = run(world, PlayerAction(origin="B", action=FOLD))  # B 弃 → 只剩 A
    assert err is None
    room = _room(world)
    assert room.hand is None and room.status is RoomStatus.PENDING_START
    kinds = [type(e.msg).__name__ for e in events if isinstance(e, Broadcast)]
    assert "HandShowDown" not in kinds  # 无摊牌、不亮牌
    ended = next(e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, HandEnded))
    # A 投 50(10+40)、B 投 10;未叫注 40 退还、被叫池 20 赢得
    assert {r.nickname: r.amount for r in ended.refunds} == {"A": 40}
    assert {w.nickname: w.amount for w in ended.winnings} == {"A": 20}
    # A:开局锁入 110(60+40+10),结算 110-50+60=120;B:锁入 110,剩 100
    assert room.seats[0].points == 120 and room.seats[1].points == 100
    assert sum(s.points for s in room.seats if s is not None) == 220  # 守恒


def test_hand_end_broadcasts_status_back_to_sitting_in():
    # 手尾把 PLAYING 改回 SITTING_IN 必须**广播**:客户端只能从事件知道状态变了。
    # 0082 之前只发 HandEnded,前端因此一直以为大家还 ready —— 界面上开不了第二手、
    # 也点不到 Ready 按钮(浏览器里实测复现)。见 core.md §4 结算 / connection.md「有座不在手」。
    world = hand_world(
        [
            player("A", 60, seat=0, bet_amount=40, has_acted=True, hole=TRIP_ACES),
            player("B", 100, seat=1, bet_amount=0, has_acted=False),
        ],
        button=0, status=HandStatus.FLOP, last_bet=40, acting_position=1,
        contributed={"A": 10, "B": 10},
    )
    world, events, err = run(world, PlayerAction(origin="B", action=FOLD))
    assert err is None

    status_msgs = [e.msg for e in events if isinstance(e, Broadcast) and isinstance(e.msg, UserStatusChanged)]
    assert {m.nickname: m.status for m in status_msgs} == {
        "A": UserStatus.SITTING_IN,
        "B": UserStatus.SITTING_IN,
    }
    # 带上座位号,客户端才知道改的是哪个座
    assert {m.nickname: m.seat_position for m in status_msgs} == {"A": 0, "B": 1}
    # world 与广播一致:广播不是凭空造的
    room = _room(world)
    assert room.users_in_room["A"] is UserStatus.SITTING_IN
    assert room.users_in_room["B"] is UserStatus.SITTING_IN

    # 顺序:先 HandEnded(这手怎么结的)再状态(大家回到了什么状态)
    kinds = [type(e.msg).__name__ for e in events if isinstance(e, Broadcast)]
    assert kinds.index("HandEnded") < kinds.index("UserStatusChanged")
