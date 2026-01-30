from sqlmodel import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func
from typing import List

from app.user.models import User
from app.handrecord.models import  HandRecordReadPagination, HandRecord, HandParticipants,HandParticipantsRead, PersonalHandRecordReadPagination, PersonalHandRecordRead
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
    statement = (
        select(
            User.nickname,
            HandParticipants.initial_points,
            HandParticipants.final_points
        )
        .join(HandParticipants.player)
        .where(HandParticipants.hand_id == hand_id)
    )
    result = await db.exec(statement)
    handdetail = result.all()
    handdetail_list: List[HandParticipantsRead] = [
        HandParticipantsRead(
            nickname=participant.player.nickname,
            initial_points=participant.initial_points,
            final_points=participant.final_points,
        )
        for participant in handdetail
    ]
    return handdetail_list

async def handrecord_get_personal_record(user_nickname: str,itemperpage: int, page: int, db: DBsession) -> PersonalHandRecordReadPagination:
    count_statement = (
        select(func.count())
        .select_from(HandParticipants)
        .join(User, HandParticipants.player_id == User.id)
        .where(User.nickname == user_nickname)
    )
    total_count = await db.scalar(count_statement)
    statement = (
        select(
            HandRecord.id.label("hand_id"),
            HandRecord.start_time,
            HandRecord.end_time,
            HandRecord.final_pot,
            HandParticipants.initial_points,
            HandParticipants.final_points
        )
        .join(HandRecord.participants)
        .join(HandParticipants.player)
        .where(User.nickname == user_nickname)
        .order_by(HandRecord.start_time.desc())
        .offset((page - 1) * itemperpage)
        .limit(itemperpage)
    )
    result = await db.exec(statement)
    handrecord = result.mappings().all()
    personalhandrecord_list: List[PersonalHandRecordRead] = [
        PersonalHandRecordRead(**row) for row in handrecord
    ]
    return PersonalHandRecordReadPagination(hand_records=personalhandrecord_list, itemperpage=itemperpage, page=page, total=total_count)