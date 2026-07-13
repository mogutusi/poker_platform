# K_user 双钥轮换穷举(P5,见 docs/auth.md §K_user 每周轮换 / changes/0066)。
# 内存 sqlite + create_all;覆盖:rotate_kuser 原子搬移(cur→prev + 版本 +1 + 重排到期 + RETURNING 回版本)、
# 未发钥不可轮换、due 查询三分(到期/未到期/不排程)、rotate_due 批量幂等 + 单账号失败隔离(边轮边出)、
# issue_login 各臂(新建/复用 pre-P5 行/已启用拒/--reset 换代/nickname 属他人拒)、生成器形制(16B hex / 全新随机)。

import pytest
from sqlalchemy.pool import StaticPool

from app.auth import kuser
from app.auth.kuser import (
    SECONDS_PER_DAY,
    RotatedKey,
    RotationFailure,
    generate_kuser,
    generate_password,
    rotate_due,
    rotate_one,
)
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.db.queries import list_login_users, users_due_for_rotation
from app.db.user_writes import issue_login, rotate_kuser

_T0 = 1_000_000.0
_ROT_S = 7 * SECONDS_PER_DAY  # 轮换周期(秒;=KUSER_ROTATION_DAYS 基线 7 天)
_GRACE_S = 3 * SECONDS_PER_DAY  # 宽限期(秒;=KUSER_GRACE_DAYS 基线 3 天)


async def _setup(*users: User):
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            for u in users:
                s.add(u)
    return sm


def _issued(uid: int, name: str, *, until: float | None) -> User:
    # 已发钥账号(v1;until 控排程:<=now 到期 / >now 未到期 / None 不排程)。
    return User(id=uid, nickname=name.title(), points=0, name=name,
                hash_password="s$1$d", k_cur=f"{uid:02x}" * 16, k_cur_ver=1, k_cur_until=until)


async def _row(sm, uid: int) -> User:
    async with sm() as s:
        return await s.get(User, uid)


# ── rotate_kuser:原子搬移 ──

async def test_rotate_shifts_cur_to_prev_and_installs_new():
    sm = await _setup(_issued(1, "alice", until=_T0))
    old_key = f"{1:02x}" * 16
    assert await rotate_kuser(sm, 1, "ff" * 16, _T0, _ROT_S, _GRACE_S) == 2  # RETURNING 回新版本
    u = await _row(sm, 1)
    assert (u.k_prev, u.k_prev_ver, u.k_prev_until) == (old_key, 1, _T0 + _GRACE_S)  # 旧钥降位 + 宽限
    assert (u.k_cur, u.k_cur_ver, u.k_cur_until) == ("ff" * 16, 2, _T0 + _ROT_S)  # 新钥上位 + 重排到期
    assert u.hash_password == "s$1$d"  # 只动密钥列,口令不受影响


async def test_rotate_twice_drops_oldest_key():
    # 双钥只留两代:二轮后 v1 彻底出局(prev=v2),旧泄露钥的存活被钉死在「一轮 + 宽限」内。
    sm = await _setup(_issued(1, "alice", until=_T0))
    await rotate_kuser(sm, 1, "ee" * 16, _T0, _ROT_S, _GRACE_S)
    await rotate_kuser(sm, 1, "ff" * 16, _T0 + _ROT_S, _ROT_S, _GRACE_S)
    u = await _row(sm, 1)
    assert (u.k_cur, u.k_cur_ver) == ("ff" * 16, 3)
    assert (u.k_prev, u.k_prev_ver) == ("ee" * 16, 2)  # v1 不再存在于任何列


async def test_rotate_unissued_account_refused():
    # k_cur 为 NULL(有 name 未发钥)→ 轮换拒(轮换是「换」不是「发」);行原封。
    sm = await _setup(User(id=1, nickname="Bob", points=0, name="bob"))
    assert await rotate_kuser(sm, 1, "ff" * 16, _T0, _ROT_S, _GRACE_S) is None
    u = await _row(sm, 1)
    assert u.k_cur is None and u.k_prev is None and u.k_cur_ver is None


