# REST 个人统计(0111)。POST /stats/me → 只看**自己**的数据(信封里的会话就是「我」)。
#
# 像 GG 的 PokerCraft:VPIP / PFR / 3-Bet / Fold to 3-Bet / C-Bet / WTSD / W$SD / WWSF / AF、按位置的战绩、
# 起手牌 13×13 热力图、盈亏曲线(含摊牌 / 非摊牌两条线)、最近手牌。
#
# 为什么只给自己:热力图和最近手牌带**底牌**,别人的底牌不能给(core.md 不变量 3)。
# 统计全部在 Python 里从「每手结果 + 动作流水」现算(规模小,也免 sqlite 时间比较的坑)。
# 0110 之前的手没有明细、0111 之前的手没有动作流水:相应的比率只用有数据的手当分母,没有就给 null。
#
# 走加密信封:内层参数 `{since?: epoch 秒}`,响应见 PlayerStats。

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.auth.session import SessionStore
from app.db.queries import PlayerHandRow, load_player_hands, load_user_by_nick
from app.rest.secure import SecureRequest, SecureResponse, open_request, seal_response

RANKS = "AKQJT98765432"
MIDDLE = {
    0: [],
    1: ["UTG"],
    2: ["UTG", "CO"],
    3: ["UTG", "HJ", "CO"],
    4: ["UTG", "LJ", "HJ", "CO"],
    5: ["UTG", "UTG+1", "LJ", "HJ", "CO"],
    6: ["UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO"],
}
RECENT_LIMIT = 300  # 最近手牌最多给多少条
GRAPH_POINTS = 400  # 盈亏曲线最多多少个点(手多了就抽样)


def position_names(seats: list[int], button: int, max_seats: int = 9) -> dict[int, str]:
    # 与前端 utils/positions.ts 同一套命名:BTN、SB、BB,再 UTG … CO;两人局庄位即小盲。
    n = len(seats)
    if n < 2:
        return {}
    size = max(max_seats, max(seats) + 1)
    order = sorted(seats, key=lambda s: (s - button) % size)
    if n == 2:
        return {order[0]: "BTN/SB", order[1]: "BB"}
    out = {order[0]: "BTN", order[1]: "SB", order[2]: "BB"}
    middle = MIDDLE.get(n - 3, MIDDLE[6])
    for i, seat in enumerate(order[3:]):
        out[seat] = middle[i] if i < len(middle) else f"MP{i}"
    return out


def combo_of(hole: str) -> str:
    # "AsKd" → "AKo","AhKh" → "AKs","7c7d" → "77";高牌在前。
    r1, s1, r2, s2 = hole[0], hole[1], hole[2], hole[3]
    if RANKS.index(r1) > RANKS.index(r2):
        r1, s1, r2, s2 = r2, s2, r1, s1
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if s1 == s2 else "o")


class Ratio(BaseModel):
    made: int  # 做了几次
    chances: int  # 有几次机会(分母)
    pct: float | None  # made / chances;没机会时 None


def _ratio(made: int, chances: int) -> Ratio:
    return Ratio(made=made, chances=chances, pct=(made / chances if chances else None))


class Summary(BaseModel):
    hands: int
    net: int
    bb_per_100: float | None
    won_hands: int
    win_rate: float | None
    vpip: Ratio
    pfr: Ratio
    three_bet: Ratio
    fold_to_three_bet: Ratio
    cbet: Ratio
    saw_flop: Ratio
    wtsd: Ratio  # Went To ShowDown:看到翻牌后打到摊牌
    wsd: Ratio  # W$SD:摊牌时赢
    wwsf: Ratio  # Won When Saw Flop
    af: float | None  # 翻牌后 (bet + raise) / call
    showdown_net: int  # 摊牌赢的(蓝线)
    non_showdown_net: int  # 没摊牌赢的(红线)


class PositionRow(BaseModel):
    position: str
    hands: int
    net: int
    bb_per_100: float | None
    vpip: float | None


class ComboCell(BaseModel):
    combo: str  # "AKs" / "AKo" / "AA"
    hands: int
    net: int
    won: int
    vpip: int  # 自愿入池了几手


class GraphPoint(BaseModel):
    n: int  # 第几手
    net: int  # 累计盈亏
    sd: int  # 累计摊牌盈亏
    nsd: int  # 累计非摊牌盈亏


class RecentHand(BaseModel):
    id: int
    end_time: datetime
    hole_cards: list[str] | None
    board: list[str]
    net: int
    pot: int
    combo: str | None
    position: str | None
    showed: bool


