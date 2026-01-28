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

    @property
    def next_status(self) -> Optional["HandStatus"]:
        translation_map = {
            HandStatus.READY_TO_START: HandStatus.PRE_FLOP,
            HandStatus.PRE_FLOP: HandStatus.FLOP,
            HandStatus.FLOP: HandStatus.TURN,
            HandStatus.TURN: HandStatus.RIVER,
            HandStatus.RIVER: HandStatus.SHOWDOWN,
        }
        return translation_map.get(self, None)

class PlayerActionType(StrEnum):
    FOLD = "fold"
    BET = "bet"
    CHECK = "check"

class CardSuit(StrEnum):
    HEARTS = "h"
    DIAMONDS = "d"
    CLUBS = "c"
    SPADES = "s"

class CardRank(StrEnum):
    TWO = "2"
    THREE = "3"
    FOUR = "4"
    FIVE = "5"
    SIX = "6"
    SEVEN = "7"
    EIGHT = "8"
    NINE = "9"
    TEN = "T"
    JACK = "J"
    QUEEN = "Q"
    KING = "K"
    ACE = "A"

class UserStatus(StrEnum):
    WATCHING = "watching"
    OFFLINE = "offline"
    SITTING_IN = "sitting_in"
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

USER_STATUS_SELF_TRANSITIONS: Set[Tuple[UserStatus, UserStatus]] = {
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


class PlayerStatus(StrEnum):
    ACTIVE = "active"
    FOLDED = "folded"
    ALLIN = "allin"

