from enum import StrEnum, IntEnum

class RoundStatus(StrEnum):
    PENDING_START = "pending_start"
    ROUND_STARTED = "round_started"
    ROUND_ENDED = "round_ended"

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

class PlayerStatus(StrEnum):
    ACTIVE = "active"
    FOLDED = "folded"
    ALLIN = "allin"
