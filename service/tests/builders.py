"""具名局面 builder(testing.md):别在测试里手搓裸 dict。

随 P1 扩展(加发牌/动作序列的 run() 等);P0 先给构造 world 的最小集。
"""

from datetime import datetime, timezone

from app.core.domain import Room, Seat, UserState, World
from app.core.enums import UserStatus

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 固定墙钟,core 不读时钟、测试可断言

DEFAULT_SMALL_BLIND = 1
DEFAULT_BUY_IN = 100
DEFAULT_MAX_SEATS = 6


def seat(nickname: str, points: int, *, new_here: bool = True) -> Seat:
    return Seat(nickname=nickname, points=points, new_here=new_here)


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
