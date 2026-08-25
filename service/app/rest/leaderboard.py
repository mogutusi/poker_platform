# REST 排行榜(rest.md §排行榜)。GET /leaderboard?limit=N → [LeaderboardEntry],按结算积分降序。
#
# 读 DB(结算后全局积分),不读 world——与 GET /lobby/rooms(读 world 头数)分属 rest.md 契约两侧(见 0048/0050)。
# points 是**桌下结算余额**:买进牌桌的筹码在 Seat.points(内存、不落库,storage.md),故全买进桌的人榜上只显桌下余额
# ——「含桌上筹码的总身家」需读 world,列为 future。请求级 session(查询内 async with)、读路径无行锁、比内存滞后(展示够用)。
# **走加密信封**(0094 收编):POST + `{sid, frame}`,内层参数 `{"limit"?: int}`,响应 `{"entries": [...]}`。
# 「解密即认证」⇒ 未登录者读不到排行榜——登录是唯一暴露在外的入口(auth.md §加密信道)。

import time
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.auth.session import SessionStore
from app.db.queries import top_users_by_points
from app.rest.secure import SecureRequest, SecureResponse, open_request, seal_response


class LeaderboardEntry(BaseModel):
    # REST DTO ≠ DB 行:rank 由服务端按降序序号赋(1 起),前端直接渲染。
    rank: int  # 名次(1 起;同分按 nickname 升序定序)
    nickname: str  # 显示名
    points: int  # 结算后全局积分(桌上筹码不计,见模块头)


def _read_limit(params: dict, default: int, maximum: int) -> int:
    # 内层参数的整数校验:信封已验过 ⇒ 这里的畸形是**客户端 bug 而非鉴权问题**,按 400 分层(rest.md 错误分层)。
    # 收编前这层由 FastAPI 的 Query(ge=, le=) 做;参数进了信封就得自己校,不能默默截断成合法值。
    raw = params.get("limit", default)
    if not isinstance(raw, int) or isinstance(raw, bool) or not (1 <= raw <= maximum):
        raise HTTPException(status_code=400, detail="bad request")
    return raw


def make_leaderboard_router(
    get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession] | None],
    session_store: SessionStore,
    now: Callable[[], float] = time.time,
) -> APIRouter:
    # 迟绑 sessionmaker getter(DevShell.setup 后才有;create_app 传 lambda: shell.sessionmaker;测试可注入 seeded)。
    router = APIRouter()

    @router.post("/leaderboard", response_model=SecureResponse)
    async def get_leaderboard(req: SecureRequest) -> SecureResponse:
        session, seq, params = open_request(session_store, req, now())  # 信封不过 → 统一 401
        limit = _read_limit(params, gameconfig.LEADERBOARD_DEFAULT_LIMIT, gameconfig.LEADERBOARD_MAX_LIMIT)
        sessionmaker = get_sessionmaker()
        assert sessionmaker is not None, "leaderboard 路由须在 DevShell.setup() 后挂载"
        rows = await top_users_by_points(sessionmaker, limit)
        entries = [LeaderboardEntry(rank=i + 1, nickname=nick, points=pts) for i, (nick, pts) in enumerate(rows)]
        return seal_response(session, seq, {"entries": [e.model_dump() for e in entries]})

    return router
