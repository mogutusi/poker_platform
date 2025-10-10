from sqlmodel import Field, SQLModel, Column
from sqlalchemy.dialects.postgresql import JSONB

from typing import Optional
from datetime import datetime

class players(SQLModel):
    name: str = Field(nullable=False)
    nickname: str = Field(nullable=False)
    points: int = Field(nullable=False)
    points_change: int = Field(nullable=False)

class GameRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    
    game_start_time: datetime = Field(nullable=False)
    game_end_time: datetime = Field(nullable=False)
    rounds_number: int = Field(nullable=False)
    game_players: list[players] = Field(default=[],sa_column=Column(JSONB))
    
class GameRecordRequest(SQLModel):
    itemperpage: int
    page: int

class GameRecordRead(SQLModel):
    id: int
    game_start_time: datetime
    game_end_time: datetime
    rounds_number: int
    game_players: list[players]

class GameRecordReadPagination(SQLModel):
    game_records: list[GameRecordRead]
    itemperpage: int
    page: int

