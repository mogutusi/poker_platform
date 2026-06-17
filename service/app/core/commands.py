from dataclasses import dataclass
from datetime import datetime

from app.core.cards import Card
from app.core.enums import PlayerActionType, UserStatus


@dataclass(frozen=True, slots=True)
class Command:
    origin: str | None  # originating nick (= who to reply errors to); None for system commands


@dataclass(frozen=True, slots=True)
class JoinRoom(Command):
    room: str  # target room (the only command carrying a room)
    uid: int  # account id read from DB by shell
    loaded: int  # account's current global points read from DB by shell


@dataclass(frozen=True, slots=True)
class LeaveRoom(Command):
    pass


@dataclass(frozen=True, slots=True)
class SitDown(Command):
    seat: int  # seat index to occupy


@dataclass(frozen=True, slots=True)
class BuyIn(Command):
    seat: int  # seat to fund
    amount: int  # points to move from global balance into the seat stack


@dataclass(frozen=True, slots=True)
class SetUserStatus(Command):
    status: UserStatus  # requested new status
    seat: int | None = None  # seat affected, if the transition involves seating


@dataclass(frozen=True, slots=True)
class SetSmallBlind(Command):
    amount: int  # new small blind (seat 0 config)


@dataclass(frozen=True, slots=True)
class SetBuyIn(Command):
    amount: int  # new room buy-in (seat 0 config)


@dataclass(frozen=True, slots=True)
class StartHand(Command):
    seat: int  # initiator's seat
    started_at: datetime  # wall clock stamped by shell, carried into Hand.start_time
    deck: list[Card] | None = None  # injected in tests/replay; None -> SystemRandom shuffle


@dataclass(frozen=True, slots=True)
class PlayerAction(Command):
    action: PlayerActionType  # FOLD / CHECK / BET
    bet_amount: int | None = None  # target total for this street (required for BET)


@dataclass(frozen=True, slots=True)
class RoomChat(Command):
    text: str  # chat body; read-only command, produces Broadcast(ChatMessage)


@dataclass(frozen=True, slots=True)
class OpenFreeEntryVote(Command):
    pass


@dataclass(frozen=True, slots=True)
class VoteFreeEntry(Command):
    approve: bool  # this voter's stance on waiving entry blinds


@dataclass(frozen=True, slots=True)
class Connect(Command):
    nick: str  # connecting nick; reconnect if already in world.users (OFFLINE)


@dataclass(frozen=True, slots=True)
class Disconnect(Command):
    nick: str  # disconnecting nick; mark OFFLINE if in a room


@dataclass(frozen=True, slots=True)
class Timeout(Command):
    nick: str  # whose turn timed out (game target, not error recipient)
    epoch: int  # hand.epoch snapshot at schedule time; staleness check on arrival


@dataclass(frozen=True, slots=True)
class Cleanup(Command):
    nick: str  # occupant whose seat-hold expired (OFFLINE past liveness)
