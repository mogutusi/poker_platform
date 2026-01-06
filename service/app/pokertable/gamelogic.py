from typing import List, Tuple, Optional
import random

from app.pokertable.models import Player, Card, Room, PlayerAction
from app.pokertable.enums import CardSuit, CardRank, PlayerStatus, PlayerActionType, HandStatus
from app.pokertable.exceptions import GameLogicError


def get_blind(players: List[Player], last_blind: List[str]) -> Tuple[List[Player], List[str]]:
    if len(last_blind) == 0:
        return players, [player.nickname for player in players]
    last_small_blind = last_blind[0]
    temp_players = []
    for player in players:
        temp_players.append(player.nickname)
    now_players = [x for x in last_blind if x in temp_players]
    now_players.extend([x for x in temp_players if x not in last_blind])
    if last_small_blind in now_players:
        temp_player = now_players[0]
        now_players = now_players[1:]
        now_players.append(temp_player)
    sorted_players = sorted(
        players,
        key=lambda p: now_players.index(p.nickname) if p.nickname in now_players else len(now_players)
    )
    return sorted_players, now_players


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

def get_next_player(players: List[Player], position: int, last_bet: Optional[int],handstatus: HandStatus,small_blind:int) -> Tuple[bool, int]:
    next_position = (position + 1) % len(players)
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

async def do_action(player_action: PlayerAction, room: Room):
    message_list = []
    position = room.hand.acting_player_position
    if player_action.user_nickname != room.hand.players[position].nickname:
        raise GameLogicError(message="Not your turn")
    now_player = room.hand.players[position]
    match player_action.action:
        case PlayerActionType.FOLD:
            room.hand.players[position].player_status = PlayerStatus.FOLDED
            go_next,room.hand.acting_player_position = get_next_player(
                players=room.hand.players, 
                position=position, 
                last_bet=room.hand.last_bet,
                handstatus=room.hand.status,
                small_blind=room.small_blind
            )
            message_list.append({"type": "player_action", "next_player": f"{room.hand.players[room.hand.acting_player_position].nickname}", "action": player_action})
            if go_next:
                room.hand.status = room.hand.status.next_status
                message_list.append({"type": "hand_status_changed", "status": room.hand.status})
                # Update hand status
                room.hand.last_bet = None
            return message_list
        case PlayerActionType.BET:
            if now_player.player_status != PlayerStatus.ACTIVE:
                raise GameLogicError(message="Player is not active")
            if player_action.bet_amount > now_player.points:
                raise GameLogicError(message="Player has not enough points to cover bet")
            if player_action.bet_amount < room.hand.last_bet:
                raise GameLogicError(message="Bet amount is less than last bet")
            now_player.points -= player_action.bet_amount
            if now_player.points == 0:
                now_player.player_status = PlayerStatus.ALLIN
            room.hand.last_bet = player_action.bet_amount
            now_player.bet_amount = player_action.bet_amount
            room.hand.pot += now_player.bet_amount
            go_next, room.hand.acting_player_position = get_next_player(
                players=room.hand.players, 
                position=position, 
                last_bet=room.hand.last_bet,
                handstatus=room.hand.status,
                small_blind=room.small_blind
            )
            message_list.append({"type": "player_action", "next_player": f"{room.hand.players[room.hand.acting_player_position].nickname}", "action": player_action})
            if go_next:
                room.hand.status = room.hand.status.next_status
                message_list.append({"type": "hand_status_changed", "status": room.hand.status})
                room.hand.last_bet = None
            return message_list
        case PlayerActionType.CHECK:
            pass
            