async def test_rotate_one_returns_key_and_version():
    sm = await _setup(_issued(1, "alice", until=_T0))
    result = await rotate_one(sm, 1, "alice", _T0, 7, 3)
    assert result is not None and result.name == "alice" and result.version == 2
    u = await _row(sm, 1)
    assert result.new_key_hex == u.k_cur  # 返回的正是落库新钥
    # 钉「天 → 秒」换算与参数序(rotation=7 天排程 / grace=3 天宽限):换算丢失或两参对调这里必红
    assert (u.k_cur_until, u.k_prev_until) == (_T0 + 7 * SECONDS_PER_DAY, _T0 + 3 * SECONDS_PER_DAY)
    assert "new_key_hex=<redacted>" in repr(result)  # repr 脱敏(密钥不进异常栈/调试输出)


async def test_rotate_dirty_null_ver_recounts_from_one():
    # 脏行兜底:k_cur 有、ver 丢(NULL)——SQL 里 NULL+1 = NULL 会静默抹版本,coalesce 使其从 0 起重计 → 1。
    sm = await _setup(User(id=1, nickname="Alice", points=0, name="alice",
                           hash_password="s$1$d", k_cur="ab" * 16))  # ver/until 均 NULL
    assert await rotate_kuser(sm, 1, "ff" * 16, _T0, _ROT_S, _GRACE_S) == 1
    u = await _row(sm, 1)
    assert u.k_cur_ver == 1 and u.k_prev_ver is None  # 新 ver=coalesce(NULL,0)+1;prev_ver 如实搬 NULL


async def test_rotate_one_unissued_returns_none():
    sm = await _setup(User(id=1, nickname="Bob", points=0, name="bob"))
    assert await rotate_one(sm, 1, "bob", _T0, 7, 3) is None


# ── due 查询与批量轮换 ──

async def test_due_query_three_way_split():
    # 到期(until<=now)选中;未到期不选;不排程(NULL)不选;未发钥(k_cur NULL)不选。
    sm = await _setup(
        _issued(1, "adue", until=_T0 - 1),
        _issued(2, "bfresh", until=_T0 + 1),
        _issued(3, "cunsched", until=None),
        User(id=4, nickname="Dbare", points=0, name="dbare"),  # 有 name 无钥
    )
    assert await users_due_for_rotation(sm, _T0) == [(1, "adue")]


async def test_rotate_due_rotates_only_due_and_is_idempotent():
    sm = await _setup(_issued(1, "adue", until=_T0), _issued(2, "bfresh", until=_T0 + _ROT_S))
    rotated = [r async for r in rotate_due(sm, _T0, 7, 3)]
    assert [r.name for r in rotated] == ["adue"]
    assert (await _row(sm, 2)).k_cur_ver == 1  # 未到期者不动
    assert [r async for r in rotate_due(sm, _T0, 7, 3)] == []  # 立即重跑:刚轮换者已排到 now+7d,无事(cron 幂等)


async def test_rotate_due_isolates_per_account_failure(monkeypatch):
    # 失败隔离(0066 自 review 抓修):第一个账号轮换抛异常 → 产出 RotationFailure 继续,第二个照常
    # 轮换且**在同一轮里就产出**(边轮边出;若攒到批尾才回,后续失败会吞掉已 commit 的密钥)。
    sm = await _setup(_issued(1, "adue", until=_T0), _issued(2, "bdue", until=_T0))
    real = rotate_kuser

    async def _flaky(sessionmaker, uid, *args, **kwargs):
        if uid == 1:
            raise RuntimeError("db hiccup")  # 模拟单账号事务瞬断(未 commit)
        return await real(sessionmaker, uid, *args, **kwargs)

    monkeypatch.setattr(kuser, "rotate_kuser", _flaky)
    items = [it async for it in rotate_due(sm, _T0, 7, 3)]
    assert [type(it) for it in items] == [RotationFailure, RotatedKey]
    assert items[0].name == "adue" and "db hiccup" in items[0].error
    assert items[1].name == "bdue" and items[1].version == 2  # 后续账号不被拖累
    assert (await _row(sm, 1)).k_cur_ver == 1  # 失败者未 commit,原封 → 下次 due 仍在,重跑即补
    assert (1, "adue") in await users_due_for_rotation(sm, _T0)


# ── issue_login:首发/补发 ──

