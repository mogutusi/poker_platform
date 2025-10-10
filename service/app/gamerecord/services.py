from sqlmodel import select

from app.gamerecord.models import  GameRecordRequest, GameRecordReadPagination, GameRecord
from app.database.core import DBsession

async def gamerecord_get(request: GameRecordRequest, db: DBsession) -> GameRecordReadPagination:
    gamerecord = await db.exec(select(GameRecord).offset((request.page - 1) * request.itemperpage).limit(request.itemperpage).order_by(GameRecord.game_start_time.desc()))
    gamerecord = gamerecord.all()
    return GameRecordReadPagination(game_records=gamerecord, itemperpage=request.itemperpage, page=request.page)

