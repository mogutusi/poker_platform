from typing import List, Tuple, Optional, Dict
from datetime import datetime
from fastapi import WebSocket
from pydantic import BaseModel, Field, field_serializer

from app.pokertable.enums import HandStatus, CardSuit, CardRank, UserStatus, PlayerStatus, RoomStatus, PlayerActionType
from app.pokertable.gameconfig import gameconfig

class Card(BaseModel):
    suit: CardSuit
    rank: CardRank

class UserInRoom(BaseModel):
    user_status: UserStatus = Field(default=UserStatus.ONLINE)
    
class Player(BaseModel):
    nickname: str
    player_status: PlayerStatus = Field(default=PlayerStatus.ACTIVE)
    points: int
    hole_cards: Optional[Tuple[Card, Card]] = Field(default=None)
    bet_amount: Optional[int] = Field(default=0,ge=0)

    @field_serializer("hole_cards", when_used="json")
    def hide_hole_cards(self, value):
        return None

class Hand(BaseModel):
    status: HandStatus
    # By default, the first position is the small blind bet and the second position is the large blind bet
    players: Optional[List[Player]]
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
    reload_points: int = Field(default=0)

class Room(BaseModel):
    # user_nickname -> users_in_room
    users_in_room: Dict[str, UserInRoom]
    seats: List[Optional[Seat]] = Field(default_factory=lambda: [None] * gameconfig.MAX_SEATS)
    hand: Optional[Hand] = Field(default=None)
    status: RoomStatus = Field(default=RoomStatus.PENDING_START)
    buy_in: int = Field(default=64,ge=32,le=128)
    last_small_blind_position: int = Field(default=0)
    small_blind: int = Field(default=1,ge=1,le=3)


class PlayerAction(BaseModel):
    user_nickname: str
    action: PlayerActionType
    bet_amount: Optional[int] = Field(default=None,ge=0)  # total bet amount

class UserAction(BaseModel):
    user_nickname: str
    action: PlayerAction
