from dataclasses import dataclass, field
from datetime import datetime

from app.core.cards import Card
from app.core.enums import HandStatus, PlayerStatus, RoomStatus, UserStatus


@dataclass
class UserState:
    uid: int  # immutable DB key (= User.id); persistence keys on this, not the mutable nickname
    nickname: str  # mutable display name; also the world.users key (changeable only in lobby)
    points: int  # global point balance, memory-authoritative
    room: str  # current room; always set — lobby users live in ConnectionManager, not world.users


@dataclass
class Player:
    nickname: str  # identity within the hand
    seat_position: int  # which Room.seats index this player occupies
    points: int  # remaining stack this hand (spendable on bets)
    status: PlayerStatus = PlayerStatus.ACTIVE  # ACTIVE / FOLDED / ALLIN
    bet_amount: int = 0  # invested this street; folded into contributed and zeroed at street end
    has_acted: bool = False  # voluntarily acted this street; reset on street start / raise reopen
    hole_cards: tuple[Card, Card] | None = None  # private; never broadcast/logged/persisted


@dataclass
class Hand:
    status: HandStatus  # current street (PRE_FLOP..ENDING)
    players: list[Player]  # action order: [0]=SB, [1]=BB (heads-up: [0]=button=SB)
    seq: int  # = room.hand_seq at start; monotonic per room; dedupe_key = f"{room}:{seq}"
    start_time: datetime  # stamped by shell; core stores but never reads it (no wall-clock branching)
    acting_position: int | None = None  # players[acting_position] = current actor
    last_bet: int = 0  # amount to call this street
    last_raise_size: int = 0  # size of the last raise increment, for min-raise checks
    deck: list[Card] = field(default_factory=list)  # private: undealt cards
    contributed: dict[str, int] = field(default_factory=dict)  # nick -> total invested this hand
    flop: tuple[Card, Card, Card] | None = None
    turn: Card | None = None
    river: Card | None = None
    epoch: int = 0  # bumped on every action/street advance; Timeout staleness key


@dataclass
class Seat:
    nickname: str  # occupant
    points: int  # table stack available when not in a hand
    in_game_points: int = 0  # principal locked into the current Hand (snapshot, for settle/record)
    new_here: bool = True  # didn't play last hand; entry requires post-or-wait (rules.md ①)
    wait_for_big_blind: bool = False  # chose "wait for BB (free)" over "post now" (wire flag)


@dataclass
class EntryVote:
    approvals: set[str] = field(default_factory=set)  # voter nicks who approved so far
    rejected: bool = False  # any reject ends the vote as failed


@dataclass
class Room:
    seats: list[Seat | None]  # fixed length = MAX_SEATS; None = empty seat
    small_blind: int  # SB amount; BB = 2 * small_blind
    buy_in: int  # default buy-in amount for this room
    users_in_room: dict[str, UserStatus] = field(default_factory=dict)  # in-room nick -> status machine
    hand: Hand | None = None  # current hand, None between hands
    status: RoomStatus = RoomStatus.PENDING_START  # PENDING_START / HAND_STARTED
    button_position: int = 0  # dealer seat index
    hand_seq: int = 0  # monotonic hand counter within the room
    entry_vote: EntryVote | None = None  # in-progress free-entry vote, if any
    waive_entry_for: set[str] = field(default_factory=set)  # snapshot: new_here nicks granted free entry next hand
    leaving: set[str] = field(default_factory=set)  # mid-hand LeaveRoom: auto-folded, evicted at hand end


@dataclass
class World:
    rooms: dict[str, Room] = field(default_factory=dict)  # room name -> Room
    users: dict[str, UserState] = field(default_factory=dict)  # nick -> UserState (only in-room users)
