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
    room: str
    msg: ServerMessage


@dataclass(frozen=True, slots=True)
class Personal(Event):
    nick: str
    msg: ServerMessage


@dataclass(frozen=True, slots=True)
class TurnChanged(Event):
    room: str
    epoch: int


@dataclass(frozen=True, slots=True)
class ClearAction(Event):
    room: str


@dataclass(frozen=True, slots=True)
class Persist(Event):
    payload: PersistPayload
