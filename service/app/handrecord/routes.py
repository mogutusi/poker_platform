from fastapi import APIRouter, HTTPException
from typing import List

from app.database.core import DBsession
from app.handrecord.models import HandRecordReadPagination, HandParticipantsRead, PersonalHandRecordReadPagination, PersonalHandRecordRequest
from app.handrecord.services import handrecord_get, handrecord_get_detail, handrecord_get_personal_record

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

@handrecord_route.post("/personalrecords",response_model=PersonalHandRecordReadPagination)
async def get_personal_record(request: PersonalHandRecordRequest, db: DBsession) -> PersonalHandRecordReadPagination:
    if (not ( 100 >= request.itemperpage >= 1)) or request.page < 1:
        raise HTTPException(status_code=400, detail="Invalid itemperpage or page")
    handrecordpagination = await handrecord_get_personal_record(request.user_nickname,request.itemperpage, request.page, db)
    return handrecordpagination