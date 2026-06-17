from dataclasses import dataclass
from enum import StrEnum


class CardSuit(StrEnum):
    # 花色短码(treys 串用)
    HEARTS = "h"  # 红桃
    DIAMONDS = "d"  # 方块
    CLUBS = "c"  # 梅花
    SPADES = "s"  # 黑桃


class CardRank(StrEnum):
    # 点数短码(treys 串用):2..9 即点数自文档,T=10、J/Q/K/A 为缩写
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
    rank: CardRank  # 2..A
    suit: CardSuit  # h/d/c/s

    def to_treys(self) -> str:
        # treys 牌串形如 "<rank><suit>",如 "As"、"Th"
        return f"{self.rank.value}{self.suit.value}"
