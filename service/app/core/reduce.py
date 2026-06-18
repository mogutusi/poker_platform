# core 游戏状态机:reduce(work, cmd) -> (events, err)。
# 纯同步、不 await、不碰 IO/DB、不读墙钟(不变量 1);改的是 GameLoop 给的工作副本,
# 成功 commit、失败/异常丢弃(见 core.md / storage.md)。本篇(0010)只落地 StartHand。

from app.core.commands import Command, StartHand
from app.core.deck import shuffled_deck
from app.core.domain import Hand, Player, Room, Work
from app.core.enums import (
    HandStatus,
    PlayerStatus,
    RoomStatus,
    UserStatus,
)
from app.core.errors import Err, ErrorCode
from app.core.events import Broadcast, Event, Personal, TurnChanged
from app.core.messages import HandStarted, HandStatusChanged, HoleCards, PlayerView
from app.core.rules import betting, blinds

ReduceResult = tuple[list[Event], Err | None]


def reduce(work: Work, cmd: Command) -> ReduceResult:
    # 顶层按命令类型分发;每个 handler:先校验(返回 Err)→ 改工作副本 → 产出 events。
    match cmd:
        case StartHand():
            return _start_hand(work, cmd)
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
    if len(deck) < 2 * n:
        return [], Err(ErrorCode.INTERNAL, f"牌堆 {len(deck)} 张不足以给 {n} 人发底牌")

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

    return _start_hand_events(work.room_name, room, hand, small_blind, big_blind), None


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
