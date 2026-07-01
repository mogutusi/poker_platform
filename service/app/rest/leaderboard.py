# REST 排行榜(rest.md §排行榜)。GET /leaderboard?limit=N → [LeaderboardEntry],按结算积分降序。
#
# 读 DB(结算后全局积分),不读 world——与 GET /lobby/rooms(读 world 头数)分属 rest.md 契约两侧(见 0048/0050)。
# points 是**桌下结算余额**:买进牌桌的筹码在 Seat.points(内存、不落库,storage.md),故全买进桌的人榜上只显桌下余额
# ——「含桌上筹码的总身家」需读 world,列为 future。请求级 session(查询内 async with)、读路径无行锁、比内存滞后(展示够用)。
# dev 明文无鉴权(排名公开、无隐私);P5 上 JWT 时按 rest.md「REST 走 JWT」补(排行榜可留公开)。

from typing import Callable

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.db.queries import top_users_by_points


class LeaderboardEntry(BaseModel):
    # REST DTO ≠ DB 行:rank 由服务端按降序序号赋(1 起),前端直接渲染。
    rank: int  # 名次(1 起;同分按 nickname 升序定序)
    nickname: str  # 显示名
    points: int  # 结算后全局积分(桌上筹码不计,见模块头)


def make_leaderboard_router(get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession] | None]) -> APIRouter:
    # 迟绑 sessionmaker getter(DevShell.setup 后才有;create_app 传 lambda: shell.sessionmaker;测试可注入 seeded)。
    router = APIRouter()

    @router.get("/leaderboard", response_model=list[LeaderboardEntry])
    async def get_leaderboard(
        limit: int = Query(default=gameconfig.LEADERBOARD_DEFAULT_LIMIT, ge=1, le=gameconfig.LEADERBOARD_MAX_LIMIT)
    ) -> list[LeaderboardEntry]:
        sessionmaker = get_sessionmaker()
        assert sessionmaker is not None, "leaderboard 路由须在 DevShell.setup() 后挂载"
        rows = await top_users_by_points(sessionmaker, limit)
        return [LeaderboardEntry(rank=i + 1, nickname=nick, points=pts) for i, (nick, pts) in enumerate(rows)]

    return router
