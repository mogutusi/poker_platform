"""扑克牌的纯数据表示。

`Card` 是不可变值对象(冻结 dataclass),可作 dict 键 / set 成员。
洗牌(SystemRandom)与牌力评估(treys Evaluator)是 P1 的 core/deck.py;
这里只放牌本身与到 treys 字符串的转换(纯计算,不变量 1 允许)。
"""

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
        """treys 用 '<rank><suit>' 形式,如 'As'、'Th'。"""
        return f"{self.rank.value}{self.suit.value}"
