"""具名局面 builder(testing.md):别在测试里手搓裸 dict。

随 P1 扩展(加发牌/动作序列的 run() 等);P0 先给构造 world 的最小集。
"""

from datetime import datetime, timezone

from app.core.cards import Card, CardRank, CardSuit
from app.core.commands import Command
from app.core.deck import FULL_DECK
from app.core.domain import Hand, Player, Room, Seat, UserState, World
from app.core.enums import HandStatus, PlayerStatus, RoomStatus, UserStatus
from app.core.reduce import reduce
from app.shell.world import checkout, commit

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 固定墙钟,core 不读时钟、测试可断言

DEFAULT_SMALL_BLIND = 1
DEFAULT_BUY_IN = 100
DEFAULT_MAX_SEATS = 6

DECK = list(FULL_DECK)  # 固定牌堆:StartHand(deck=DECK) 注入,玩家 j 得 (DECK[j], DECK[N+j])


def seat(
    nickname: str,
    points: int,
    *,
    new_here: bool = True,
    wait_for_big_blind: bool = False,
) -> Seat:
    return Seat(
        nickname=nickname,
        points=points,
        new_here=new_here,
        wait_for_big_blind=wait_for_big_blind,
    )


def room_with(
    *,
    seats: list[Seat | None] | None = None,
    button: int = 0,
    small_blind: int = DEFAULT_SMALL_BLIND,
    buy_in: int = DEFAULT_BUY_IN,
    max_seats: int = DEFAULT_MAX_SEATS,
    users_in_room: dict[str, UserStatus] | None = None,
) -> Room:
    seat_list: list[Seat | None] = list(seats) if seats is not None else [None] * max_seats
    if len(seat_list) < max_seats:
        seat_list += [None] * (max_seats - len(seat_list))
    return Room(
        seats=seat_list,
        small_blind=small_blind,
        buy_in=buy_in,
        button_position=button,
        users_in_room=users_in_room if users_in_room is not None else {},
    )


def make_world(
    rooms: dict[str, Room] | None = None,
    users: dict[str, UserState] | None = None,
) -> World:
    return World(rooms=rooms or {}, users=users or {})


def player(
    nickname: str,
    points: int,
    *,
    seat: int = 0,
    bet_amount: int = 0,
    status: PlayerStatus = PlayerStatus.ACTIVE,
    has_acted: bool = False,
    hole: tuple[Card, Card] | None = None,
) -> Player:
    return Player(
        nickname=nickname,
        seat_position=seat,
        points=points,
        status=status,
        bet_amount=bet_amount,
        has_acted=has_acted,
        hole_cards=hole,
    )


_RANK_BY_CHAR = {r.value: r for r in CardRank}
_SUIT_BY_CHAR = {s.value: s for s in CardSuit}


def card(code: str) -> Card:
    # "As" / "Th" / "2c" → Card;摊牌测试里手写牌面用(rank 字符 + suit 字符)
    return Card(_RANK_BY_CHAR[code[0]], _SUIT_BY_CHAR[code[1]])


def deck_for(holes: list[tuple[Card, Card]], board: list[Card]) -> list[Card]:
    # 排成 reduce 发牌顺序:player j 得 (deck[j], deck[n+j]),其后 5 张公共牌,余下用 FULL_DECK 补足。
    # holes 按**行动序**(players 顺序)给。
    ordered = [h[0] for h in holes] + [h[1] for h in holes] + list(board)
    used = set(ordered)
    return ordered + [c for c in FULL_DECK if c not in used]


