"""GET /leaderboard 查询 + 路由(0050)。httpx/TestClient 未装 → 路由直接 await endpoint。
覆盖:查询(points 降序 / 同分 nickname 升序定序 / limit 截断 / 空表)、路由(rank 递增 + limit 生效)、create_app 布线。"""

from sqlalchemy.pool import StaticPool

from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.db.queries import top_users_by_points
import pytest
from fastapi import HTTPException

from app.auth.session import SessionStore
from app.rest.leaderboard import LeaderboardEntry, make_leaderboard_router
from tests.rest._sealed import T0, TTL, call, seal_req
from app.shell.lifespan import create_app


async def _seeded_sm(users: dict[str, int]):
    # users: {nickname: points};内存 sqlite 建表 + 种子(id 自 1 递增)。
    engine = make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            for i, (nick, pts) in enumerate(users.items(), start=1):
                s.add(User(id=i, nickname=nick, points=pts))
    return sm


def _endpoint(router):
    routes = [r for r in router.routes if getattr(r, "path", None) == "/leaderboard"]
    assert len(routes) == 1, "leaderboard 路由应恰好注册一条"
    return routes[0].endpoint


async def test_query_orders_by_points_desc_then_nick():
    sm = await _seeded_sm({"alice": 100, "bob": 300, "carol": 100})
    rows = await top_users_by_points(sm, 10)
    assert rows == [("bob", 300), ("alice", 100), ("carol", 100)]  # 降序;同分 alice<carol(nickname 升序)


async def test_query_limit_truncates():
    sm = await _seeded_sm({"a": 5, "b": 4, "c": 3, "d": 2})
    assert await top_users_by_points(sm, 2) == [("a", 5), ("b", 4)]


async def test_query_empty_table():
    sm = await _seeded_sm({})
    assert await top_users_by_points(sm, 10) == []


def _wired(sm):
    # 端点 + 一个活会话:0094 起本端点走加密信封,「解密即认证」。
    store = SessionStore(TTL)
    sid, session = store.create("alice", "Alice", T0)
    return _endpoint(make_leaderboard_router(lambda: sm, store, now=lambda: T0)), sid, session


async def test_route_ranks_and_limits():
    sm = await _seeded_sm({"alice": 100, "bob": 300, "carol": 200})
    endpoint, sid, session = _wired(sm)
    payload = await call(endpoint, sid, session, {"limit": 2})
    assert payload["entries"] == [
        LeaderboardEntry(rank=1, nickname="bob", points=300).model_dump(),
        LeaderboardEntry(rank=2, nickname="carol", points=200).model_dump(),  # limit=2 截断:alice(100)不出现
    ]


async def test_route_rejects_without_envelope():
    # 0094 的正题:排行榜也要登录才看得到。未知 sid → 统一 401(fail-closed)。
    sm = await _seeded_sm({"alice": 100})
    endpoint, _sid, session = _wired(sm)
    with pytest.raises(HTTPException) as ei:
        await endpoint(seal_req("bogus-sid", session, 1, {}))
    assert ei.value.status_code == 401


async def test_route_bad_limit_is_400_not_silently_clamped():
    # 参数进了信封就得自己校:收编前这层是 FastAPI 的 Query(ge=,le=)。信封已验过 ⇒ 畸形是客户端 bug,
    # 按 400 分层(不是 401),更不能默默截断成合法值——那会让「limit=0」悄悄变成一整页。
    sm = await _seeded_sm({"alice": 100})
    endpoint, sid, session = _wired(sm)
    # seq 每次都要新的:同一个 seq 重来会被防重放窗判成重放、回 401,那就测不到 400 了。
    for seq, bad in enumerate((0, -1, 10_000, "5", True), start=1):
        with pytest.raises(HTTPException) as ei:
            await endpoint(seal_req(sid, session, seq, {"limit": bad}))
        assert ei.value.status_code == 400, bad


def test_create_app_registers_leaderboard_route():
    # 布线:create_app() 产出的 app 注册了 POST /leaderboard(不跑 lifespan,只验路由表)。
    app = create_app()
    routes = [r for r in app.routes if getattr(r, "path", None) == "/leaderboard"]
    assert len(routes) == 1
    assert routes[0].methods == {"POST"}, "0094 收编进信封 ⇒ 不再有明文 GET"
