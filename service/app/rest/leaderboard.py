# REST 排行榜(rest.md §排行榜)。GET /leaderboard?limit=N → [LeaderboardEntry],按结算积分降序。
#
# 读 DB(结算后全局积分),不读 world——与 GET /lobby/rooms(读 world 头数)分属 rest.md 契约两侧(见 0048/0050)。
# points 是**桌下结算余额**:买进牌桌的筹码在 Seat.points(内存、不落库,storage.md),故全买进桌的人榜上只显桌下余额
# ——「含桌上筹码的总身家」需读 world,列为 future。请求级 session(查询内 async with)、读路径无行锁、比内存滞后(展示够用)。
# **走加密信封**(0094 收编):POST + `{sid, frame}`,内层参数 `{"limit"?: int}`,响应 `{"entries": [...]}`。
# 「解密即认证」⇒ 未登录者读不到排行榜——登录是唯一暴露在外的入口(auth.md §加密信道)。

import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Iterable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.auth.session import SessionStore
from app.db.queries import StatsRow, load_stats_rows, top_users_by_points
from app.rest.secure import SecureRequest, SecureResponse, open_request, seal_response


class LeaderboardEntry(BaseModel):
    # REST DTO ≠ DB 行:rank 由服务端按降序序号赋(1 起),前端直接渲染。
    rank: int  # 名次(1 起;同分按 nickname 升序定序)
    nickname: str  # 显示名
    points: int  # 结算后全局积分(桌上筹码不计,见模块头)


class Rival(BaseModel):
    nickname: str
    amount: int  # 两人之间的净额(恒正)


class StatsEntry(BaseModel):
    # POST /leaderboard/stats 的一行(0110):一段时间内的牌局统计,按净赢降序排名。
    rank: int
    nickname: str
    points: int  # 账号当前全局积分(结算后,不含桌上筹码)
    hands: int  # 这段时间打了几手
    net: int  # 这段时间净赢(可负)
    won_hands: int  # 赢钱的手数(net > 0)
    win_rate: float  # won_hands / hands
    vpip: float | None  # 翻牌前自愿入池率;None = 这段时间只有 0110 之前的手,没数据
    bb_per_100: float | None  # 每百手赢多少个大盲;None = 无大盲数据
    most_won_from: Rival | None  # 从谁那儿赢得最多(两人对账净额最大的正方)
    most_lost_to: Rival | None  # 输给谁最多


def compute_stats(rows: Iterable[StatsRow], since: datetime | None) -> list[StatsEntry]:
    # 纯函数,便于单测。两人之间的输赢怎么算:一手里每个输家输掉的钱,按赢家各自赢的比例分给赢家
    # (没有抽水,一手里输赢正好相抵)。累计下来,A 对 B 的净额 = A 从 B 那赢的 − B 从 A 那赢的。
    by_hand: dict[int, list[StatsRow]] = defaultdict(list)
    for r in rows:
        if since is None or r.end_time >= since:
            by_hand[r.hand_id].append(r)

    hands: dict[str, int] = defaultdict(int)
    net: dict[str, int] = defaultdict(int)
    won: dict[str, int] = defaultdict(int)
    vpip_n: dict[str, int] = defaultdict(int)
    vpip_d: dict[str, int] = defaultdict(int)
    bb_sum: dict[str, float] = defaultdict(float)
    bb_hands: dict[str, int] = defaultdict(int)
    points: dict[str, int] = {}
    took: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))  # took[w][l] = w 从 l 赢的

    for part in by_hand.values():
        winners = [r for r in part if r.net > 0]
        total_won = sum(r.net for r in winners)
        for r in part:
            hands[r.nickname] += 1
            net[r.nickname] += r.net
            points[r.nickname] = r.points
            if r.net > 0:
                won[r.nickname] += 1
            if r.vpip is not None:
                vpip_d[r.nickname] += 1
                vpip_n[r.nickname] += int(r.vpip)
            if r.big_blind:
                bb_sum[r.nickname] += r.net / r.big_blind
                bb_hands[r.nickname] += 1
            if r.net < 0 and total_won > 0:
                for w in winners:
                    took[w.nickname][r.nickname] += -r.net * w.net / total_won

    def rival(me: str, *, winning: bool) -> Rival | None:
        others = set(took[me]) | {w for w in took if me in took[w]}
        best: tuple[float, str] | None = None
        for o in sorted(others):
            balance = took[me].get(o, 0.0) - took[o].get(me, 0.0)
            score = balance if winning else -balance
            if score >= 0.5 and (best is None or score > best[0]):
                best = (score, o)
        return Rival(nickname=best[1], amount=round(best[0])) if best else None

    order = sorted(hands, key=lambda n: (-net[n], -hands[n], n))
    return [
        StatsEntry(
            rank=i + 1,
            nickname=n,
            points=points[n],
            hands=hands[n],
            net=net[n],
            won_hands=won[n],
            win_rate=won[n] / hands[n],
            vpip=vpip_n[n] / vpip_d[n] if vpip_d[n] else None,
            bb_per_100=round(bb_sum[n] / bb_hands[n] * 100, 1) if bb_hands[n] else None,
            most_won_from=rival(n, winning=True),
            most_lost_to=rival(n, winning=False),
        )
        for i, n in enumerate(order)
    ]


def _read_since(params: dict) -> datetime | None:
    # since = epoch 秒(前端传「本地今天 0 点」);缺省 = 全部时间
    raw = params.get("since")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        raise HTTPException(status_code=400, detail="bad request")
    return datetime.fromtimestamp(raw, tz=timezone.utc)


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

    @router.post("/leaderboard/stats", response_model=SecureResponse)
    async def get_leaderboard_stats(req: SecureRequest) -> SecureResponse:
        # 统计榜(0110):参数 {since?: epoch 秒, limit?};响应 {"entries": [StatsEntry...]},按净赢降序。
        session, seq, params = open_request(session_store, req, now())
        limit = _read_limit(params, gameconfig.LEADERBOARD_DEFAULT_LIMIT, gameconfig.LEADERBOARD_MAX_LIMIT)
        since = _read_since(params)
        sessionmaker = get_sessionmaker()
        assert sessionmaker is not None, "leaderboard 路由须在 DevShell.setup() 后挂载"
        entries = compute_stats(await load_stats_rows(sessionmaker), since)[:limit]
        return seal_response(session, seq, {"entries": [e.model_dump() for e in entries]})

    return router
