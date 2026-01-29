from re import T
from typing import List, Tuple, Optional, Dict
import random
from treys.lookup import LookupTable
import treys
from collections import defaultdict

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

def get_winner_pot(hand: Hand, notfold_player_list: List[Player], button_position: int, seat_size: int) -> Dict[str,int]:
    community_cards = [treys.Card.new(card.to_treys_card()) for card in [hand.flop_cards[0],hand.flop_cards[1],hand.flop_cards[2],hand.turn_card,hand.river_card]]
    rank = {}
    for player in notfold_player_list:
        hole_cards = [treys.Card.new(player.hole_cards[0].to_treys_card()),treys.Card.new(player.hole_cards[1].to_treys_card())]
        hand_rank = _evaluator.evaluate(hand=hole_cards,board=community_cards)
        rank[player.nickname] = hand_rank
    sorted_pot = sorted(hand.pots.items(), key=lambda x: x[1])
    sorted_pot_dict = {name: pot for name, pot in sorted_pot}
    sorted_pot = [t[0] for t in sorted_pot]
    winner_pot_list = []
    notfold_name_dict = {player.nickname: player for player in notfold_player_list}
    eligible_players: List[str] = [name for name in sorted_pot if name in notfold_name_dict.keys()]

    pot_pointer = 0
    while len(eligible_players) > 0:
        now_pot = 0
        now_player = []
        basic_chip = sorted_pot_dict[eligible_players[0]]
        while pot_pointer < len(sorted_pot) and sorted_pot_dict[sorted_pot[pot_pointer]] <= basic_chip:
            if sorted_pot[pot_pointer] in notfold_name_dict.keys():
                now_player.append(sorted_pot[pot_pointer])
            now_pot += sorted_pot_dict[sorted_pot[pot_pointer]]
            sorted_pot_dict[sorted_pot[pot_pointer]] = 0
            pot_pointer += 1
        temp_pointer = pot_pointer
        while temp_pointer < len(sorted_pot):
            now_pot += basic_chip
            sorted_pot_dict[sorted_pot[temp_pointer]] -= basic_chip
            temp_pointer += 1
        now_rank = [rank[player] for player in eligible_players]
        winner_list:List[str] = [eligible_players[i] for i,rank in enumerate(now_rank) if rank == min(now_rank)]
        winner_pot_list.append((winner_list, now_pot))
        eligible_players = [x for x in eligible_players if x not in set(now_player)]
    winner_pot: Dict[str, int] = defaultdict(int)
    for winner_list, now_pot in winner_pot_list:
        small_chip = now_pot % len(winner_list)
        lucky_guy = winner_list[0]
        distance = float('inf')
        for winner in winner_list:
            winner_pot[winner] += now_pot//len(winner_list)
            if (notfold_name_dict[winner].seat_position - button_position)%seat_size < distance:
                distance = (notfold_name_dict[winner].seat_position - button_position)%seat_size
                lucky_guy = winner
        winner_pot[lucky_guy] += small_chip

    return winner_pot
