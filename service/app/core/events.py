from dataclasses import dataclass


class ServerMessage:
    """wire outbound message base (P6)."""


class PersistPayload:
    """delayDB write item base (P4)."""


@dataclass(frozen=True, slots=True)
class Event:
    pass


@dataclass(frozen=True, slots=True)
class Broadcast(Event):
    room: str  # send to all in-room members of this room
    msg: ServerMessage  # wire payload (privacy-trimmed at projection)


@dataclass(frozen=True, slots=True)
class Personal(Event):
    nick: str  # single recipient (hole cards / StateSnapshot / UserLeft receipt)
    msg: ServerMessage  # wire payload for this nick only


@dataclass(frozen=True, slots=True)
class TurnChanged(Event):
    room: str  # room whose action timer to (re)start
    acting_nick: str  # new actor; Timer needs it to build Timeout(nick, epoch)
    epoch: int  # current hand.epoch, for Timeout staleness


@dataclass(frozen=True, slots=True)
class ClearAction(Event):
    room: str  # stop this room's action timer (hand ended)


@dataclass(frozen=True, slots=True)
class Persist(Event):
    payload: PersistPayload  # snapshot value handed to delayDB (state-write / event-write)
