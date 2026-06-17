from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    INTERNAL = "INTERNAL"  # unexpected bug; reduce raised, normalized by GameLoop
    NO_SUCH_ROOM = "NO_SUCH_ROOM"  # JoinRoom target room does not exist
    ROOM_FULL = "ROOM_FULL"  # JoinRoom: no free seat / capacity reached
    ALREADY_IN_ROOM = "ALREADY_IN_ROOM"  # single-room rule: already in another room
    NOT_IN_ROOM = "NOT_IN_ROOM"  # command requires being in a room
    SEAT_TAKEN = "SEAT_TAKEN"  # SitDown/BuyIn on an occupied seat
    NOT_YOUR_SEAT = "NOT_YOUR_SEAT"  # acting on a seat the caller doesn't own
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"  # not in USER_STATUS_TRANSITIONS
    INSUFFICIENT_POINTS = "INSUFFICIENT_POINTS"  # buy-in exceeds global balance
    HAND_IN_PROGRESS = "HAND_IN_PROGRESS"  # action illegal while a hand runs
    NO_HAND = "NO_HAND"  # action requires a live hand
    NOT_YOUR_TURN = "NOT_YOUR_TURN"  # PlayerAction not from the acting player
    ILLEGAL_ACTION = "ILLEGAL_ACTION"  # action violates betting rules (rules.md ②)
    NOT_ENOUGH_PLAYERS = "NOT_ENOUGH_PLAYERS"  # StartHand with < 2 ready players
    NO_VOTE_IN_PROGRESS = "NO_VOTE_IN_PROGRESS"  # VoteFreeEntry with no open vote
    NOT_A_VOTER = "NOT_A_VOTER"  # voting while not an eligible voter


@dataclass(frozen=True, slots=True)
class Err:
    code: ErrorCode  # machine-readable code; frontend maps to UI/copy
    detail: str = ""  # human-readable context (who, which seat, what state)
