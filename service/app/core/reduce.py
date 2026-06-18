# core 游戏状态机:reduce(work, cmd) -> (events, err)。
# 纯同步、不 await、不碰 IO/DB、不读墙钟(不变量 1);改的是 GameLoop 给的工作副本,
# 成功 commit、失败/异常丢弃(见 core.md / storage.md)。本篇(0010)只落地 StartHand。

from app.core.cards import Card
from app.core.commands import Command, PlayerAction, StartHand
from app.core.deck import BOARD_CARDS, evaluate, shuffled_deck
from app.core.domain import Hand, Player, Room, Work
from app.core.enums import (
    HandStatus,
    PlayerStatus,
    RoomStatus,
    UserStatus,
)
from app.core.errors import Err, ErrorCode
from app.core.events import Broadcast, ClearAction, Event, Personal, Persist, TurnChanged
from app.core.messages import (
    HandEnded,
    HandShowDown,
    HandStarted,
    HandStatusChanged,
    HoleCards,
    NickAmount,
    PlayerActed,
    PlayerView,
    ShowdownReveal,
)
from app.core.records import HandRecordWrite, ParticipantWrite
from app.core.rules import betting, blinds, sidepot

ReduceResult = tuple[list[Event], Err | None]


def reduce(work: Work, cmd: Command) -> ReduceResult:
    # 顶层按命令类型分发;每个 handler:先校验(返回 Err)→ 改工作副本 → 产出 events。
    match cmd:
        case StartHand():
            return _start_hand(work, cmd)
        case PlayerAction():
            return _player_action(work, cmd)
        case _:
            # 其余命令的 handler 随后续变更逐个落地(见 refactor/TODO P1);未实现期间
            # 按内部错误归一(工作副本被丢弃、world 不动),全部落地后此分支应不可达。
            return [], Err(ErrorCode.INTERNAL, f"reduce 暂未实现命令 {type(cmd).__name__}")


