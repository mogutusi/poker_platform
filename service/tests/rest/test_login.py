# POST /user/login 端点穷举(P5,见 docs/auth.md §登录握手 / changes/0059)。
# 内存 sqlite(StaticPool)+ create_all + 种 login-enabled 用户;httpx/TestClient 未装 → 直接 await handler。
# 覆盖:正路(响应 K_user 解密得 session_id/token/exp、会话登记且 token/name/nickname 一致)、错密码/未知账号/
# legacy(name=NULL)/错 K_user blob/坏 iv hex 一律 401、失败不铸会话、create_app 挂路由。

import json
import secrets

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from ttxsgm import sm4_cbc_dec, sm4_cbc_enc

from app.auth.passwords import hash_password
from app.auth.session import SessionStore
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.rest.login import LoginRequest, make_login_router

_ROUNDS = 500
_T0 = 1_000_000.0
_TTL = 3600
_PW = "correct horse battery staple"
_KUSER = secrets.token_bytes(16)


async def _setup():
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            s.add(User(id=1, nickname="Alice", points=1000, name="alice",
                       hash_password=hash_password(_PW, _ROUNDS), k_cur=_KUSER.hex(), k_cur_ver=1))
            s.add(User(id=2, nickname="Legacy", points=0))  # name/hash/k_cur NULL:未启用登录
            s.add(User(id=3, nickname="Bob", points=500, name="bob",
                       hash_password=hash_password(_PW, _ROUNDS), k_cur=_KUSER.hex(), k_cur_ver=1))  # 第二个可登录用户(nonce 按 name 隔离测)
    return sm


def _endpoint(router):
    routes = [r for r in router.routes if getattr(r, "path", None) == "/user/login"]
    assert len(routes) == 1, "login 路由应恰好注册一条"
    return routes[0].endpoint


def _make_blob(payload: dict, key: bytes = _KUSER) -> tuple[str, str]:
    # blob 自 0063 起须带 ts(重放守卫);默认盖 _T0(= 端点注入的 now),调用方可覆盖测新鲜窗。
    iv = secrets.token_bytes(16)
    return iv.hex(), sm4_cbc_enc(key, iv, json.dumps({"ts": _T0, **payload}).encode()).hex()


async def _login(store, sm, name, iv_hex, blob_hex, now=_T0):
    router = make_login_router(lambda: sm, store, now=lambda: now)
    return await _endpoint(router)(LoginRequest(name=name, iv=iv_hex, blob=blob_hex))


async def test_happy_path_issues_k_user_encrypted_session():
    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n1"})
    resp = await _login(store, sm, "alice", iv_hex, blob_hex)
    # 响应用 K_user 解密 → session data
    data = json.loads(sm4_cbc_dec(_KUSER, bytes.fromhex(resp.iv), bytes.fromhex(resp.blob)))
    assert set(data) == {"session_id", "session_token", "exp", "rotate"}
    assert data["exp"] == _T0 + _TTL
    assert data["rotate"] is False  # 当前钥登录 → 无换钥提示(0066)
    # 会话已登记、token/name/nickname 一致
    session = store.lookup(data["session_id"], _T0)
    assert session is not None
    assert session.token.hex() == data["session_token"]
    assert (session.name, session.nickname) == ("alice", "Alice")


async def test_wrong_password_401_no_session():
    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": "WRONG", "client_nonce": "n"})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", iv_hex, blob_hex)
    assert ei.value.status_code == 401
    assert len(store) == 0  # 失败不铸会话


async def test_unknown_name_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n"})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "nobody", iv_hex, blob_hex)
    assert ei.value.status_code == 401


async def test_legacy_null_name_user_401():
    # Legacy(name=NULL)→ load_user_for_login 按 name 查不到 → 401(用 nickname "Legacy" 也不匹配 name)。
    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n"})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "Legacy", iv_hex, blob_hex)
    assert ei.value.status_code == 401


async def test_wrong_k_user_blob_401():
    # 用别的 K_user 封 blob → 服务器用登记 K_user 解出乱码 → authenticate None → 401。
    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n"}, key=secrets.token_bytes(16))
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", iv_hex, blob_hex)
    assert ei.value.status_code == 401


