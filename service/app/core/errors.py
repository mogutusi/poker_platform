"""错误是返回值,不是异常(硬规则 4 / error.md)。

reduce 与 core helper 一律返回 `Err | None`(或 `(value, Err)`),绝不 raise;
异常只留给真正的 bug,由 GameLoop 归一为 Err(INTERNAL)。
`detail` 必须带上下文(谁、哪个房间、什么状态),便于定位。
"""

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    # 通用 / 系统
    INTERNAL = "internal"
    INVALID_COMMAND = "invalid_command"
    # 房间 / 大厅
    ROOM_NOT_FOUND = "room_not_found"
    ROOM_FULL = "room_full"
    ALREADY_IN_ROOM = "already_in_room"
    NOT_IN_ROOM = "not_in_room"
    # 座位 / 状态
    SEAT_TAKEN = "seat_taken"
    SEAT_EMPTY = "seat_empty"
    NOT_YOUR_SEAT = "not_your_seat"
    INVALID_STATUS_TRANSITION = "invalid_status_transition"
    INSUFFICIENT_POINTS = "insufficient_points"
    # 手牌 / 行动
    HAND_IN_PROGRESS = "hand_in_progress"
    NO_HAND = "no_hand"
    NOT_YOUR_TURN = "not_your_turn"
    ILLEGAL_ACTION = "illegal_action"
    NOT_ENOUGH_PLAYERS = "not_enough_players"
    # 投票
    NO_VOTE_IN_PROGRESS = "no_vote_in_progress"
    NOT_A_VOTER = "not_a_voter"


@dataclass(frozen=True, slots=True)
class Err:
    code: ErrorCode
    detail: str
