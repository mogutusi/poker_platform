from typing import List, Tuple, Optional, Set
from datetime import datetime
from fastapi import WebSocket
from pydantic import BaseModel, Field

from app.pokertable.enum import RoundStatus, CardSuit, CardRank, UserStatus, PlayerStatus
from app.gamerecord.models import record_players

class Card(BaseModel):
    suit: CardSuit
    rank: CardRank

class UserInRoom(BaseModel):
    user_status: UserStatus = Field(default=UserStatus.ONLINE)
    websocket: WebSocket = Field(exclude=True)

    model_config = {"arbitrary_types_allowed": True}

class Player(BaseModel):
    nickname: str
    player_status: PlayerStatus = Field(default=PlayerStatus.ACTIVE)
    points: int
    hole_cards: Optional[Tuple[Card, Card]] = Field(default=None)

class Round(BaseModel):
    status: RoundStatus = Field(default=RoundStatus.PENDING_START)
    # By default, the first position is the small blind bet and the second position is the large blind bet
    players: Optional[List[Player]]
    last_blind: List[str]
    round_start_time: Optional[datetime]
    flop_cards: Optional[Tuple[Card, Card, Card]]
    turn_card: Optional[Card]
    river_card: Optional[Card]
    
class RoomRecord(BaseModel):
    game_start_time: Optional[datetime] = Field(default=None)
    game_end_time: Optional[datetime] = Field(default=None)
    rounds_number: int = Field(default=0)
    players_set: Set[str] = Field(default=set())
    game_players: List[record_players] = Field(default=[])