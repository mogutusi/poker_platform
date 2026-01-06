from fastapi import APIRouter, HTTPException
from typing import List

from app.database.core import DBsession
from app.handrecord.models import HandRecordReadPagination, HandParticipantsRead, MyHandRecordReadPagination
from app.handrecord.services import handrecord_get, handrecord_get_detail, handrecord_get_my_record

handrecord_route = APIRouter(prefix="/handrecord",tags=["handrecord"])

@handrecord_route.get("/records",response_model=HandRecordReadPagination)
async def getrecords(itemperpage: int, page: int, db: DBsession) -> HandRecordReadPagination:
    if (not ( 100 >= itemperpage >= 1)) or page < 1:
        raise HTTPException(status_code=400, detail="Invalid itemperpage or page")
    handrecordpagination = await handrecord_get(itemperpage,page, db)
    return handrecordpagination

@handrecord_route.get("/detail/{hand_id}",response_model=List[HandParticipantsRead])
async def get_detail(hand_id: int, db: DBsession) -> List[HandParticipantsRead]:
    handrecordpagination = await handrecord_get_detail(hand_id, db)
    return handrecordpagination

# @handrecord_route.get("/myrecords",response_model=MyHandRecordReadPagination)
# async def get_my_record(user_id: int,itemperpage: int, page: int, db: DBsession) -> MyHandRecordReadPagination:
#     if (not ( 100 >= itemperpage >= 1)) or page < 1:
#         raise HTTPException(status_code=400, detail="Invalid itemperpage or page")
#     handrecordpagination = await handrecord_get_my_record(user_id,itemperpage, page, db)
#     return handrecordpagination