# ── 开局(StartHand)── rules.md ① 开局那半 + core.md §1
def _start_hand(work: Work, cmd: StartHand) -> ReduceResult:
    room = work.room
    if room is None:
        return [], Err(ErrorCode.NOT_IN_ROOM, "StartHand:发起人不在任何房间")
    if room.status is not RoomStatus.PENDING_START or room.hand is not None:
        return [], Err(ErrorCode.HAND_IN_PROGRESS, "已有手牌进行中")

    # 发起人须在其声明的座位、且 READY_TO_PLAY
    if not (0 <= cmd.seat < len(room.seats)):
        return [], Err(ErrorCode.NOT_YOUR_SEAT, f"座位号 {cmd.seat} 越界")
    seat = room.seats[cmd.seat]
    if seat is None or seat.nickname != cmd.origin:
        return [], Err(ErrorCode.NOT_YOUR_SEAT, f"座位 {cmd.seat} 不属于 {cmd.origin}")
    if room.users_in_room.get(cmd.origin) is not UserStatus.READY_TO_PLAY:
        return [], Err(ErrorCode.NOT_READY, f"{cmd.origin} 未 READY_TO_PLAY,不能开局")

    # 入局资格:本手被发牌的座位下标集合 + 需付入局 BB 的入局者
    dealt, paying_entrants = _eligible_seats(room)
    if len(dealt) < 2:
        return [], Err(ErrorCode.NOT_ENOUGH_PLAYERS, f"在局 ready 玩家不足 2(={len(dealt)})")

    # 定位(复用 blinds):庄推进到下一在局座位,排成行动序 players([0]=SB、[1]=BB)
    button = blinds.advance_button(room.button_position, dealt)
    order = blinds.seat_order(button, dealt)
    n = len(order)
    small_blind = room.small_blind
    big_blind = blinds.BIG_BLIND_MULTIPLE * small_blind

    # 取牌堆并校验够发 2N 张:生产洗牌恒 52;注入牌堆(重放/测试)若过短先返 Err,守 helper
    # 「绝不 raise」契约(core.md)。先校验后改工作副本——此 return 在任何 mutation 之前。
    deck = list(cmd.deck) if cmd.deck is not None else shuffled_deck()
    if len(deck) < 2 * n + BOARD_CARDS:
        # 一手最多需 2N 张底牌 + 5 张公共牌;一次校验到底,使后续发公共牌的 helper 永不缺牌(绝不 raise)。
        # 生产洗牌恒 52 张不受影响;注入过短牌堆(测试/重放)返 Err、丢工作副本。
        return [], Err(ErrorCode.INTERNAL, f"牌堆 {len(deck)} 张不足一手({n} 人)所需")

    # 锁筹:Seat.points → Player.points,存 in_game_points 快照,Seat.points 清零
    players: list[Player] = []
    for idx in order:
        s = room.seats[idx]
        assert s is not None  # order ⊆ dealt ⊆ 已占座位
        s.in_game_points = s.points
        players.append(Player(nickname=s.nickname, seat_position=idx, points=s.points))
        s.points = 0

    room.hand_seq += 1
    hand = Hand(
        status=HandStatus.PRE_FLOP,
        players=players,
        seq=room.hand_seq,
        start_time=cmd.started_at,
    )

    # 下盲(结构盲注)+ 入局 post(付盲即玩,live:投一个 BB 进 bet_amount)
    blinds.post_blinds(hand, small_blind)
    blind_seats = {players[0].seat_position, players[1].seat_position}
    for p in players:
        if p.seat_position in paying_entrants and p.seat_position not in blind_seats:
            _post_entry(p, big_blind)  # 占盲位的入局者以结构盲注充作入局付费,不重复 post

    # 发底牌(不烧牌):轮转取前 2N 张,余下存 hand.deck 供后续街(deck 已在上方校验长度)
    for j, p in enumerate(players):
        p.hole_cards = (deck[j], deck[n + j])
    hand.deck = deck[2 * n:]

    # 置 PLAYING / HAND_STARTED;入局者清 new_here / wait_for_big_blind;消费 waive 快照
    for p in players:
        room.users_in_room[p.nickname] = UserStatus.PLAYING
        dealt_seat = room.seats[p.seat_position]
        assert dealt_seat is not None
        dealt_seat.new_here = False
        dealt_seat.wait_for_big_blind = False
    room.waive_entry_for = set()
    room.button_position = button
    room.status = RoomStatus.HAND_STARTED
    room.hand = hand

    # preflop 首行动 = BB 下一位(heads-up 自然回到 players[0]=button/SB)
    hand.acting_position = betting.next_active_position(hand, 1)

    events = _start_hand_events(work.room_name, room, hand, small_blind, big_blind)
    if hand.acting_position is None:
        # born-all-in:全员投盲即 all-in、无人可行动 → 不等动作,立即结算本街、跑公共牌摊牌
        # (完成 0010 §6 待办:街推进入口须接住此手,否则手卡死、无人察觉)。
        events += _close_street(work, hand, big_blind)
    return events, None


def _eligible_seats(room: Room) -> tuple[set[int], set[int]]:
    # 返回 (本手被发牌的座位下标集合, 其中需付入局 BB 的座位下标集合)。
    # 资格(rules.md ①):READY_TO_PLAY 且——非 new_here(上一手在局)直接发;
    # bootstrap(无任何已入局玩家)全员免付发;new_here 在 waive 快照里免付发;
    # new_here 付盲即玩(默认)发并 post 一个 BB;new_here 选等大盲则本手不发(等 BB 路过,时机留 0011)。
    ready = [
        (i, s)
        for i, s in enumerate(room.seats)
        if s is not None and room.users_in_room.get(s.nickname) is UserStatus.READY_TO_PLAY
    ]
    # bootstrap 看**整桌已占座位**(rules.md ① 行 60「桌上还没有任何已入局玩家」),不只 ready 子集:
    # 坐出/未 ready 的已入局(new_here=False)玩家仍堵掉新人的免费入局,守防躲盲不变量(行 46/50)。
    bootstrap = not any(s is not None and not s.new_here for s in room.seats)

    dealt: set[int] = set()
    paying_entrants: set[int] = set()
    for i, s in ready:
        if not s.new_here or bootstrap or s.nickname in room.waive_entry_for:
            dealt.add(i)  # 已入局 / bootstrap / 免盲快照:免付入局
        elif not s.wait_for_big_blind:
            dealt.add(i)  # 付盲即玩(默认)
            paying_entrants.add(i)
        # else: 等大盲 → 本手不发牌(等 BB 路过其座,0011)
    return dealt, paying_entrants


