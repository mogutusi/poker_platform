from enum import StrEnum, IntEnum
from typing import Tuple, Optional, Set

class RoomStatus(StrEnum):
    PENDING_START = "pending_start"
    HAND_STARTED = "hand_started"
    HAND_ENDED = "hand_ended"

class HandStatus(StrEnum):
    READY_TO_START = "ready_to_start"
    PRE_FLOP = "pre_flop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    ENDING = "ending"

    # _HAND_STATUS_SEQUENCE: Tuple["HandStatus"] = ( PRE_FLOP, FLOP, TURN, RIVER, SHOWDOWN)

    @property
    def next_status(self) -> Optional["HandStatus"]:
        if self not in self._HAND_STATUS_SEQUENCE:
            return None
        if self == self._HAND_STATUS_SEQUENCE[-1]:
            return HandStatus.ENDING
        return self._HAND_STATUS_SEQUENCE[self._HAND_STATUS_SEQUENCE.index(self) + 1]

class PlayerActionType(StrEnum):
    FOLD = "fold"
    BET = "bet"
    CHECK = "check"

class CardSuit(StrEnum):
    HEARTS = "hearts"
    DIAMONDS = "diamonds"
    CLUBS = "clubs"
    SPADES = "spades"

class CardRank(IntEnum):
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13
    ACE = 14

class UserStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    READY_TO_PLAY = "ready_to_play"
    PLAYING = "playing"
    READY_TO_WATCH = "ready_to_watch"
    WATCHING = "watching"

    def can_change_to(self, new_status: "UserStatus") -> bool:
        return (self, new_status) in USER_STATUS_TRANSITIONS
    

USER_STATUS_TRANSITIONS: Set[Tuple[UserStatus, UserStatus]] = {
    (UserStatus.ONLINE, UserStatus.OFFLINE),
    (UserStatus.ONLINE, UserStatus.READY_TO_PLAY),
    (UserStatus.ONLINE, UserStatus.READY_TO_WATCH),
    (UserStatus.OFFLINE, UserStatus.ONLINE),
    (UserStatus.READY_TO_PLAY, UserStatus.PLAYING),
    (UserStatus.READY_TO_PLAY, UserStatus.READY_TO_WATCH),
    (UserStatus.READY_TO_WATCH, UserStatus.WATCHING),
    (UserStatus.READY_TO_WATCH, UserStatus.READY_TO_PLAY),
    (UserStatus.PLAYING, UserStatus.READY_TO_PLAY),
    (UserStatus.PLAYING, UserStatus.WATCHING),
    (UserStatus.WATCHING, UserStatus.READY_TO_PLAY),
    (UserStatus.WATCHING, UserStatus.PLAYING),
}

class PlayerStatus(StrEnum):
    ACTIVE = "active"
    FOLDED = "folded"
    ALLIN = "allin"

