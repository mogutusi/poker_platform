from dataclasses import dataclass
from enum import StrEnum


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


@dataclass(frozen=True, slots=True)
class Card:
    rank: CardRank
    suit: CardSuit

    def to_treys(self) -> str:
        return f"{self.rank.value}{self.suit.value}"
