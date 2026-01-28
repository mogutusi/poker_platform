from typing import List, Tuple, Optional
import random
from treys.lookup import LookupTable
import treys

from app.pokertable.models import Player, Card, Room, PlayerAction, Hand
from app.pokertable.enums import CardSuit, CardRank, PlayerStatus, PlayerActionType, HandStatus
from app.pokertable.exceptions import GameLogicError
from app.pokertable.wsm_schemas import PlayerActionMessage

_evaluator = treys.Evaluator()

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

def do_action(message: PlayerActionMessage, hand: Hand, player: Player) -> None:
    match message.action:
        case PlayerActionType.FOLD:
            player.player_status = PlayerStatus.FOLDED
            
        case PlayerActionType.BET:
            bet_amount = message.bet_amount
            if bet_amount is None:
                raise GameLogicError(message="Bet amount is required")
            if bet_amount > player.points + player.bet_amount:
                raise GameLogicError(message="Bet amount is greater than the player's points")
            if bet_amount < hand.last_bet:
                if bet_amount == player.points + player.bet_amount:
                    player.player_status = PlayerStatus.ALLIN
                    player.points = 0
                    player.bet_amount = bet_amount
                else:
                    raise GameLogicError(message="Bet amount is less than the last bet")
            else:
                active_player_num = 0
                for player in hand.players:
                    if player.player_status == PlayerStatus.ACTIVE:
                        active_player_num += 1
                if active_player_num == 1:
                    raise GameLogicError(message="Only you can bet, it is meaningless to bet")
                player.points = player.points - bet_amount + player.bet_amount
                player.bet_amount = bet_amount
                hand.last_bet = bet_amount
                if player.points == 0:
                    player.player_status = PlayerStatus.ALLIN
        case PlayerActionType.CHECK:
            if hand.last_bet is not None and hand.last_bet > 0:
                raise GameLogicError(message="Cannot check after a bet")

def get_winner(hand: Hand,active_player: Optional[Player],notfold_player_list: list[Player]) -> List[Tuple[str,int]]:
    if len(notfold_player_list) == 0:
        return [active_player.nickname]
    if hand.flop_cards is None or hand.turn_card is None or hand.river_card is None:
        raise GameLogicError(message="the hand is not ended")
    community_cards = [card.to_treys_card() for card in [hand.flop_cards[0],hand.flop_cards[1],hand.flop_cards[2],hand.turn_card,hand.river_card]]
    hole_cards = []
    for player in notfold_player_list:
        temp_hole_cards = []
        temp_hole_cards.append(player.hole_cards[0].to_treys_card())
        temp_hole_cards.append(player.hole_cards[1].to_treys_card())
        hole_cards.append(temp_hole_cards)
    winner_list = []
    for i,hole_card in enumerate(hole_cards):
        hand_rank = _evaluator.evaluate(hand=hole_card,board=community_cards)
        winner_list.append((notfold_player_list[i].nickname,hand_rank))
    winner_list.sort(key=lambda x: x[1])
    return winner_list[0][0]