# REST 手牌历史(rest.md §手牌历史)。GET /hands?user=&limit=&before= → [HandRecordView],新→旧游标分页。
#
# 读 DB 的手牌记录(delayDB 事件写追加);**隐私内建**:HandRecord/HandParticipant 只存结果(uid + 初/末筹码 + 池额),
# hole_cards/deck 从不落库(core.md 不变量 3 / models.py)——历史看输赢、看不到底牌。游标 = HandRecord.id(单调唯一);
# user 过滤按参与者;room 过滤按 HandRecord.room 列(0052 加列,健壮免 dedupe_key LIKE)。
# dev 明文无鉴权(P5 上 JWT 时按 rest.md「REST 走 JWT」补,可要求仅查自己)。

from datetime import datetime
from typing import Callable

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.db.queries import list_hands, load_user_by_nick


class HandParticipantView(BaseModel):
    nickname: str
    initial_points: int  # 开局锁入本手的筹码
    final_points: int  # 结算后还回座位的筹码
    net: int  # 本手盈亏 = final_points - initial_points(便利派生)


class HandRecordView(BaseModel):
    id: int  # 手牌记录主键;兼作下一页游标(下一页传 before=本页最后一条的 id)
    dedupe_key: str  # "room:seq" 手牌标识
    start_time: datetime  # 开局墙钟
    end_time: datetime  # 手结束墙钟
    final_pot: int  # 各子池金额之和(不含退还的未叫注)
    participants: list[HandParticipantView]  # 全部参与者(按 nickname 升序)


def make_hands_router(get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession] | None]) -> APIRouter:
    # 迟绑 sessionmaker getter(DevShell.setup 后才有;create_app 传 lambda: shell.sessionmaker;测试可注入 seeded)。
    router = APIRouter()

    @router.get("/hands", response_model=list[HandRecordView])
    async def get_hands(
        room: str | None = Query(default=None),  # 按房名过滤(精确匹配 HandRecord.room 列)
        user: str | None = Query(default=None),  # 按昵称过滤:只返回该玩家参与过的手(仍含对手)
        before: int | None = Query(default=None, ge=1),  # 游标:只取 id < before(下一页)
        limit: int = Query(default=gameconfig.HANDS_DEFAULT_LIMIT, ge=1, le=gameconfig.HANDS_MAX_LIMIT),
    ) -> list[HandRecordView]:
        sessionmaker = get_sessionmaker()
        assert sessionmaker is not None, "hands 路由须在 DevShell.setup() 后挂载"
        participant_uid: int | None = None
        if user is not None:
            row = await load_user_by_nick(sessionmaker, user)
            if row is None:
                return []  # 无此用户 = 无手牌历史
            participant_uid = row[0]
        rows = await list_hands(
            sessionmaker, room=room, participant_uid=participant_uid, before_id=before, limit=limit
        )
        return [
            HandRecordView(
                id=hid,
                dedupe_key=dk,
                start_time=st,
                end_time=et,
                final_pot=pot,
                participants=[
                    HandParticipantView(nickname=n, initial_points=i, final_points=f, net=f - i)
                    for n, i, f in ps
                ],
            )
            for hid, dk, st, et, pot, ps in rows
        ]

    return router
