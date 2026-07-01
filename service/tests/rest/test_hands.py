"""GET /hands 查询 + 路由(0051)。httpx 未装 → 路由直接 await endpoint。
覆盖:查询(新→旧全量 / user 过滤 / before 游标 / limit / 参与者组装+排序 / 空)、路由(user+DTO net / 未知用户空)、布线。"""

from datetime import datetime, timezone

from sqlalchemy.pool import StaticPool

from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import HandParticipant, HandRecord, User
from app.db.queries import list_hands
from app.rest.hands import make_hands_router
from app.shell.lifespan import create_app

_T = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _seeded():
    # 3 用户 + 3 手(id 1..3)+ 参与者。hand1: alice+bob;hand2: alice+carol;hand3: bob+carol。
    engine = make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            s.add_all(
                [
                    User(id=1, nickname="alice", points=100),
                    User(id=2, nickname="bob", points=200),
                    User(id=3, nickname="carol", points=300),
                ]
            )
            s.add_all(
                [
                    HandRecord(id=1, dedupe_key="r1:1", room="r1", start_time=_T, end_time=_T, final_pot=20),
                    HandRecord(id=2, dedupe_key="r1:2", room="r1", start_time=_T, end_time=_T, final_pot=40),
                    HandRecord(id=3, dedupe_key="r2:1", room="r2", start_time=_T, end_time=_T, final_pot=60),
                ]
            )
            await s.flush()  # 先落 User/HandRecord 父行,满足下面 HandParticipant 的 FK(sqlite foreign_keys=ON 即时校验)
            s.add_all(
                [
                    HandParticipant(hand_id=1, uid=1, initial_points=10, final_points=20),  # alice +10
                    HandParticipant(hand_id=1, uid=2, initial_points=10, final_points=0),  # bob -10
                    HandParticipant(hand_id=2, uid=1, initial_points=20, final_points=0),  # alice -20
                    HandParticipant(hand_id=2, uid=3, initial_points=20, final_points=40),  # carol +20
                    HandParticipant(hand_id=3, uid=2, initial_points=30, final_points=60),  # bob +30
                    HandParticipant(hand_id=3, uid=3, initial_points=30, final_points=0),  # carol -30
                ]
            )
    return sm


def _endpoint(router):
    routes = [r for r in router.routes if getattr(r, "path", None) == "/hands"]
    assert len(routes) == 1, "hands 路由应恰好注册一条"
    return routes[0].endpoint


async def test_query_all_newest_first():
    rows = await list_hands(await _seeded(), limit=10)
    assert [hid for hid, *_ in rows] == [3, 2, 1]  # id 降序(新→旧)


async def test_query_user_filter():
    rows = await list_hands(await _seeded(), participant_uid=1, limit=10)  # alice 参与 hand 1,2
    assert [hid for hid, *_ in rows] == [2, 1]


async def test_query_before_cursor():
    rows = await list_hands(await _seeded(), before_id=3, limit=10)  # id<3 → hand 2,1(不含游标条 3)
    assert [hid for hid, *_ in rows] == [2, 1]


async def test_query_limit_truncates_newest():
    rows = await list_hands(await _seeded(), limit=1)
    assert [hid for hid, *_ in rows] == [3]


async def test_query_participants_assembled_sorted_by_nick():
    rows = await list_hands(await _seeded(), participant_uid=1, limit=10)
    hand2 = next(r for r in rows if r[0] == 2)
    assert hand2[5] == (("alice", 20, 0), ("carol", 20, 40))  # 全部参与者,按 nick 升序


async def test_query_empty_before_first():
    assert await list_hands(await _seeded(), before_id=1, limit=10) == []  # id<1 无


async def test_query_room_filter():
    sm = await _seeded()
    assert [hid for hid, *_ in await list_hands(sm, room="r1", limit=10)] == [2, 1]  # r1 有 hand 1,2
    assert [hid for hid, *_ in await list_hands(sm, room="r2", limit=10)] == [3]  # r2 有 hand 3
    assert await list_hands(sm, room="ghost", limit=10) == []  # 无此房 → 空


async def test_query_room_and_user_combine():
    sm = await _seeded()
    # alice(uid1) 参与 hand 1,2(皆 r1);room=r1 + alice → [2,1];room=r2 + alice → 空(alice 未在 r2)
    assert [hid for hid, *_ in await list_hands(sm, room="r1", participant_uid=1, limit=10)] == [2, 1]
    assert await list_hands(sm, room="r2", participant_uid=1, limit=10) == []


async def test_route_room_filter():
    sm = await _seeded()
    result = await _endpoint(make_hands_router(lambda: sm))(room="r2", user=None, before=None, limit=10)
    assert [v.id for v in result] == [3]


async def test_route_user_and_dto_net():
    sm = await _seeded()
    result = await _endpoint(make_hands_router(lambda: sm))(room=None, user="alice", before=None, limit=10)
    assert [v.id for v in result] == [2, 1]  # alice 的手,新→旧
    hand2 = next(v for v in result if v.id == 2)
    alice = next(p for p in hand2.participants if p.nickname == "alice")
    assert (alice.initial_points, alice.final_points, alice.net) == (20, 0, -20)  # net 派生
    assert hand2.dedupe_key == "r1:2" and hand2.final_pot == 40


async def test_route_unknown_user_returns_empty():
    sm = await _seeded()
    result = await _endpoint(make_hands_router(lambda: sm))(room=None, user="nobody", before=None, limit=10)
    assert result == []


def test_create_app_registers_hands_route():
    app = create_app()
    routes = [r for r in app.routes if getattr(r, "path", None) == "/hands"]
    assert len(routes) == 1
    assert "GET" in routes[0].methods
