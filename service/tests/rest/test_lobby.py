"""GET /lobby/rooms 投影 + 路由(0048)。httpx/TestClient 本环境未装 → 路由测试直接 await endpoint。
覆盖:纯投影(字段 / big_blind 派生 / seated 含 OFFLINE 保座 / watching 计数 / 排序 / 空 / HAND_STARTED)、
路由体(fake world→list_rooms;world=None→503)、create_app 布线。"""

import asyncio

import pytest
from fastapi import HTTPException

from app.core.enums import RoomStatus, UserStatus
from app.rest.lobby import RoomMeta, list_rooms, make_lobby_router
from app.shell.lifespan import create_app
from tests.builders import hand_world, make_world, player, room_with, seat


def _endpoint(router):
    # 取 /lobby/rooms 的原始 async handler(APIRoute.endpoint),供直接 await(无需 HTTP client)。
    routes = [r for r in router.routes if getattr(r, "path", None) == "/lobby/rooms"]
    assert len(routes) == 1, "lobby 路由应恰好注册一条"
    return routes[0].endpoint


def test_list_rooms_empty():
    assert list_rooms(make_world()) == []


def test_list_rooms_projection_fields():
    # 2 在座(alice READY / bob PLAYING)+ 1 观战(carol WATCHING);小盲 5 → 大盲 10 派生。
    room = room_with(
        seats=[seat("alice", 100), None, seat("bob", 100)],
        small_blind=5,
        buy_in=200,
        max_seats=6,
        users_in_room={
            "alice": UserStatus.READY_TO_PLAY,
            "bob": UserStatus.PLAYING,
            "carol": UserStatus.WATCHING,
        },
    )
    (meta,) = list_rooms(make_world(rooms={"table1": room}))
    assert meta == RoomMeta(
        id="table1",
        small_blind=5,
        big_blind=10,
        buy_in=200,
        max_seats=6,
        seated=2,
        watching=1,
        status=RoomStatus.PENDING_START,
    )


def test_list_rooms_sorted_by_id():
    r = room_with(seats=[None], small_blind=1, buy_in=100, max_seats=2)
    world = make_world(rooms={"zeta": r, "alpha": r, "mid": r})
    assert [m.id for m in list_rooms(world)] == ["alpha", "mid", "zeta"]


def test_seated_counts_offline_held_seat():
    # OFFLINE 玩家保座:座位仍占 → seated 计入;但状态非 WATCHING → 不计 watching(在座与在线正交)。
    room = room_with(
        seats=[seat("a", 100)],
        small_blind=1,
        buy_in=100,
        max_seats=4,
        users_in_room={"a": UserStatus.OFFLINE},
    )
    (meta,) = list_rooms(make_world(rooms={"r": room}))
    assert (meta.seated, meta.watching) == (1, 0)


def test_hand_started_status_and_seated():
    # 手牌进行中的房:status=HAND_STARTED,seated=玩家数,watching=0。
    world = hand_world([player("a", 50, seat=0), player("b", 50, seat=1)])
    (meta,) = list_rooms(world)
    assert meta.status is RoomStatus.HAND_STARTED
    assert (meta.seated, meta.watching) == (2, 0)


def test_router_endpoint_returns_list_rooms():
    room = room_with(seats=[seat("a", 100)], small_blind=2, buy_in=50, max_seats=4)
    world = make_world(rooms={"r": room})
    result = asyncio.run(_endpoint(make_lobby_router(lambda: world))())
    assert result == list_rooms(world)


def test_router_endpoint_503_when_world_not_ready():
    # world 尚未建(setup 前的极窄窗口)→ 503,不崩、不返回空表冒充「无房」。
    with pytest.raises(HTTPException) as ei:
        asyncio.run(_endpoint(make_lobby_router(lambda: None))())
    assert ei.value.status_code == 503


def test_create_app_registers_lobby_route():
    # 布线:create_app() 产出的 app 注册了 GET /lobby/rooms(不跑 lifespan,只验路由表)。
    app = create_app()
    lobby = [r for r in app.routes if getattr(r, "path", None) == "/lobby/rooms"]
    assert len(lobby) == 1
    assert "GET" in lobby[0].methods
