# POST /user/logout 信封端点(0097,BUG-8:此前 SessionStore.revoke 全仓零调用者、前端「退出」只清本地)。
# 覆盖:happy(封回 status:ok + seq 回显、会话真被吊销、同 sid 再用即 401)、只吊销自己这一个
# (同账号别的设备不受牵连)、未知 sid 401(信封继承)、路由恰好注册一条。
# 不装 TestClient,取 APIRoute.endpoint 直接 await(同 tests/rest 其余文件)。

import pytest
from fastapi import HTTPException

from app.auth.session import SessionStore
from app.rest.profile import make_profile_router
from tests.rest._sealed import T0, TTL, call, seal_req


def _endpoint(store, now: float = T0):
    router = make_profile_router(lambda: None, store, now=lambda: now)  # 登出不碰 DB → sessionmaker 用不着
    routes = [r for r in router.routes if getattr(r, "path", None) == "/user/logout"]
    assert len(routes) == 1, "/user/logout 路由应恰好注册一条"
    return routes[0].endpoint


async def test_logout_revokes_the_calling_session():
    store = SessionStore(TTL)
    sid, session = store.create("alice", "Alice", T0)

    assert await call(_endpoint(store), sid, session, {}) == {"status": "ok"}  # 响应封回且 seq 回显(call 内断言)

    assert store.lookup(sid, T0) is None  # 会话已从表里摘掉
    assert session.expires_at == 0.0  # 且对象被判死 → 那条活 ws 下一帧被 4401 关掉(0097)


async def test_second_logout_with_same_sid_is_401():
    # 吊销之后这个 sid 就查不到了,信封那一关先拒 → 401(与未知 sid 无从区分,secure.py 有意统一)。
    store = SessionStore(TTL)
    sid, session = store.create("alice", "Alice", T0)
    endpoint = _endpoint(store)
    await call(endpoint, sid, session, {})

    with pytest.raises(HTTPException) as e:
        await endpoint(seal_req(sid, session, 2, {}))
    assert e.value.status_code == 401


async def test_logout_only_kills_its_own_session():
    # 登出是「退出这台设备」,不是「退出所有设备」——后者是改密码的语义(0097 / rest.md)。
    store = SessionStore(TTL)
    sid_here, session_here = store.create("alice", "Alice", T0)
    sid_phone, session_phone = store.create("alice", "Alice", T0)

    await call(_endpoint(store), sid_here, session_here, {})

    assert store.lookup(sid_here, T0) is None
    assert store.lookup(sid_phone, T0) is session_phone  # 另一台设备照常在线
    assert session_phone.expires_at > T0


async def test_unknown_sid_401():
    store = SessionStore(TTL)
    _, session = store.create("alice", "Alice", T0)
    with pytest.raises(HTTPException) as e:
        await _endpoint(store)(seal_req("never-existed", session, 1, {}))
    assert e.value.status_code == 401


def test_create_app_registers_logout_route():
    from app.shell.lifespan import create_app

    app = create_app()
    routes = [r for r in app.routes if getattr(r, "path", None) == "/user/logout"]
    assert len(routes) == 1 and routes[0].methods == {"POST"}  # 只认 POST(信封端点,同 0094)