def hand_world(
    players: list[Player],
    *,
    button: int = 0,
    small_blind: int = DEFAULT_SMALL_BLIND,
    status: HandStatus = HandStatus.RIVER,
    last_bet: int = 0,
    last_raise_size: int = 0,
    acting_position: int = 0,
    contributed: dict[str, int] | None = None,
    flop: tuple[Card, Card, Card] | None = None,
    turn: Card | None = None,
    river: Card | None = None,
    deck: list[Card] | None = None,
    room_name: str = "r1",
    max_seats: int = DEFAULT_MAX_SEATS,
) -> World:
    # 拼一个「手牌进行中」的 world(供结算/推进测试直接驱动收尾动作)。players 按行动序给;
    # 各 Seat 重建:points=0(已锁入)、in_game_points=锁入快照(=本手剩余 + 本街投入 + 已并入 contributed)。
    contributed = dict(contributed) if contributed is not None else {}
    seats: list[Seat | None] = [None] * max_seats
    users_in_room: dict[str, UserStatus] = {}
    users: dict[str, UserState] = {}
    for uid, p in enumerate(players):
        locked = p.points + p.bet_amount + contributed.get(p.nickname, 0)
        seats[p.seat_position] = Seat(nickname=p.nickname, points=0, in_game_points=locked, new_here=False)
        users_in_room[p.nickname] = UserStatus.PLAYING
        users[p.nickname] = UserState(uid=uid, nickname=p.nickname, points=0, room=room_name)
    h = Hand(
        status=status,
        players=list(players),
        seq=1,
        start_time=T0,
        acting_position=acting_position,
        last_bet=last_bet,
        last_raise_size=last_raise_size,
        contributed=contributed,
        flop=flop,
        turn=turn,
        river=river,
        deck=deck if deck is not None else [],
    )
    room = Room(
        seats=seats,
        small_blind=small_blind,
        buy_in=DEFAULT_BUY_IN,
        button_position=button,
        users_in_room=users_in_room,
        status=RoomStatus.HAND_STARTED,
        hand=h,
        hand_seq=1,
    )
    return World(rooms={room_name: room}, users=users)


def make_table(
    seat_map: dict[int, Seat],
    *,
    button: int = 0,
    small_blind: int = DEFAULT_SMALL_BLIND,
    buy_in: int = DEFAULT_BUY_IN,
    max_seats: int = DEFAULT_MAX_SEATS,
    room_name: str = "r1",
    statuses: dict[str, UserStatus] | None = None,
    waive: set[str] | None = None,
) -> World:
    # 拼一张「已就座」的桌:seat_map 给座位号→Seat;默认全员 READY_TO_PLAY、在 world.users 里。
    # statuses 覆盖个别人的 UserStatus(如 SITTING_OUT);waive 预置 room.waive_entry_for。
    seats: list[Seat | None] = [None] * max_seats
    users_in_room: dict[str, UserStatus] = {}
    users: dict[str, UserState] = {}
    for uid, (idx, s) in enumerate(sorted(seat_map.items())):
        seats[idx] = s
        status = (statuses or {}).get(s.nickname, UserStatus.READY_TO_PLAY)
        users_in_room[s.nickname] = status
        users[s.nickname] = UserState(uid=uid, nickname=s.nickname, points=0, room=room_name)
    room = Room(
        seats=seats,
        small_blind=small_blind,
        buy_in=buy_in,
        button_position=button,
        users_in_room=users_in_room,
        waive_entry_for=waive if waive is not None else set(),
    )
    return World(rooms={room_name: room}, users=users)


def run(world: World, cmd: Command) -> tuple[World, list, object]:
    # GameLoop 单步:checkout → reduce → 成功 commit / 失败丢弃。返回 (world, events, err)。
    work = checkout(world, cmd)
    events, err = reduce(work, cmd)
    if err is None:
        commit(world, work)
    return world, events, err


def hand(
    players: list[Player],
    *,
    last_bet: int = 0,
    last_raise_size: int = 0,
    contributed: dict[str, int] | None = None,
    status: HandStatus = HandStatus.PRE_FLOP,
    acting_position: int = 0,
) -> Hand:
    return Hand(
        status=status,
        players=players,
        seq=1,
        start_time=T0,
        acting_position=acting_position,
        last_bet=last_bet,
        last_raise_size=last_raise_size,
        contributed=contributed if contributed is not None else {},
    )
