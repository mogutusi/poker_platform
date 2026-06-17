from enum import StrEnum


class RoomStatus(StrEnum):
    PENDING_START = "pending_start"
    HAND_STARTED = "hand_started"


class HandStatus(StrEnum):
    PRE_FLOP = "pre_flop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    ENDING = "ending"

    @property
    def next_status(self) -> "HandStatus | None":
        chain = {
            HandStatus.PRE_FLOP: HandStatus.FLOP,
            HandStatus.FLOP: HandStatus.TURN,
            HandStatus.TURN: HandStatus.RIVER,
            HandStatus.RIVER: HandStatus.SHOWDOWN,
        }
        return chain.get(self)


class PlayerActionType(StrEnum):
    FOLD = "fold"
    BET = "bet"
    CHECK = "check"


class PlayerStatus(StrEnum):
    ACTIVE = "active"
    FOLDED = "folded"
    ALLIN = "allin"


class UserStatus(StrEnum):
    WATCHING = "watching"
    OFFLINE = "offline"
    SITTING_IN = "sitting_in"
    READY_TO_PLAY = "ready_to_play"
    SITTING_OUT = "sitting_out"
    PLAYING = "playing"

    def can_change_to(self, new_status: "UserStatus") -> bool:
        return (self, new_status) in USER_STATUS_TRANSITIONS

    def userself_can_change_to(self, new_status: "UserStatus") -> bool:
        return (self, new_status) in USER_STATUS_SELF_TRANSITIONS


USER_STATUS_TRANSITIONS: set[tuple[UserStatus, UserStatus]] = {
    # disconnect / reconnect
    (UserStatus.WATCHING, UserStatus.OFFLINE),
    (UserStatus.READY_TO_PLAY, UserStatus.OFFLINE),
    (UserStatus.PLAYING, UserStatus.OFFLINE),
    (UserStatus.SITTING_IN, UserStatus.OFFLINE),
    (UserStatus.SITTING_OUT, UserStatus.OFFLINE),
    (UserStatus.OFFLINE, UserStatus.WATCHING),
    (UserStatus.OFFLINE, UserStatus.READY_TO_PLAY),
    (UserStatus.OFFLINE, UserStatus.PLAYING),
    (UserStatus.OFFLINE, UserStatus.SITTING_IN),
    (UserStatus.OFFLINE, UserStatus.SITTING_OUT),
    # sit down
    (UserStatus.WATCHING, UserStatus.SITTING_IN),
    # stand up
    (UserStatus.READY_TO_PLAY, UserStatus.WATCHING),
    (UserStatus.SITTING_OUT, UserStatus.WATCHING),
    (UserStatus.SITTING_IN, UserStatus.WATCHING),
    # stay in the table
    (UserStatus.SITTING_IN, UserStatus.READY_TO_PLAY),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_IN),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_IN, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_OUT, UserStatus.SITTING_IN),
    # game flow
    (UserStatus.READY_TO_PLAY, UserStatus.PLAYING),
    (UserStatus.PLAYING, UserStatus.SITTING_IN),
    (UserStatus.PLAYING, UserStatus.SITTING_OUT),
}

USER_STATUS_SELF_TRANSITIONS: set[tuple[UserStatus, UserStatus]] = {
    (UserStatus.SITTING_IN, UserStatus.READY_TO_PLAY),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_IN),
    (UserStatus.SITTING_IN, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_IN, UserStatus.WATCHING),
    (UserStatus.PLAYING, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_OUT, UserStatus.SITTING_IN),
    (UserStatus.SITTING_OUT, UserStatus.WATCHING),
    (UserStatus.READY_TO_PLAY, UserStatus.WATCHING),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_OUT),
}
