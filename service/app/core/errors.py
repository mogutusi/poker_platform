from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    INTERNAL = "internal"
    INVALID_COMMAND = "invalid_command"
    ROOM_NOT_FOUND = "room_not_found"
    ROOM_FULL = "room_full"
    ALREADY_IN_ROOM = "already_in_room"
    NOT_IN_ROOM = "not_in_room"
    SEAT_TAKEN = "seat_taken"
    SEAT_EMPTY = "seat_empty"
    NOT_YOUR_SEAT = "not_your_seat"
    INVALID_STATUS_TRANSITION = "invalid_status_transition"
    INSUFFICIENT_POINTS = "insufficient_points"
    HAND_IN_PROGRESS = "hand_in_progress"
    NO_HAND = "no_hand"
    NOT_YOUR_TURN = "not_your_turn"
    ILLEGAL_ACTION = "illegal_action"
    NOT_ENOUGH_PLAYERS = "not_enough_players"
    NO_VOTE_IN_PROGRESS = "no_vote_in_progress"
    NOT_A_VOTER = "not_a_voter"


@dataclass(frozen=True, slots=True)
class Err:
    code: ErrorCode
    detail: str
