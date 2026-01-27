from typing import List, Tuple, Optional
import random

from app.pokertable.models import Player, Card, Room, PlayerAction, Hand
from app.pokertable.enums import CardSuit, CardRank, PlayerStatus, PlayerActionType, HandStatus
from app.pokertable.exceptions import GameLogicError


def deal_cards(players: List[Player]) -> List[Card]:
    cards = [Card(suit=suit, rank=rank) for suit in CardSuit for rank in CardRank]
    rng = random.SystemRandom()
    rng.shuffle(cards)
    for player in players:
        player.hole_cards = (cards.pop(),)
    for player in players:
        player.hole_cards = player.hole_cards + (cards.pop(),)
    players[0].player_status = PlayerStatus.ACTIVE
    return cards

# return True if the hand go to the next status
def get_next_player(hand: Hand, small_blind: int) -> Tuple[bool, int]:
    players = hand.players
    next_position = -1
    for i in range(len(players)):
        if players[(hand.acting_player_position + i) % len(players)].player_status == PlayerStatus.ACTIVE:
            next_position = (hand.acting_player_position + i) % len(players)
            break
    if next_position == -1:
        return True, -1
    if hand.last_bet == 0:
        first_player = -1
        for player in players:
            first_player+=1
            if player.player_status == PlayerStatus.ACTIVE:
                break
        if first_player == next_position:
            return True,first_player
        return False,next_position
    if players[next_position].bet_amount == hand.last_bet:
        ## BB preflop Optional action
        if hand.handstatus == HandStatus.PRE_FLOP and next_position==1:
            if 2 * small_blind == hand.last_bet:
                return False,next_position
        first_player = -1
        for player in players:
            first_player+=1
            if player.player_status == PlayerStatus.ACTIVE:
                break
        return True,first_player
    return False,next_position

def end_round(hand: Hand) -> bool:
    players = hand.players
    for player in players:
        if player.player_status == PlayerStatus.ACTIVE:
            return False
    return True

