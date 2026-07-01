"""GET /leaderboard 查询 + 路由(0050)。httpx/TestClient 未装 → 路由直接 await endpoint。
覆盖:查询(points 降序 / 同分 nickname 升序定序 / limit 截断 / 空表)、路由(rank 递增 + limit 生效)、create_app 布线。"""

from sqlalchemy.pool import StaticPool

from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.db.queries import top_users_by_points
from app.rest.leaderboard import LeaderboardEntry, make_leaderboard_router
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


async def test_route_ranks_and_limits():
    sm = await _seeded_sm({"alice": 100, "bob": 300, "carol": 200})
    result = await _endpoint(make_leaderboard_router(lambda: sm))(limit=2)
    assert result == [
        LeaderboardEntry(rank=1, nickname="bob", points=300),
        LeaderboardEntry(rank=2, nickname="carol", points=200),  # limit=2 截断:alice(100)不出现
    ]
    assert [e.rank for e in result] == [1, 2]  # rank 从 1 递增连续


def test_create_app_registers_leaderboard_route():
    # 布线:create_app() 产出的 app 注册了 GET /leaderboard(不跑 lifespan,只验路由表)。
    app = create_app()
    routes = [r for r in app.routes if getattr(r, "path", None) == "/leaderboard"]
    assert len(routes) == 1
    assert "GET" in routes[0].methods
