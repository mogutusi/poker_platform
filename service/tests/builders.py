"""具名局面 builder(testing.md):别在测试里手搓裸 dict。

随 P1 扩展(加发牌/动作序列的 run() 等);P0 先给构造 world 的最小集。
"""

from datetime import datetime, timezone

from app.core.commands import Command
from app.core.deck import FULL_DECK
from app.core.domain import Hand, Player, Room, Seat, UserState, World
from app.core.enums import HandStatus, PlayerStatus, UserStatus
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
) -> Player:
    return Player(
        nickname=nickname,
        seat_position=seat,
        points=points,
        status=status,
        bet_amount=bet_amount,
        has_acted=has_acted,
    )


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
