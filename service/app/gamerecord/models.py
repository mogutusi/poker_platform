from sqlmodel import Field, SQLModel, Column
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import BaseModel

from typing import Optional
from datetime import datetime

class record_players(SQLModel):
    nickname: str
    initial_points: int
    final_points: int

class GameRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    
    game_start_time: datetime = Field(nullable=False)
    game_end_time: datetime = Field(nullable=False)
    rounds_number: int = Field(nullable=False)
    game_players: list[record_players] = Field(default=[],sa_column=Column(JSONB))
    
class GameRecordRequest(BaseModel):
    itemperpage: int
    page: int

class GameRecordRead(BaseModel):
    id: int
    game_start_time: datetime
    game_end_time: datetime
    rounds_number: int
    game_players: list[record_players]

class GameRecordReadPagination(BaseModel):
    game_records: list[GameRecordRead]
    itemperpage: int
    page: int

