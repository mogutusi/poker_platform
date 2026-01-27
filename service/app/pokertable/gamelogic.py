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

# return False if the hand go to the next status
def get_next_player(hand: Hand, small_blind: int) -> bool:
    players = hand.players
    next_position = (hand.acting_player_position + 1) % len(hand.players)
    while players[next_position].player_status != PlayerStatus.ACTIVE:
        next_position = (next_position + 1) % len(players)
    if last_bet is None:
        first_player = -1
        for player in players:
            first_player+=1
            if player.player_status == PlayerStatus.ACTIVE:
                break
        if first_player == next_position:
            return True,first_player
        return False,next_position
    if players[next_position].bet_amount == last_bet:
        if handstatus == HandStatus.PRE_FLOP and next_position==1:
            if 2 * small_blind == last_bet:
                return False,next_position
        first_player = -1
        for player in players:
            first_player+=1
            if player.player_status == PlayerStatus.ACTIVE:
                break
        return True,first_player
    return False,next_position

def end_hand(room: Room) -> bool:
    active_players = 0
    allin_players = 0
    for player in room.hand.players:
        if player.player_status == PlayerStatus.ACTIVE:
            active_players += 1
        if player.player_status == PlayerStatus.ALLIN:
            allin_players += 1
    if active_players == 1 and allin_players == 0:
        return True
    if active_players == 0:
        return True
    return False

