from dataclasses import dataclass, field
from datetime import datetime

from app.core.cards import Card
from app.core.enums import HandStatus, PlayerStatus, RoomStatus, UserStatus


@dataclass
class UserState:
    uid: int  # immutable DB key; persistence keys on this, not the mutable nickname
    nickname: str
    points: int
    room: str  # always set: lobby users live only in ConnectionManager, not in world.users


@dataclass
class Player:
    nickname: str
    seat_position: int
    points: int
    status: PlayerStatus = PlayerStatus.ACTIVE
    bet_amount: int = 0
    has_acted: bool = False
    hole_cards: tuple[Card, Card] | None = None  # private; never broadcast/logged/persisted


@dataclass
class Hand:
    status: HandStatus
    players: list[Player]  # ordered by action; [0]=SB, [1]=BB (heads-up: [0]=button=SB)
    seq: int
    start_time: datetime  # stamped by shell; core never reads the clock
    acting_position: int | None = None
    last_bet: int = 0
    last_raise_size: int = 0
    deck: list[Card] = field(default_factory=list)  # private
    contributed: dict[str, int] = field(default_factory=dict)
    flop: tuple[Card, Card, Card] | None = None
    turn: Card | None = None
    river: Card | None = None
    epoch: int = 0  # bumped on every action/street advance; Timeout staleness key


@dataclass
class Seat:
    nickname: str
    points: int
    in_game_points: int = 0
    new_here: bool = True
    wait_for_big_blind: bool = False


@dataclass
class EntryVote:
    approvals: set[str] = field(default_factory=set)
    rejected: bool = False


@dataclass
class Room:
    seats: list[Seat | None]  # fixed length = MAX_SEATS
    small_blind: int
    buy_in: int
    users_in_room: dict[str, UserStatus] = field(default_factory=dict)
    hand: Hand | None = None
    status: RoomStatus = RoomStatus.PENDING_START
    button_position: int = 0
    hand_seq: int = 0
    entry_vote: EntryVote | None = None
    waive_entry_for: set[str] = field(default_factory=set)
    leaving: set[str] = field(default_factory=set)  # mid-hand LeaveRoom, evicted at hand end


@dataclass
class World:
    rooms: dict[str, Room] = field(default_factory=dict)
    users: dict[str, UserState] = field(default_factory=dict)