def _post_entry(player: Player, big_blind: int) -> None:
    # 入局 post(live):从 points 投一个 BB 进 bet_amount;短码即 all-in;不置 has_acted。
    amount = min(big_blind, player.points)
    player.points -= amount
    player.bet_amount += amount
    if player.points == 0:
        player.status = PlayerStatus.ALLIN


def _start_hand_events(
    room_name: str | None,
    room: Room,
    hand: Hand,
    small_blind: int,
    big_blind: int,
) -> list[Event]:
    # 投影为出站载荷(快照值,无活引用);顺序按 core.md §事件:HandStarted → HoleCards* → HandStatusChanged → TurnChanged
    assert room_name is not None  # 开局必有目标房
    views = tuple(
        PlayerView(p.seat_position, p.nickname, p.points, p.bet_amount, p.status) for p in hand.players
    )
    started = HandStarted(
        hand_seq=hand.seq,
        button_position=room.button_position,
        small_blind=small_blind,
        big_blind=big_blind,
        players=views,
        acting_position=hand.acting_position,
    )
    events: list[Event] = [Broadcast(room=room_name, msg=started)]
    for p in hand.players:
        assert p.hole_cards is not None  # 开局已发
        events.append(Personal(nick=p.nickname, msg=HoleCards(cards=p.hole_cards)))
    events.append(Broadcast(room=room_name, msg=HandStatusChanged(status=hand.status, board=())))
    if hand.acting_position is not None:
        acting = hand.players[hand.acting_position]
        events.append(TurnChanged(room=room_name, acting_nick=acting.nickname, epoch=hand.epoch))
    return events


# ── 玩家动作(PlayerAction)── rules.md ② 行动 + ③ 街推进/摊牌/结算 + core.md §2-4
def _player_action(work: Work, cmd: PlayerAction) -> ReduceResult:
    room = work.room
    if room is None:
        return [], Err(ErrorCode.NOT_IN_ROOM, "PlayerAction:发起人不在任何房间")
    hand = room.hand
    if hand is None:
        return [], Err(ErrorCode.NO_HAND, "无进行中手牌")
    pos = hand.acting_position
    if pos is None or hand.players[pos].nickname != cmd.origin:
        return [], Err(ErrorCode.NOT_YOUR_TURN, f"非 {cmd.origin} 的行动回合")
    actor = hand.players[pos]

    big_blind = blinds.BIG_BLIND_MULTIPLE * room.small_blind
    err = betting.apply_action(hand, actor, cmd.action, cmd.bet_amount, big_blind)
    if err is not None:
        return [], err  # 违规动作:丢工作副本、world 不动(失败安全)

    # 动作合法、已改 hand。先快照行动结果(街若关闭,settle_street 会清零 bet_amount),再推进。
    acted_bet, acted_points, acted_status = actor.bet_amount, actor.points, actor.status
    follow = _advance(work, hand, big_blind)
    acted = Broadcast(
        room=work.room_name,
        msg=PlayerActed(
            seat_position=actor.seat_position,
            nickname=actor.nickname,
            action=cmd.action,
            bet_amount=acted_bet,
            points=acted_points,
            status=acted_status,
            last_bet=hand.last_bet,
            pot=_pot(hand),
            acting_position=hand.acting_position,
        ),
    )
    return [acted, *follow], None


