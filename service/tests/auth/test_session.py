# ws 会话表穷举(P5,见 docs/auth.md §登录握手 / changes/0055)。
# 时钟外移(now 显式传)→ 过期/清理逻辑可控测。覆盖:create(id≠token·每次异·exp·登记可查)/
# lookup(命中·未知·过期删)/ 多会话并存 / revoke(删·幂等)/ prune(清过期·不误清)/ token 形制 / config 接线。

from app import gameconfig
from app.auth.session import Session, SessionStore

_TTL = 3600  # 测试用会话有效期(秒)
_T0 = 1_000_000.0  # 测试基准墙钟(epoch 秒);相对它推进 now


def _store(ttl: int = _TTL) -> SessionStore:
    return SessionStore(ttl)


def test_create_returns_public_id_and_secret_token():
    store = _store()
    sid, session = store.create("alice_name", "alice", _T0)
    assert isinstance(sid, str) and sid  # 公开句柄,非空串
    assert isinstance(session.token, bytes) and len(session.token) == 32  # 秘密 32B 票据
    assert sid.encode() != session.token  # id 与 token 不同源
    assert session.name == "alice_name" and session.nickname == "alice"
    assert session.expires_at == _T0 + _TTL  # exp = now + ttl


def test_create_ids_and_tokens_unique():
    store = _store()
    ids, tokens = set(), set()
    for _ in range(50):
        sid, session = store.create("n", "nick", _T0)
        ids.add(sid)
        tokens.add(session.token)
    assert len(ids) == 50 and len(tokens) == 50  # 每次铸都新随机


def test_lookup_hits_before_expiry():
    store = _store()
    sid, session = store.create("n", "nick", _T0)
    assert store.lookup(sid, _T0) is session  # 同一对象
    assert store.lookup(sid, _T0 + _TTL - 1) is session  # exp 前一秒仍在


def test_lookup_unknown_returns_none():
    assert _store().lookup("no-such-sid", _T0) is None


def test_lookup_expired_returns_none_and_evicts():
    store = _store()
    sid, _ = store.create("n", "nick", _T0)
    assert store.lookup(sid, _T0 + _TTL) is None  # now >= exp → 失效(边界:恰好到点即拒)
    assert len(store) == 0  # 惰性清:过期行被删
    assert store.lookup(sid, _T0) is None  # 已删,再查也 None


def test_multiple_sessions_coexist_per_name():
    # 轮换靠新登录铸新会话(会话层不强制单例);同 name 多 sid 并存,各自独立可查。
    store = _store()
    sid1, s1 = store.create("n", "nick", _T0)
    sid2, s2 = store.create("n", "nick", _T0)
    assert sid1 != sid2 and s1.token != s2.token
    assert store.lookup(sid1, _T0) is s1 and store.lookup(sid2, _T0) is s2
    assert len(store) == 2


def test_revoke_removes_and_is_idempotent():
    store = _store()
    sid, _ = store.create("n", "nick", _T0)
    assert store.revoke(sid) is True  # 真的吊销了一条
    assert store.lookup(sid, _T0) is None
    assert store.revoke(sid) is False  # 幂等,未知 id 无害
    assert store.revoke("never-existed") is False


def test_revoke_kills_the_held_session_object():
    # 摘表项不够(0097):活 ws 连接持有的是 Session **对象**,每帧只比对 conn.session.expires_at
    # (receiver 收帧 / sender 出站),从不回头查表。只 pop 的话已经连着的人照样收发,而吊销要防的
    # 恰恰是「凭证已泄露、对方可能已连着」。所以 revoke 必须就地把对象判死,复用 0070 那条强制路径。
    store = _store()
    sid, session = store.create("n", "nick", _T0)
    store.revoke(sid)
    assert session.expires_at == 0.0  # 判死:任何持有该对象的连接下一帧即被 4401 关掉
    assert _T0 >= session.expires_at  # 以「过期」的形式表达,才走得进既有的兜底检查
    assert len(store) == 0  # 「摘表项」那一半:别只判死不摘,否则死会话一直占着表(revoke 是两件事)


