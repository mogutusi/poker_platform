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
    WATCHING = "watching"
    OFFLINE = "offline"
    READY_TO_PLAY = "ready_to_play"
    SITTING_OUT = "sitting_out"
    PLAYING = "playing"
    
    def userself_can_change_to(self, new_status: "UserStatus") -> bool:
        return (self, new_status) in USER_STATUS_SELF_TRANSITIONS

    def can_change_to(self, new_status: "UserStatus") -> bool:
        return (self, new_status) in USER_STATUS_TRANSITIONS
    

USER_STATUS_TRANSITIONS: Set[Tuple[UserStatus, UserStatus]] = {
    # disconnect/reconnect
    (UserStatus.WATCHING, UserStatus.OFFLINE),
    (UserStatus.READY_TO_PLAY, UserStatus.OFFLINE),
    (UserStatus.PLAYING, UserStatus.OFFLINE),
    (UserStatus.SITTING_OUT, UserStatus.OFFLINE),
    (UserStatus.OFFLINE, UserStatus.WATCHING),   
    (UserStatus.OFFLINE, UserStatus.READY_TO_PLAY), 
    (UserStatus.OFFLINE, UserStatus.PLAYING),       
    (UserStatus.OFFLINE, UserStatus.SITTING_OUT),

    # sit down
    (UserStatus.WATCHING, UserStatus.READY_TO_PLAY),
    
    # stand up
    (UserStatus.READY_TO_PLAY, UserStatus.WATCHING),
    (UserStatus.SITTING_OUT, UserStatus.WATCHING),

    # stay in the table
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_OUT, UserStatus.READY_TO_PLAY),

    # game flow
    (UserStatus.READY_TO_PLAY, UserStatus.PLAYING),
    (UserStatus.PLAYING, UserStatus.READY_TO_PLAY),
    (UserStatus.PLAYING, UserStatus.SITTING_OUT), 
}

USER_STATUS_SELF_TRANSITIONS: Set[Tuple[UserStatus, UserStatus]] = {
    (UserStatus.PLAYING, UserStatus.SITTING_OUT),
    (UserStatus.SITTING_OUT, UserStatus.READY_TO_PLAY),
    (UserStatus.SITTING_OUT, UserStatus.WATCHING),
    (UserStatus.READY_TO_PLAY, UserStatus.WATCHING),
    (UserStatus.READY_TO_PLAY, UserStatus.SITTING_OUT),
}


class PlayerStatus(StrEnum):
    ACTIVE = "active"
    FOLDED = "folded"
    ALLIN = "allin"