async def test_bad_iv_hex_401():
    sm = await _setup()
    store = SessionStore(_TTL)
    _, blob_hex = _make_blob({"password": _PW, "client_nonce": "n"})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", "nothex!!", blob_hex)
    assert ei.value.status_code == 401


async def test_db_error_returns_uniform_401():
    # 基础设施错(DB 查询抛)也归统一 401,不冒成 500 泄「DB 故障 vs 认证失败」之别。
    store = SessionStore(_TTL)

    def _raising_sessionmaker():
        raise RuntimeError("db down")

    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n"})
    router = make_login_router(_raising_sessionmaker, store, now=lambda: _T0)
    with pytest.raises(HTTPException) as ei:
        await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))
    assert ei.value.status_code == 401
    assert len(store) == 0  # 未铸会话


async def test_dev_seeded_user_can_login():
    # 端到端:seed_dev_users 补齐鉴权列 → DEV_USERS[0] 用 DEV_PASSWORD + DEV_KUSER 真登录(changes/0060)。
    from app import gameconfig
    from app.shell.lifespan import seed_dev_users

    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    await seed_dev_users(sm)
    store = SessionStore(_TTL)
    dev_nick = gameconfig.DEV_USERS[0]
    dev_key = bytes.fromhex(gameconfig.DEV_KUSER)
    iv_hex, blob_hex = _make_blob({"password": gameconfig.DEV_PASSWORD, "client_nonce": "n"}, key=dev_key)
    resp = await _login(store, sm, dev_nick, iv_hex, blob_hex)
    data = json.loads(sm4_cbc_dec(dev_key, bytes.fromhex(resp.iv), bytes.fromhex(resp.blob)))
    session = store.lookup(data["session_id"], _T0)
    assert session is not None and session.name == dev_nick


async def test_seed_backfills_pre_p5_dev_user():
    # pre-P5 dev 行(只 nickname/points、name=NULL)→ seed_dev_users 回填鉴权列(login-enable),不重置 points。
    from app import gameconfig
    from app.shell.lifespan import _dev_uid, seed_dev_users

    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    uid = _dev_uid(0)
    async with sm() as s:
        async with s.begin():
            s.add(User(id=uid, nickname=gameconfig.DEV_USERS[0], points=777))  # 无鉴权列(pre-P5)
    await seed_dev_users(sm)
    async with sm() as s:
        user = await s.get(User, uid)
        assert user.points == 777  # 回填不重置积分
        assert user.name == gameconfig.DEV_USERS[0]
        assert user.k_cur == gameconfig.DEV_KUSER and user.hash_password is not None
        assert user.k_cur_ver == 1 and user.k_cur_until is None  # dev 钥不排程(0066:cron 不轮 dev 共享钥)


def test_create_app_registers_login_route():
    # 布线:create_app() 注册 POST /user/login(不跑 lifespan,只验路由表)。
    from app.shell.lifespan import create_app

    app = create_app()
    routes = [r for r in app.routes if getattr(r, "path", None) == "/user/login"]
    assert len(routes) == 1 and "POST" in routes[0].methods


async def test_stale_ts_rejected_401():
    # freshness 窗(0063):|now - blob.ts| 超 LOGIN_REPLAY_WINDOW_SECONDS → 401(截获的旧包过窗即废)。
    from app import gameconfig

    sm = await _setup()
    store = SessionStore(_TTL)
    old_ts = _T0 - gameconfig.LOGIN_REPLAY_WINDOW_SECONDS - 1
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-old", "ts": old_ts})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", iv_hex, blob_hex)
    assert ei.value.status_code == 401 and len(store) == 0


async def test_future_ts_rejected_401():
    # 绝对值窗:超前(坏钟/伪造未来包)同样拒,不只拒过去。
    from app import gameconfig

    sm = await _setup()
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob(
        {"password": _PW, "client_nonce": "n-fut", "ts": _T0 + gameconfig.LOGIN_REPLAY_WINDOW_SECONDS + 1}
    )
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", iv_hex, blob_hex)
    assert ei.value.status_code == 401


async def test_replayed_login_blob_rejected_401():
    # nonce 去重(0063):同一 {name, iv, blob} 原包重投——首投成功铸会话,重投 nonce 撞库 → 401、不再铸。
    # 注:守卫状态活在 router 内,两次须走同一 router(_login 每调新建 router,故此测手持一个)。
    sm = await _setup()
    store = SessionStore(_TTL)
    router = make_login_router(lambda: sm, store, now=lambda: _T0)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-replay"})
    await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))  # 首投成功
    assert len(store) == 1
    with pytest.raises(HTTPException) as ei:
        await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))  # 原包重放
    assert ei.value.status_code == 401
    assert len(store) == 1  # 未再铸会话