def test_revoke_all_for_name_spares_current_and_other_accounts():
    # 改密自救:清该账号其它设备的会话,留下当前这个,不碰别人的。
    store = _store()
    keep, keep_session = store.create("alice", "Alice", _T0)
    other1, s1 = store.create("alice", "Alice", _T0)
    other2, s2 = store.create("alice", "Alice", _T0)
    bob, bob_session = store.create("bob", "Bob", _T0)

    assert store.revoke_all_for_name("alice", except_id=keep) == 2
    assert store.lookup(keep, _T0) is keep_session and keep_session.expires_at > _T0  # 自己还在
    assert store.lookup(other1, _T0) is None and store.lookup(other2, _T0) is None
    assert s1.expires_at == 0.0 and s2.expires_at == 0.0  # 那两台设备的活连接也会被踢
    assert store.lookup(bob, _T0) is bob_session and bob_session.expires_at > _T0  # 别人的会话不受牵连


def test_revoke_all_for_name_without_exception_clears_the_account():
    store = _store()
    sid_a, _ = store.create("alice", "Alice", _T0)
    sid_b, _ = store.create("alice", "Alice", _T0)
    assert store.revoke_all_for_name("alice") == 2  # 不留例外 → 全清
    assert store.lookup(sid_a, _T0) is None and store.lookup(sid_b, _T0) is None
    assert store.revoke_all_for_name("nobody") == 0  # 无此账号:0 条,不报错


def test_prune_clears_expired_only():
    store = _store()
    old_sid, _ = store.create("n", "nick", _T0)  # exp = _T0 + _TTL
    fresh_sid, _ = store.create("n", "nick", _T0 + _TTL - 1)  # old 尚未过期,create 的预扫(0070)不清它
    cleared = store.prune(_T0 + _TTL)  # now >= old.exp,< fresh.exp
    assert cleared == 1  # 只清过期的那条
    assert store.lookup(old_sid, _T0 + _TTL) is None
    assert store.lookup(fresh_sid, _T0 + _TTL) is not None
    assert len(store) == 1


def test_create_prunes_expired_sessions():
    # 0070:静默轮换抛弃的旧会话不会再被 lookup(惰性删够不着)→ create 预扫——
    # 每次登录清一遍过期会话,过期密钥不常驻内存(清扫频率 = 登录频率)。
    store = _store()
    store.create("n", "nick", _T0)
    store.create("n", "nick", _T0)  # 两条都 exp = _T0 + _TTL
    assert len(store) == 2
    store.create("n", "nick", _T0 + _TTL)  # 新登录:预扫清掉两条过期的
    assert len(store) == 1


def test_prune_empty_and_none_expired():
    store = _store()
    assert store.prune(_T0) == 0  # 空表
    store.create("n", "nick", _T0)
    assert store.prune(_T0) == 0  # 无过期
    assert len(store) == 1


def test_session_is_dataclass_with_expected_fields():
    s = Session(name="n", nickname="nick", token=b"\x00" * 32, expires_at=_T0)
    assert (s.name, s.nickname, s.token, s.expires_at) == ("n", "nick", b"\x00" * 32, _T0)


def test_repr_redacts_secret_token():
    # 脱敏红线(log.md):Session 的 repr 不泄 token(防误 print/log)。token 字段 repr=False。
    _, session = _store().create("n", "nick", _T0)
    text = repr(session)
    assert session.token.hex() not in text  # 秘密值(hex)不在 repr
    assert str(session.token) not in text  # 秘密值(bytes 字面)不在 repr
    assert "token" not in text  # repr=False → 字段整个不出现
    assert "nick" in text  # 非秘密字段仍在,repr 对调试仍有用


def test_wired_to_gameconfig_ttl():
    # 接线:SessionStore 用 gameconfig.SESSION_TTL_SECONDS 定 exp(端点真实用法)。
    ttl = gameconfig.SESSION_TTL_SECONDS
    assert isinstance(ttl, int) and ttl >= 60
    _, session = SessionStore(ttl).create("n", "nick", _T0)
    assert session.expires_at == _T0 + ttl


def test_rename_nickname_updates_all_sessions_of_account():
    # 改昵称联动(changes/0065):同账号全部会话(多设备)nickname 齐改;他账号不动;返回改动条数。
    store = SessionStore(ttl_seconds=3600)
    _, s1 = store.create("alice", "Alice", now=_T0)
    _, s2 = store.create("alice", "Alice", now=_T0)  # 第二设备
    _, other = store.create("bob", "Bob", now=_T0)
    assert store.rename_nickname("alice", "Neo") == 2
    assert s1.nickname == "Neo" and s2.nickname == "Neo"
    assert other.nickname == "Bob"  # 他账号不受影响
    assert store.rename_nickname("nobody", "X") == 0  # 无此账号:no-op
