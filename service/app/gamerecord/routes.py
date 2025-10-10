from fastapi import APIRouter

from app.database.core import DBsession
from app.gamerecord.models import GameRecordRequest, GameRecordReadPagination
from app.gamerecord.services import gamerecord_get

gamerecord_route = APIRouter(prefix="/gamerecord",tags=["gamerecord"])

@gamerecord_route.get("/get")
async def get(request: GameRecordRequest, db: DBsession) -> GameRecordReadPagination:
    gamerecordpagination = await gamerecord_get(request, db)
    return gamerecordpagination

