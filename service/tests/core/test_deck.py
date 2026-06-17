"""P1:deck —— 整副牌、洗牌是排列、treys evaluate 越小越强。"""

from app.core import deck
from app.core.cards import Card, CardRank, CardSuit


def test_full_deck_is_52_unique():
    assert len(deck.FULL_DECK) == 52
    assert len(set(deck.FULL_DECK)) == 52


def test_shuffled_deck_is_a_permutation():
    shuffled = deck.shuffled_deck()
    assert len(shuffled) == 52
    assert set(shuffled) == set(deck.FULL_DECK)
    # 不原地改 FULL_DECK
    assert len(deck.FULL_DECK) == 52


def test_evaluate_lower_is_stronger():
    board = [
        Card(CardRank.TWO, CardSuit.HEARTS),
        Card(CardRank.SEVEN, CardSuit.DIAMONDS),
        Card(CardRank.NINE, CardSuit.SPADES),
        Card(CardRank.JACK, CardSuit.CLUBS),
        Card(CardRank.KING, CardSuit.DIAMONDS),
    ]
    pair_aces = (Card(CardRank.ACE, CardSuit.HEARTS), Card(CardRank.ACE, CardSuit.DIAMONDS))
    king_high = (Card(CardRank.THREE, CardSuit.CLUBS), Card(CardRank.FOUR, CardSuit.SPADES))
    # 一对 A 强于 K-high ⇒ 分数更小
    assert deck.evaluate(board, pair_aces) < deck.evaluate(board, king_high)