def _advance(work: Work, hand: Hand, big_blind: int) -> list[Event]:
    # 一次行动后的推进:本街关闭 → 结算分支;否则换下一个 ACTIVE 行动者(epoch+1 + TurnChanged)。
    if betting.street_closed(hand):
        return _close_street(work, hand, big_blind)
    assert hand.acting_position is not None  # 未关 ⇒ 当前行动者存在(已在 _player_action 校验)
    hand.acting_position = betting.next_active_position(hand, hand.acting_position)
    hand.epoch += 1
    assert hand.acting_position is not None  # 未关 ⇒ 必有另一个 ACTIVE(见 rules.md ② street_closed)
    nxt = hand.players[hand.acting_position]
    return [TurnChanged(room=work.room_name, acting_nick=nxt.nickname, epoch=hand.epoch)]


def _close_street(work: Work, hand: Hand, big_blind: int) -> list[Event]:
    # 本街关闭:并入 contributed、重置本街计数,然后按 rules.md ③ 分支。
    betting.settle_street(hand, big_blind)
    live = [p for p in hand.players if p.status is not PlayerStatus.FOLDED]
    if len(live) == 1:
        return _settle_and_end(work, hand, reveal=False)  # 只剩一人未弃 → 无摊牌直接结束
    can_act = [p for p in hand.players if p.status is PlayerStatus.ACTIVE]
    if len(can_act) <= 1 or hand.status is HandStatus.RIVER:
        # 其余 all-in(无人再行动)→ 跑完公共牌摊牌;或 RIVER 正常关闭 → 摊牌
        return _settle_and_end(work, hand, reveal=True)
    # 否则进下一街,继续下注
    nxt = hand.status.next_status
    assert nxt is not None and nxt is not HandStatus.SHOWDOWN  # RIVER 已在上面分流
    _deal_board(hand, nxt)
    hand.status = nxt
    hand.acting_position = _postflop_first(hand, room=work.room)
    hand.epoch += 1
    assert hand.acting_position is not None  # ≥2 可行动 ⇒ postflop 首行动者存在
    return [
        Broadcast(room=work.room_name, msg=HandStatusChanged(status=nxt, board=tuple(_board(hand)))),
        TurnChanged(
            room=work.room_name,
            acting_nick=hand.players[hand.acting_position].nickname,
            epoch=hand.epoch,
        ),
    ]


def _settle_and_end(work: Work, hand: Hand, *, reveal: bool) -> list[Event]:
    # 摊牌(reveal)或无摊牌(单人)结算:边池(rules.md ③)→ 分配进 Player.points → 收尾。
    room = work.room
    assert room is not None
    live = [p for p in hand.players if p.status is not PlayerStatus.FOLDED]
    events: list[Event] = []
    if reveal:
        _run_out_board(hand)  # 补齐未发公共牌(已满则幂等);摊牌需完整 5 张
        board = _board(hand)
        strength: dict[str, int] = {}
        reveals: list[ShowdownReveal] = []
        for p in live:
            assert p.hole_cards is not None  # 在局者开局已发底牌
            strength[p.nickname] = evaluate(board, p.hole_cards)
            reveals.append(ShowdownReveal(p.seat_position, p.nickname, p.hole_cards))
        hand.status = HandStatus.SHOWDOWN
        events.append(
            Broadcast(room=work.room_name, msg=HandShowDown(board=tuple(board), reveals=tuple(reveals)))
        )
    else:
        strength = {live[0].nickname: 0}  # 仅一名未弃牌者,牌力无需比较

    payout = sidepot.settle(
        contributed=hand.contributed,
        live={p.nickname for p in live},
        strength=strength,
        seat_of={p.nickname: p.seat_position for p in hand.players},
        button_position=room.button_position,
        seat_size=len(room.seats),
    )
    for nick, amount in payout.total.items():
        _by_nick(hand, nick).points += amount  # 赢得 + 退还进本手剩余筹码,随后由 finalize 还回座位

    events += _finalize_hand(work, hand, payout)
    return events


