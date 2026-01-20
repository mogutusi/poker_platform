from typing import Literal, Union, Optional, List, Annotated
from pydantic import BaseModel, Field, Discriminator
from datetime import datetime

from app.pokertable.enums import UserStatus, HandStatus, RoomStatus, PlayerActionType, PlayerStatus
from app.pokertable.models import Card, Player, Hand, Room

# ============================================
# client -> server （ClientMessage）
# ============================================

class SitdownMessage(BaseModel):
    type: Literal["sit_down"] = "sit_down"
    seat_number: int

    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "sit_down",
                "seat_number": 0
            }
        }
    }

class BuyInMessage(BaseModel):
    type: Literal["buy_in"] = "buy_in"
    buy_in: int
    seat_number: int
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "buy_in",
                "buy_in": 64
            }
        }
    }

class SetUserStatusMessage(BaseModel):
    type: Literal["set_user_status"] = "set_user_status"
    user_status: UserStatus
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "set_user_status",
                "user_status": "ready_to_play"
            }
        }
    }

class SetSmallBlindMessage(BaseModel):
    type: Literal["set_small_blind"] = "set_small_blind"
    small_blind: int = Field(ge=1, le=10)
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "set_small_blind",
                "small_blind": 1
            }
        }
    }

class SetBuyInMessage(BaseModel):
    type: Literal["set_buy_in"] = "set_buy_in"
    buy_in: int = Field(ge=32, le=128)
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "set_buy_in",
                "buy_in": 64
            }
        }
    }


class StartHandMessage(BaseModel):
    type: Literal["start_hand"] = "start_hand"
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "start_hand"
            }
        }
    }


class PlayerActionMessage(BaseModel):
    type: Literal["player_action"] = "player_action"
    action: PlayerActionType
    bet_amount: Optional[int] = Field(default=None, ge=0)
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "type": "player_action",
                    "action": "fold"
                },
                {
                    "type": "player_action",
                    "action": "bet",
                    "bet_amount": 10
                },
                {
                    "type": "player_action",
                    "action": "check"
                }
            ]
        }
    }


class ChatMessage(BaseModel):
    """聊天消息"""
    type: Literal["chat"] = "chat"
    message: str = Field(max_length=500)
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "chat",
                "message": "Good game!"
            }
        }
    }


# 客户端消息联合类型（使用 Discriminated Union）
ClientMessage = Annotated[
    Union[
        SitdownMessage,
        SetUserStatusMessage,
        SetSmallBlindMessage,
        SetBuyInMessage,
        StartHandMessage,
        PlayerActionMessage,
        ChatMessage,
        BuyInMessage,
    ],
    Field(discriminator="type")
]


# ============================================
# server -> client （ServerMessage）
# ============================================

class UserSitdownMessage(BaseModel):
    type: Literal["user_sitdown"] = "user_sitdown"
    seat_number: int
    user_nickname: str
    timestamp: datetime = Field(default_factory=datetime.now)

    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "user_sitdown",
                "seat_number": 0,
                "user_nickname": "John",
                "timestamp": "2025-12-07T12:00:00"
            }
        }
    }

class PlayerBuyInMessage(BaseModel):
    type: Literal["player_buy_in"] = "player_buy_in"
    seat_number: int
    user_nickname: str
    buy_in: int
    timestamp: datetime = Field(default_factory=datetime.now)

    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "player_buy_in",
                "seat_number": 0,
                "user_nickname": "John",
                "buy_in": 64
            }
        }
    }


class UserOnlineMessage(BaseModel):
    
    type: Literal["user_online"] = "user_online"
    nickname: str
    user_status: UserStatus
    timestamp: datetime = Field(default_factory=datetime.now)


class UserOfflineMessage(BaseModel):
    type: Literal["user_offline"] = "user_offline"
    nickname: str
    timestamp: datetime = Field(default_factory=datetime.now)


class SmallBlindSetMessage(BaseModel):
    type: Literal["small_blind_set"] = "small_blind_set"
    set_by: str
    small_blind: int
    timestamp: datetime = Field(default_factory=datetime.now)

