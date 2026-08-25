# REST 手牌历史(rest.md §手牌历史)。GET /hands?user=&limit=&before= → [HandRecordView],新→旧游标分页。
#
# 读 DB 的手牌记录(delayDB 事件写追加);**隐私内建**:HandRecord/HandParticipant 只存结果(uid + 初/末筹码 + 池额),
# hole_cards/deck 从不落库(core.md 不变量 3 / models.py)——历史看输赢、看不到底牌。游标 = HandRecord.id(单调唯一);
# user 过滤按参与者;room 过滤按 HandRecord.room 列(0052 加列,健壮免 dedupe_key LIKE)。
# **走加密信封**(0094 收编):POST + `{sid, frame}`,内层参数 `{room?, user?, before?, limit?}`,响应 `{"hands": [...]}`。
# 「解密即认证」⇒ 未登录者读不到任何人的手牌流水——登录是唯一暴露在外的入口(auth.md §加密信道)。
# **授权范围仍是「登录用户可查任何人」**(`user=` 点名照旧),那是与传输无关的另一个决定,待定,见 rest.md。

import time
from datetime import datetime
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.auth.session import SessionStore
from app.db.queries import list_hands, load_user_by_nick
from app.rest.secure import SecureRequest, SecureResponse, open_request, seal_response


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


def _opt_str(params: dict, key: str) -> str | None:
    # 内层参数校验:信封已验过 ⇒ 畸形是客户端 bug、非鉴权问题,按 400 分层(收编前这层由 FastAPI Query 做)。
    value = params.get(key)
    if value is None or isinstance(value, str):
        return value
    raise HTTPException(status_code=400, detail="bad request")


def _opt_positive_int(params: dict, key: str, *, maximum: int | None = None) -> int | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise HTTPException(status_code=400, detail="bad request")
    if maximum is not None and value > maximum:
        raise HTTPException(status_code=400, detail="bad request")
    return value


def make_hands_router(
    get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession] | None],
    session_store: SessionStore,
    now: Callable[[], float] = time.time,
) -> APIRouter:
    # 迟绑 sessionmaker getter(DevShell.setup 后才有;create_app 传 lambda: shell.sessionmaker;测试可注入 seeded)。
    router = APIRouter()

    @router.post("/hands", response_model=SecureResponse)
    async def get_hands(req: SecureRequest) -> SecureResponse:
        session, seq, params = open_request(session_store, req, now())  # 信封不过 → 统一 401
        room = _opt_str(params, "room")  # 按房名过滤(精确匹配 HandRecord.room 列)
        user = _opt_str(params, "user")  # 按昵称过滤:只返回该玩家参与过的手(仍含对手)
        before = _opt_positive_int(params, "before")  # 游标:只取 id < before(下一页)
        limit = _opt_positive_int(params, "limit", maximum=gameconfig.HANDS_MAX_LIMIT)
        if limit is None:
            limit = gameconfig.HANDS_DEFAULT_LIMIT
        sessionmaker = get_sessionmaker()
        assert sessionmaker is not None, "hands 路由须在 DevShell.setup() 后挂载"
        participant_uid: int | None = None
        if user is not None:
            row = await load_user_by_nick(sessionmaker, user)
            if row is None:
                return seal_response(session, seq, {"hands": []})  # 无此用户 = 无手牌历史
            participant_uid = row[0]
        rows = await list_hands(
            sessionmaker, room=room, participant_uid=participant_uid, before_id=before, limit=limit
        )
        views = [
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
        # mode="json" 让 datetime 变 ISO 串:信封内层是 json.dumps,原样丢 datetime 会 TypeError。
        return seal_response(session, seq, {"hands": [v.model_dump(mode="json") for v in views]})

    return router