async def test_issue_creates_new_row():
    sm = await _setup()
    assert await issue_login(sm, name="carol", nickname="Carol", password_hash="s$1$d",
                             key_hex="aa" * 16, now=_T0, rotation_seconds=_ROT_S, points=100,
                             reset=False) == (1, None)  # (新版本, 无拒因)
    users = await list_login_users(sm)
    assert len(users) == 1 and users[0].name == "carol" and users[0].k_cur_ver == 1
    u = await _row(sm, users[0].uid)
    assert (u.k_cur, u.k_cur_until, u.points) == ("aa" * 16, _T0 + _ROT_S, 100)
    assert u.k_prev is None


async def test_issue_enables_pre_p5_row_by_nickname():
    # 既有行(name=NULL,如原型期用户)按 nickname 复用:login-enable 不重置 points。
    sm = await _setup(User(id=7, nickname="Dave", points=777))
    assert await issue_login(sm, name="dave", nickname="Dave", password_hash="s$1$d",
                             key_hex="aa" * 16, now=_T0, rotation_seconds=_ROT_S, points=0,
                             reset=False) == (1, None)
    u = await _row(sm, 7)
    assert (u.name, u.k_cur, u.k_cur_ver, u.points) == ("dave", "aa" * 16, 1, 777)


async def test_issue_already_issued_refused_without_reset():
    sm = await _setup(_issued(1, "alice", until=_T0))
    version, refusal = await issue_login(sm, name="alice", nickname="Alice", password_hash="s$2$d",
                                         key_hex="bb" * 16, now=_T0, rotation_seconds=_ROT_S,
                                         points=0, reset=False)
    assert version is None and refusal is not None and "already issued" in refusal
    u = await _row(sm, 1)
    assert u.k_cur == f"{1:02x}" * 16 and u.hash_password == "s$1$d"  # 拒后原封


async def test_issue_reset_reissues_and_clears_prev():
    # --reset 补发(疑似泄露/丢失):版本换代(返回真实 v2,CLI 据此打印,不硬报 v1)、
    # 旧钥**不留宽限**(补发即强制换代,留旧钥反而留洞)。
    sm = await _setup(_issued(1, "alice", until=_T0))
    async with sm() as s:  # 先造出一个宽限中的 prev,验 reset 会清掉
        async with s.begin():
            u = await s.get(User, 1)
            u.k_prev, u.k_prev_ver, u.k_prev_until = "cc" * 16, 0, _T0 + _GRACE_S
    assert await issue_login(sm, name="alice", nickname="Alice", password_hash="s$2$d",
                             key_hex="bb" * 16, now=_T0, rotation_seconds=_ROT_S, points=0,
                             reset=True) == (2, None)
    u = await _row(sm, 1)
    assert (u.k_cur, u.k_cur_ver, u.hash_password) == ("bb" * 16, 2, "s$2$d")
    assert u.k_prev is None and u.k_prev_ver is None and u.k_prev_until is None


async def test_issue_nickname_owned_by_other_account_refused():
    # nickname 已属别的账号(name 非 NULL)→ 拒(不劫持他人游戏身份)。
    sm = await _setup(_issued(1, "alice", until=None))
    version, refusal = await issue_login(sm, name="alice2", nickname="Alice", password_hash="s$1$d",
                                         key_hex="bb" * 16, now=_T0, rotation_seconds=_ROT_S,
                                         points=0, reset=False)
    assert version is None and refusal is not None and "belongs to account" in refusal


# ── 生成器形制 ──

def test_generate_kuser_shape_and_freshness():
    keys = {generate_kuser() for _ in range(32)}
    assert len(keys) == 32  # CSPRNG 全新随机,批量不重
    assert all(len(k) == 32 and bytes.fromhex(k) for k in keys)  # 32 hex = 16B(SM4 128-bit)


def test_generate_password_shape_and_freshness():
    pws = {generate_password() for _ in range(32)}
    assert len(pws) == 32
    assert all(len(p) >= 12 for p in pws)  # 高熵(12B urlsafe ≈ 16 字符)


# ── list 记账视图 ──

async def test_list_login_users_no_key_material():
    # list 投影不带任何键材料(LoginUserMeta 字段封闭);排序按 name 稳定。
    sm = await _setup(_issued(2, "bob", until=_T0), _issued(1, "alice", until=None))
    rows = await list_login_users(sm)
    assert [r.name for r in rows] == ["alice", "bob"]
    assert not any(hasattr(r, f) for r in rows for f in ("k_cur", "k_prev", "hash_password"))
    assert rows[0].k_cur_until is None and rows[1].k_cur_until == _T0