class BuyInSetMessage(BaseModel):
    type: Literal["buy_in_set"] = "buy_in_set"
    buy_in: int
    set_by: str
    timestamp: datetime = Field(default_factory=datetime.now)

class RoomStateMessage(BaseModel):
    type: Literal["room_state"] = "room_state"
    room: Room

class UserStatusChangedMessage(BaseModel):
    type: Literal["user_status_changed"] = "user_status_changed"
    user_status: UserStatus
    user_nickname: str
    timestamp: datetime = Field(default_factory=datetime.now)

class RoomStatusChangedMessage(BaseModel):
    type: Literal["room_status_changed"] = "room_status_changed"
    room_status: RoomStatus
    changed_by: str
    timestamp: datetime = Field(default_factory=datetime.now)

class HandStartedMessage(BaseModel):
    type: Literal["hand_started"] = "hand_started"
    hand : Hand
    timestamp: datetime = Field(default_factory=datetime.now)


class HoleCardsMessage(BaseModel):
    type: Literal["hole_cards"] = "hole_cards"
    cards: tuple[Card, Card]


class BettingRoundStartedMessage(BaseModel):
    type: Literal["betting_round_started"] = "betting_round_started"
    hand_status: HandStatus
    acting_player: str
    pot: int
    last_bet: Optional[int]


class PlayerActionBroadcast(BaseModel):
    """玩家操作广播"""
    type: Literal["player_action_broadcast"] = "player_action_broadcast"
    player: str
    action: PlayerActionType
    bet_amount: Optional[int]
    pot: int
    next_player: Optional[str]
    timestamp: datetime = Field(default_factory=datetime.now)


class HandStageChangedMessage(BaseModel):
    """手牌阶段变化（发公共牌）"""
    type: Literal["hand_stage_changed"] = "hand_stage_changed"
    hand_status: HandStatus
    community_cards: List[Card]
    pot: int
    next_player: str


class HandEndedMessage(BaseModel):
    """手牌结束"""
    type: Literal["hand_ended"] = "hand_ended"
    winners: List[str]
    pot_distribution: dict[str, int]
    final_board: List[Card]
    showdown_hands: Optional[dict[str, tuple[Card, Card]]]  


class ChatBroadcast(BaseModel):
    type: Literal["chat_broadcast"] = "chat_broadcast"
    sender: str
    message: str
    timestamp: datetime = Field(default_factory=datetime.now)


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    error_code: str
    message: str
    timestamp: datetime = Field(default_factory=datetime.now)
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "type": "error",
                "error_code": "NOT_YOUR_TURN",
                "message": "现在不是你的回合",
                "timestamp": "2025-12-07T12:00:00"
            }
        }
    }


# 服务器消息联合类型
ServerMessage = Annotated[
    Union[
        UserOnlineMessage,
        UserOfflineMessage,
        SmallBlindSetMessage,
        BuyInSetMessage,
        RoomStateMessage,
        RoomStatusChangedMessage,
        HandStartedMessage,
        HoleCardsMessage,
        BettingRoundStartedMessage,
        PlayerActionBroadcast,
        HandStageChangedMessage,
        HandEndedMessage,
        ChatBroadcast,
        ErrorMessage,
    ],
    Field(discriminator="type")
]


def parse_client_message(data: str) -> ClientMessage:
    """
    Args:
        data: JSON 
    Returns:
        Pydantic Object

    """
    import json
    from pydantic import TypeAdapter
    
    data_dict = json.loads(data)
    adapter = TypeAdapter(ClientMessage)
    return adapter.validate_python(data_dict)


def serialize_server_message(message: ServerMessage) -> str:
    """
    Returns:
        JSON 
    """
    return message.model_dump_json()

from dataclasses import dataclass

@dataclass
class BroadcastTarget:
    message: ServerMessage

@dataclass
class PersonalTarget:
    nickname: str
    message: ServerMessage

ServerResponse = Union[BroadcastTarget, PersonalTarget]