class PlayerStats(BaseModel):
    nickname: str
    can_view_others: bool = False  # 本地测试开关 DEV_STATS_ANY_USER 开着时为 True,前端据此给「切换玩家」
    summary: Summary
    positions: list[PositionRow]
    combos: list[ComboCell]
    graph: list[GraphPoint]
    recent: list[RecentHand]


def _split(s: str | None) -> list[str]:
    return [s[i : i + 2] for i in range(0, len(s), 2)] if s else []


def compute_player_stats(nickname: str, uid: int, rows: list[PlayerHandRow], since: datetime | None) -> PlayerStats:
    rows = [r for r in rows if since is None or r.end_time >= since]

    c: dict[str, int] = defaultdict(int)
    bb_sum = 0.0
    bb_hands = 0
    pos_stat: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    combos: dict[str, ComboCell] = {}
    graph: list[GraphPoint] = []
    cum = sd = nsd = 0

    for r in rows:
        c["hands"] += 1
        c["net"] += r.net
        if r.net > 0:
            c["won"] += 1
        if r.big_blind:
            bb_sum += r.net / r.big_blind
            bb_hands += 1

        actions = json.loads(r.actions) if r.actions else None
        mine = [a for a in actions if a[1] == uid] if actions is not None else None
        board_len = len(r.board) // 2

        vpip: bool | None = r.vpip
        if mine is not None:
            vpip = any(a[0] == "pre_flop" and a[2] in ("call", "bet", "raise") for a in mine)
        if vpip is not None:
            c["vpip_d"] += 1
            c["vpip"] += int(vpip)

        saw_flop = None
        if actions is not None:
            c["has_log"] += 1
            pre = [a for a in actions if a[0] == "pre_flop"]
            # PFR:翻牌前加过注
            if any(a[1] == uid and a[2] == "raise" for a in pre):
                c["pfr"] += 1
            # 3-Bet / Fold to 3-Bet:按翻牌前加注次数走一遍
            raises = 0
            three_opp = False
            opened_by_me = False
            ft3_opp = False
            for street, who, kind, _amt in pre:
                if who == uid:
                    if raises == 1 and not opened_by_me and not three_opp:
                        three_opp = True
                        c["3b_opp"] += 1
                        if kind == "raise":
                            c["3b"] += 1
                    if raises == 2 and opened_by_me and not ft3_opp:
                        ft3_opp = True
                        c["f3b_opp"] += 1
                        if kind == "fold":
                            c["f3b"] += 1
                if kind == "raise":
                    raises += 1
                    if raises == 1 and who == uid:
                        opened_by_me = True
            folded_pre = any(a[1] == uid and a[0] == "pre_flop" and a[2] == "fold" for a in actions)
            saw_flop = board_len >= 3 and not folded_pre
            c["sf_d"] += 1
            if saw_flop:
                c["sf"] += 1
                if r.net > 0:
                    c["wwsf"] += 1
                if r.showed:
                    c["wtsd"] += 1
                    if r.net > 0:
                        c["wsd"] += 1
                # C-Bet:翻牌前最后一个加注的人是我,翻牌圈我第一次行动之前没人下注,我下注
                last_raiser = next((a[1] for a in reversed(pre) if a[2] == "raise"), None)
                if last_raiser == uid:
                    for street, who, kind, _amt in (a for a in actions if a[0] == "flop"):
                        if who == uid:
                            c["cb_opp"] += 1
                            if kind == "bet":
                                c["cb"] += 1
                            break
                        if kind in ("bet", "raise"):
                            break  # 有人先下注了,没有 c-bet 机会
            # AF:翻牌后的主动下注 / 跟注
            for street, who, kind, _amt in actions:
                if who == uid and street != "pre_flop":
                    if kind in ("bet", "raise"):
                        c["agg"] += 1
                    elif kind == "call":
                        c["calls"] += 1

        if r.showed:
            sd += r.net
        else:
            nsd += r.net
        cum += r.net
        graph.append(GraphPoint(n=c["hands"], net=cum, sd=sd, nsd=nsd))

        # 位置
        position = None
        if r.button_position is not None and r.seat_position is not None and r.seats:
            position = position_names(list(r.seats), r.button_position).get(r.seat_position)
        if position:
            ps = pos_stat[position]
            ps["hands"] += 1
            ps["net"] += r.net
            if r.big_blind:
                ps["bb"] += r.net / r.big_blind
                ps["bb_hands"] += 1
            if vpip is not None:
                ps["vpip_d"] += 1
                ps["vpip"] += int(vpip)

        # 起手牌格子
        if r.hole_cards and len(r.hole_cards) == 4:
            key = combo_of(r.hole_cards)
            cell = combos.setdefault(key, ComboCell(combo=key, hands=0, net=0, won=0, vpip=0))
            cell.hands += 1
            cell.net += r.net
            cell.won += int(r.net > 0)
            cell.vpip += int(bool(vpip))

    if len(graph) > GRAPH_POINTS:  # 抽样,保留最后一个点
        step = len(graph) / GRAPH_POINTS
        graph = [graph[int(i * step)] for i in range(GRAPH_POINTS - 1)] + [graph[-1]]

    order = ["BTN", "BTN/SB", "SB", "BB", "UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO"]
    positions = [
        PositionRow(
            position=p,
            hands=int(v["hands"]),
            net=int(v["net"]),
            bb_per_100=round(v["bb"] / v["bb_hands"] * 100, 1) if v["bb_hands"] else None,
            vpip=v["vpip"] / v["vpip_d"] if v["vpip_d"] else None,
        )
        for p, v in sorted(pos_stat.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
    ]

    recent = []
    for r in reversed(rows[-RECENT_LIMIT:]):
        position = None
        if r.button_position is not None and r.seat_position is not None and r.seats:
            position = position_names(list(r.seats), r.button_position).get(r.seat_position)
        recent.append(
            RecentHand(
                id=r.hand_id,
                end_time=r.end_time,
                hole_cards=_split(r.hole_cards) or None,
                board=_split(r.board),
                net=r.net,
                pot=r.final_pot,
                combo=combo_of(r.hole_cards) if r.hole_cards and len(r.hole_cards) == 4 else None,
                position=position,
                showed=bool(r.showed),
            )
        )

    hands = c["hands"]
    summary = Summary(
        hands=hands,
        net=c["net"],
        bb_per_100=round(bb_sum / bb_hands * 100, 1) if bb_hands else None,
        won_hands=c["won"],
        win_rate=c["won"] / hands if hands else None,
        vpip=_ratio(c["vpip"], c["vpip_d"]),
        pfr=_ratio(c["pfr"], c["has_log"]),
        three_bet=_ratio(c["3b"], c["3b_opp"]),
        fold_to_three_bet=_ratio(c["f3b"], c["f3b_opp"]),
        cbet=_ratio(c["cb"], c["cb_opp"]),
        saw_flop=_ratio(c["sf"], c["sf_d"]),
        wtsd=_ratio(c["wtsd"], c["sf"]),
        wsd=_ratio(c["wsd"], c["wtsd"]),
        wwsf=_ratio(c["wwsf"], c["sf"]),
        af=round(c["agg"] / c["calls"], 2) if c["calls"] else (float(c["agg"]) if c["agg"] else None),
        showdown_net=sd,
        non_showdown_net=nsd,
    )
    return PlayerStats(
        nickname=nickname,
        summary=summary,
        positions=positions,
        combos=sorted(combos.values(), key=lambda x: x.combo),
        graph=graph,
        recent=recent,
    )


def make_stats_router(
    get_sessionmaker: Callable[[], async_sessionmaker[AsyncSession] | None],
    session_store: SessionStore,
    now: Callable[[], float] = time.time,
) -> APIRouter:
    router = APIRouter()

    @router.post("/stats/me", response_model=SecureResponse)
    async def get_my_stats(req: SecureRequest) -> SecureResponse:
        session, seq, params = open_request(session_store, req, now())
        raw = params.get("since")
        since = None
        if raw is not None:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
                raise HTTPException(status_code=400, detail="bad request")
            since = datetime.fromtimestamp(raw, tz=timezone.utc)
        sessionmaker = get_sessionmaker()
        assert sessionmaker is not None, "stats 路由须在 DevShell.setup() 后挂载"
        # 默认只看自己。带 user= 看别人只在本地测试开关 DEV_STATS_ANY_USER 打开时允许(会暴露别人的底牌热力图)。
        target = params.get("user", session.nickname)
        if not isinstance(target, str):
            raise HTTPException(status_code=400, detail="bad request")
        any_user = bool(gameconfig.DEV_STATS_ANY_USER)
        if target != session.nickname and not any_user:
            raise HTTPException(status_code=403, detail="stats of other players are private")
        user = await load_user_by_nick(sessionmaker, target)
        if user is None:
            raise HTTPException(status_code=404, detail="no such user")
        stats = compute_player_stats(target, user[0], await load_player_hands(sessionmaker, user[0]), since)
        stats.can_view_others = any_user
        return seal_response(session, seq, stats.model_dump(mode="json"))

    return router