def _finalize_hand(work: Work, hand: Hand, payout: sidepot.Payout) -> list[Event]:
    # 收尾:各 Player.points 还回 Seat、清锁筹、PLAYING→SITTING_IN;产 HandEnded + Persist + ClearAction。
    assert work.room_name is not None
    room = work.room
    assert room is not None
    participants: list[ParticipantWrite] = []
    for p in hand.players:
        s = room.seats[p.seat_position]
        assert s is not None
        initial = s.in_game_points  # 开局锁入快照
        s.points += p.points  # p.points 已含赢得 / 退还
        s.in_game_points = 0
        participants.append(
            ParticipantWrite(uid=work.users[p.nickname].uid, initial_points=initial, final_points=s.points)
        )
        if room.users_in_room.get(p.nickname) is UserStatus.PLAYING:
            room.users_in_room[p.nickname] = UserStatus.SITTING_IN

    record = HandRecordWrite(
        dedupe_key=f"{work.room_name}:{hand.seq}",
        start_time=hand.start_time,
        final_pot=sum(pot.amount for pot in payout.pots),
        participants=tuple(participants),
    )
    ended = HandEnded(
        winnings=tuple(NickAmount(n, a) for n, a in payout.winnings.items()),
        refunds=tuple(NickAmount(n, a) for n, a in payout.refunds.items()),
    )

    hand.status = HandStatus.ENDING
    hand.acting_position = None
    room.hand = None
    room.status = RoomStatus.PENDING_START
    return [
        Broadcast(room=work.room_name, msg=ended),
        Persist(payload=record),
        ClearAction(room=work.room_name),
    ]


# ── 纯计算 helper(发牌 / 位次 / 底池)──
def _deal_board(hand: Hand, status: HandStatus) -> None:
    # 发该街公共牌(不烧牌):从 hand.deck 顺取。_start_hand 已校验牌堆够 2N+5,故不缺牌。
    if status is HandStatus.FLOP:
        hand.flop = (hand.deck.pop(0), hand.deck.pop(0), hand.deck.pop(0))
    elif status is HandStatus.TURN:
        hand.turn = hand.deck.pop(0)
    elif status is HandStatus.RIVER:
        hand.river = hand.deck.pop(0)


def _run_out_board(hand: Hand) -> None:
    # 一次发齐所有未发公共牌(≤1 人可行动 / born-all-in 时);已发的不动(幂等)。
    if hand.flop is None:
        _deal_board(hand, HandStatus.FLOP)
    if hand.turn is None:
        _deal_board(hand, HandStatus.TURN)
    if hand.river is None:
        _deal_board(hand, HandStatus.RIVER)


def _board(hand: Hand) -> list[Card]:
    # 当前已发公共牌(flop → turn → river 顺序)。
    cards: list[Card] = list(hand.flop) if hand.flop is not None else []
    if hand.turn is not None:
        cards.append(hand.turn)
    if hand.river is not None:
        cards.append(hand.river)
    return cards


def _postflop_first(hand: Hand, *, room: Room | None) -> int | None:
    # postflop 首行动 = 庄家下一位起第一个 ACTIVE(≥3 人为 SB 位;heads-up 为 BB);见 rules.md ①。
    assert room is not None
    button_idx = next(i for i, p in enumerate(hand.players) if p.seat_position == room.button_position)
    return betting.next_active_position(hand, button_idx)


def _pot(hand: Hand) -> int:
    # 当前总底池:已并入的 contributed + 各人本街尚未并入的 bet_amount。
    return sum(hand.contributed.values()) + sum(p.bet_amount for p in hand.players)


def _by_nick(hand: Hand, nick: str) -> Player:
    return next(p for p in hand.players if p.nickname == nick)
