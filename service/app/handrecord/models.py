from sqlmodel import Field, SQLModel, Relationship
#from sqlalchemy.dialects.postgresql import JSONB
from pydantic import BaseModel

from typing import Optional, List
from datetime import datetime

class HandRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    
    start_time: datetime = Field(default_factory=datetime.now,nullable=False)
    end_time: datetime = Field(nullable=False)
    
    final_pot: int = Field(nullable=False)
    
    #Relationship
    participants: List["HandParticipants"] = Relationship(
        back_populates="hand",
        sa_relationship_kwargs={"foreign_keys":"HandParticipants.hand_id"}
    )


class HandParticipants(SQLModel,table=True):
    hand_id: int = Field(nullable=False,primary_key=True,foreign_key="handrecord.id")
    player_id: int = Field(nullable=False,primary_key=True,foreign_key="user.id")
    initial_points: int = Field(nullable=False)
    final_points: int = Field(nullable=False)


    #Relationship
    hand: "HandRecord" = Relationship(
        back_populates="participants",
        sa_relationship_kwargs={"foreign_keys":"HandParticipants.hand_id"}
    )
    player: "User" = Relationship(
        back_populates="hand_participants",
        sa_relationship_kwargs={"foreign_keys":"HandParticipants.player_id"}
    )


class HandParticipantsRead(BaseModel):
    nickname: str
    initial_points: int
    final_points: int


class HandRecordRead(BaseModel):
    id: int
    start_time: datetime
    end_time: datetime
    final_pot: int

class HandRecordReadPagination(BaseModel):
    hand_records: list[HandRecordRead]
    itemperpage: int
    page: int
    total: int

class PersonalHandRecordRequest(BaseModel):
    user_nickname: str
    itemperpage: int
    page: int

class PersonalHandRecordRead(BaseModel):
    hand_id: int
    start_time: datetime
    end_time: datetime
    final_pot: int
    initial_points: int
    final_points: int

class PersonalHandRecordReadPagination(BaseModel):
    hand_records: list[PersonalHandRecordRead]
    itemperpage: int
    page: int
    total: int