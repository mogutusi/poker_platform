from dataclasses import dataclass
from datetime import datetime

from app.core.cards import Card
from app.core.enums import PlayerActionType, UserStatus


@dataclass(frozen=True, slots=True)
class Command:
    origin: str | None  # originating nick; None for system commands


@dataclass(frozen=True, slots=True)
class JoinRoom(Command):
    room: str  # the only command carrying a room
    uid: int
    loaded: int


@dataclass(frozen=True, slots=True)
class LeaveRoom(Command):
    pass


@dataclass(frozen=True, slots=True)
class SitDown(Command):
    seat: int


@dataclass(frozen=True, slots=True)
class BuyIn(Command):
    seat: int
    amount: int


@dataclass(frozen=True, slots=True)
class SetUserStatus(Command):
    status: UserStatus
    seat: int | None = None


@dataclass(frozen=True, slots=True)
class SetSmallBlind(Command):
    amount: int


@dataclass(frozen=True, slots=True)
class SetBuyIn(Command):
    amount: int


@dataclass(frozen=True, slots=True)
class StartHand(Command):
    seat: int
    started_at: datetime
    deck: list[Card] | None = None  # injected in tests/replay; None → SystemRandom shuffle


@dataclass(frozen=True, slots=True)
class PlayerAction(Command):
    action: PlayerActionType
    bet_amount: int | None = None


@dataclass(frozen=True, slots=True)
class RoomChat(Command):
    text: str


@dataclass(frozen=True, slots=True)
class OpenFreeEntryVote(Command):
    pass


@dataclass(frozen=True, slots=True)
class VoteFreeEntry(Command):
    approve: bool


@dataclass(frozen=True, slots=True)
class Connect(Command):
    nick: str


@dataclass(frozen=True, slots=True)
class Disconnect(Command):
    nick: str


@dataclass(frozen=True, slots=True)
class Timeout(Command):
    nick: str
    epoch: int


@dataclass(frozen=True, slots=True)
class Cleanup(Command):
    nick: str