async def test_fresh_nonce_second_login_succeeds():
    # 正常二登(新 nonce)不受守卫影响——去重只挡「同 nonce 重放」,不挡真用户重复登录。
    sm = await _setup()
    store = SessionStore(_TTL)
    router = make_login_router(lambda: sm, store, now=lambda: _T0)
    for nonce in ("n-1", "n-2"):
        iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": nonce})
        await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))
    assert len(store) == 2  # 两会话都铸成(轮换场景:新登录顶替旧连接,会话可并存)


async def test_replay_blocked_across_full_freshness_window_of_skewed_blob():
    # 回归(0063 自 review):ts 超前 now 至 W 的 blob,其新鲜期最晚到 ts+W = 首登 now+2W;nonce 条目
    # TTL 若只 W 会「条目先过期、blob 还新鲜」留重放缝。现 TTL=2W + 严格过期剪枝:整个新鲜期内重放必 401。
    from app import gameconfig

    sm = await _setup()
    store = SessionStore(_TTL)
    w = gameconfig.LOGIN_REPLAY_WINDOW_SECONDS
    clock = {"now": _T0}
    router = make_login_router(lambda: sm, store, now=lambda: clock["now"])
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-skew", "ts": _T0 + w})  # 最大容许超前
    await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))  # 首登成功
    for replay_at in (_T0 + w, _T0 + 2 * w):  # 旧缝所在时刻 + blob 新鲜期最后一刻
        clock["now"] = replay_at
        with pytest.raises(HTTPException) as ei:
            await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))
        assert ei.value.status_code == 401
    assert len(store) == 1  # 全程只铸首登那一个会话


async def test_failed_auth_does_not_poison_nonce_cache():
    # 守卫在 authenticate **之后**(0063 决策 5):错密码探测(nonce=X)灌不进缓存——随后正确登录复用 X 仍成功。
    # 若守卫挪到 authenticate 前,无凭证者可用探测包 401-锁死合法登录(此测杀该回归)。
    sm = await _setup()
    store = SessionStore(_TTL)
    router = make_login_router(lambda: sm, store, now=lambda: _T0)
    iv_hex, blob_hex = _make_blob({"password": "WRONG", "client_nonce": "n-probe"})
    with pytest.raises(HTTPException):
        await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))  # 探测:密码错 401
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-probe"})  # 同 nonce 正确登录
    await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))
    assert len(store) == 1  # 未被探测锁死


async def test_nonce_isolated_per_account_name():
    # nonce 键于 (name, nonce)(端到端):alice/bob 撞同一 nonce 串,双方都能登录(若退化成全局键则第二人 401)。
    sm = await _setup()
    store = SessionStore(_TTL)
    router = make_login_router(lambda: sm, store, now=lambda: _T0)
    for name in ("alice", "bob"):
        iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-shared"})
        await _endpoint(router)(LoginRequest(name=name, iv=iv_hex, blob=blob_hex))
    assert len(store) == 2


# ── K_user 双钥轮换(0066):旧钥宽限登录 + rotate 提示 ──

_NEW_KUSER = secrets.token_bytes(16)  # 轮换后的当前钥(_KUSER 充旧钥 k_prev)


async def _setup_rotated(prev_until: float):
    # 已轮换过一次的账号:k_cur=_NEW_KUSER(v2)、k_prev=_KUSER(v1,宽限至 prev_until)。
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            s.add(User(id=1, nickname="Alice", points=1000, name="alice",
                       hash_password=hash_password(_PW, _ROUNDS),
                       k_cur=_NEW_KUSER.hex(), k_cur_ver=2, k_cur_until=_T0 + 7 * 86400,  # 下轮排程(登录不查它)
                       k_prev=_KUSER.hex(), k_prev_ver=1, k_prev_until=prev_until))
    return sm


