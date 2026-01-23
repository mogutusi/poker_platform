from typing import List, Tuple, Optional, Dict
from datetime import datetime
from fastapi import WebSocket
from pydantic import BaseModel, Field, field_serializer

from app.pokertable.enums import HandStatus, CardSuit, CardRank, PlayerStatus, RoomStatus, PlayerActionType, UserStatus
from app.pokertable.gameconfig import gameconfig

class Card(BaseModel):
    suit: CardSuit
    rank: CardRank
    
class Player(BaseModel):
    nickname: str
    player_status: PlayerStatus = Field(default=PlayerStatus.ACTIVE)
    points: int
    hole_cards: Optional[Tuple[Card, Card]] = Field(default=None)
    # round total bet amount
    bet_amount: Optional[int] = Field(default=0,ge=0)
    seat_position: int

    @field_serializer("hole_cards", when_used="json")
    def hide_hole_cards(self, value):
        return None

class Hand(BaseModel):
    status: HandStatus
    # By default, the first position is the small blind bet and the second position is the large blind bet
    players: Optional[List[Player]]
    # players[acting_player_position] is the acting player
    acting_player_position: Optional[int] = Field(default=None)
    last_bet: Optional[int] = Field(default=None)
    deck: Optional[List[Card]]
    pot: int = Field(default=0,ge=0)
    start_time: Optional[datetime]
    flop_cards: Optional[Tuple[Card, Card, Card]]
    turn_card: Optional[Card]
    river_card: Optional[Card]


class Seat(BaseModel):
    nickname: str
    points: int
    in_game_points: int = Field(default=0,ge=0)


class Room(BaseModel):
    # user_nickname -> user_status
    users_in_room: Dict[str, UserStatus]
    seats: List[Optional[Seat]] = Field(default_factory=lambda: [None] * gameconfig.MAX_SEATS)
    hand: Optional[Hand] = Field(default=None)
    status: RoomStatus = Field(default=RoomStatus.PENDING_START)
    buy_in: int = Field(
        default=gameconfig.MIN_BUY_IN,
        ge=gameconfig.MIN_BUY_IN,
        le=gameconfig.MAX_BUY_IN
    )
    new_player_seat_list: List[int] = Field(default=[])
    button_position: int = Field(default=gameconfig.MAX_SEATS - 1)
    small_blind: int = Field(
        default=gameconfig.DEFAULT_SMALL_BLIND,
        ge=gameconfig.MIN_SMALL_BLIND,
        le=gameconfig.MAX_SMALL_BLIND
    )
    # user_nickname -> user_status
    # when the room is disconnected, the user_status will be saved to the snapshot
    disconnect_snapshot: Dict[str, UserStatus] = Field(default={})


class PlayerAction(BaseModel):
    user_nickname: str
    action: PlayerActionType
    bet_amount: Optional[int] = Field(default=None,ge=0)  # total bet amount

class UserAction(BaseModel):
    user_nickname: str
    action: PlayerAction
