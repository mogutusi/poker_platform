from sqlmodel import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func
from typing import List

from app.handrecord.models import  HandRecordReadPagination, HandRecord, HandParticipants,HandParticipantsRead, MyHandRecordReadPagination, MyHandRecordRead
from app.database.core import DBsession

async def handrecord_get(itemperpage: int, page: int, db: DBsession) -> HandRecordReadPagination:
    total_count = await db.scalar(
        select(func.count(HandRecord.id))
    )
    handrecord = await db.exec(
        select(HandRecord)
        .order_by(HandRecord.start_time.desc())
        .offset((page - 1) * itemperpage)
        .limit(itemperpage)
    )
    handrecord = handrecord.all()
    return HandRecordReadPagination(hand_records=handrecord, itemperpage=itemperpage, page=page, total=total_count)

async def handrecord_get_detail(hand_id: int, db: DBsession) -> List[HandParticipantsRead]:
    handdetail = await db.exec(
        select(HandParticipants)
        .options(selectinload(HandParticipants.player))
        .where(HandParticipants.hand_id == hand_id)
    )
    handdetail = handdetail.all()
    handdetail_list: List[HandParticipantsRead] = [
        HandParticipantsRead(
            nickname=participant.player.nickname,
            initial_points=participant.initial_points,
            final_points=participant.final_points,
        )
        for participant in handdetail
    ]
    return handdetail_list

async def handrecord_get_my_record(user_id: int,itemperpage: int, page: int, db: DBsession) -> MyHandRecordReadPagination:
    total_count = await db.scalar(
        select(func.count(HandParticipants.hand_id))
        .where(HandParticipants.player_id == user_id)
    )
    handrecord = await db.exec(
        select(HandParticipants,HandRecord)
        .join(HandRecord, HandParticipants.hand_id == HandRecord.id)
        .where(HandParticipants.player_id == user_id)
        .order_by(HandRecord.start_time.desc())
        .offset((page - 1) * itemperpage)
        .limit(itemperpage)
    )
    handrecord = handrecord.all()
    myhandrecord_list: List[MyHandRecordRead] = [
        MyHandRecordRead(
            hand_id=hand.id,
            start_time=hand.start_time,
            end_time=hand.end_time,
            final_pot=hand.final_pot,
            initial_points=participant.initial_points,
            final_points=participant.final_points,
        )
        for participant, hand in handrecord
    ]
    return MyHandRecordReadPagination(hand_records=myhandrecord_list, itemperpage=itemperpage, page=page, total=total_count)