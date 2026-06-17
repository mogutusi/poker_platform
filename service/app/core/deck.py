import random

from treys import Card as TreysCard
from treys import Evaluator

from app.core.cards import Card, CardRank, CardSuit

# 整副 52 张(花色 × 点数);洗牌从它复制,不原地改
FULL_DECK: list[Card] = [Card(rank, suit) for suit in CardSuit for rank in CardRank]

_RNG = random.SystemRandom()  # 密码学随机;不变量 1 允许的本地非阻塞计算
_EVALUATOR = Evaluator()  # treys 单例;evaluate 是 O(1) 纯计算


def shuffled_deck() -> list[Card]:
    # 返回洗好的新牌堆;StartHand 不带 deck 时用它,带 deck(重放/测试)时不调
    deck = list(FULL_DECK)
    _RNG.shuffle(deck)
    return deck


def evaluate(board: list[Card], hole: tuple[Card, Card]) -> int:
    # treys 牌力分:**越小越强**;board 5 张 + hole 2 张(摊牌时补齐 board 再调)
    treys_board = [TreysCard.new(c.to_treys()) for c in board]
    treys_hole = [TreysCard.new(c.to_treys()) for c in hole]
    return _EVALUATOR.evaluate(treys_board, treys_hole)