async def test_old_key_within_grace_logs_in_with_rotate_hint():
    # 旧钥(k_prev)在宽限内登录成功;响应用**旧钥**加密(客户端手里只有旧钥)且 rotate=true 提示换新。
    # prev_until 恰取 now(=_T0):钉「宽限含端点」(now <= k_prev_until)——写成 < 这里必红。
    sm = await _setup_rotated(prev_until=_T0)
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-old-key"})  # 旧钥 _KUSER 封 blob
    resp = await _login(store, sm, "alice", iv_hex, blob_hex)
    data = json.loads(sm4_cbc_dec(_KUSER, bytes.fromhex(resp.iv), bytes.fromhex(resp.blob)))  # 旧钥可解
    assert data["rotate"] is True
    assert store.lookup(data["session_id"], _T0) is not None  # 会话照铸(宽限登录是正常登录)


async def test_overdue_k_cur_still_logs_in():
    # 0066 决策 2 的钉子:k_cur_until 是**排程**不是拒登时刻——轮换 cron 迟跑(until 已过期)时,
    # 当前钥登录必须照常成功,否则运维故障放大成全员锁死。若有人把「过期拒登」写进登录路径,这里必红。
    sm = await _setup()
    async with sm() as s:
        async with s.begin():
            user = await s.get(User, 1)
            user.k_cur_until = _T0 - 1  # 早该轮换而 cron 没跑
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-overdue"})
    resp = await _login(store, sm, "alice", iv_hex, blob_hex)
    data = json.loads(sm4_cbc_dec(_KUSER, bytes.fromhex(resp.iv), bytes.fromhex(resp.blob)))
    assert data["rotate"] is False and len(store) == 1


async def test_replayed_old_key_blob_rejected():
    # 重放守卫 × 双钥:旧钥首登成功后,同一 blob 原包重投必 401(nonce 去重在**匹配任一把**之后统一生效;
    # 若有人把守卫挪进 k_cur 单臂,这里必红)。
    sm = await _setup_rotated(prev_until=_T0 + 1)
    store = SessionStore(_TTL)
    router = make_login_router(lambda: sm, store, now=lambda: _T0)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-old-replay"})  # 旧钥封
    await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))  # 首投成功(宽限)
    with pytest.raises(HTTPException) as ei:
        await _endpoint(router)(LoginRequest(name="alice", iv=iv_hex, blob=blob_hex))  # 原包重放
    assert ei.value.status_code == 401
    assert len(store) == 1  # 未再铸会话


async def test_new_key_after_rotation_logs_in_no_hint():
    # 新钥(k_cur)登录:rotate=false;响应用新钥加密。
    sm = await _setup_rotated(prev_until=_T0 + 1)
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-new-key"}, key=_NEW_KUSER)
    resp = await _login(store, sm, "alice", iv_hex, blob_hex)
    data = json.loads(sm4_cbc_dec(_NEW_KUSER, bytes.fromhex(resp.iv), bytes.fromhex(resp.blob)))
    assert data["rotate"] is False


async def test_old_key_past_grace_rejected_401():
    # 旧钥过宽限(now > k_prev_until)→ 401(这是轮换的安全边界:泄露的旧钥最多活到宽限尾)。
    sm = await _setup_rotated(prev_until=_T0 - 1)
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-expired"})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", iv_hex, blob_hex)
    assert ei.value.status_code == 401 and len(store) == 0


async def test_old_key_null_prev_until_rejected_401():
    # k_prev 在、k_prev_until 缺(脏行)→ fail-closed 拒(轮换总成对盖 prev+until,缺 until 不放行)。
    sm = await _setup_rotated(prev_until=_T0 + 1)
    async with sm() as s:
        async with s.begin():
            user = await s.get(User, 1)
            user.k_prev_until = None
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-dirty"})
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", iv_hex, blob_hex)
    assert ei.value.status_code == 401


async def test_retired_key_before_rotation_never_worked():
    # 无 k_prev 的账号(未轮换过)拿任意别的钥登录必 401——两次尝试不放大攻击面(第二把只在 prev 存在且宽限内才试)。
    sm = await _setup()  # alice 只有 k_cur=_KUSER
    store = SessionStore(_TTL)
    iv_hex, blob_hex = _make_blob({"password": _PW, "client_nonce": "n-x"}, key=_NEW_KUSER)  # 非登记钥
    with pytest.raises(HTTPException) as ei:
        await _login(store, sm, "alice", iv_hex, blob_hex)
    assert ei.value.status_code == 401
