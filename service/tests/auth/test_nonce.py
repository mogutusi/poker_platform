# NonceCache 穷举(P5 登录重放守卫,见 docs/auth.md §登录握手 / changes/0063)。
# 时钟外移(now/ttl 逐调用传)→ 过期/剪枝可确定断言。覆盖:首见收 / 窗口内重复拒 /
# 过期后同 nonce 可复用 / 跨 name 隔离 / 拒绝不改状态 / 惰性剪枝防无界长。

from app.auth.nonce import NonceCache

_T0 = 1_000.0
_TTL = 120.0


def test_first_seen_accepted_duplicate_rejected():
    c = NonceCache()
    assert c.check_and_add("alice", "n1", _T0, _TTL)
    assert not c.check_and_add("alice", "n1", _T0 + 1, _TTL)  # 窗口内重复 = 重放
    assert len(c) == 1  # 拒绝不新增条目


def test_expired_nonce_reusable_strictly_after_expiry():
    # 过期(now **严格大于** expires_at)后同 (name, nonce) 可再用;恰在到期瞬间条目仍有效
    # ——闭掉「恰到期瞬间重放」边界(changes/0063;freshness 窗对该瞬间可能仍放行)。
    c = NonceCache()
    assert c.check_and_add("alice", "n1", _T0, _TTL)
    assert not c.check_and_add("alice", "n1", _T0 + _TTL, _TTL)  # 恰到期:条目仍在 → 重复拒
    assert c.check_and_add("alice", "n1", _T0 + _TTL + 1, _TTL)  # 过期后:剪掉,视为首见


def test_names_isolated():
    # nonce 按 (name, nonce) 键:不同账号撞同一 nonce 串互不影响(客户端随机域独立)。
    c = NonceCache()
    assert c.check_and_add("alice", "n1", _T0, _TTL)
    assert c.check_and_add("bob", "n1", _T0, _TTL)
    assert not c.check_and_add("alice", "n1", _T0 + 1, _TTL)


def test_lazy_prune_bounds_size():
    # 每次调用剪过期项:长跑进程里缓存不无界长(登记 100 条 → 全过期后一次调用清空)。
    c = NonceCache()
    for i in range(100):
        assert c.check_and_add("alice", f"n{i}", _T0, _TTL)
    assert len(c) == 100
    assert c.check_and_add("alice", "fresh", _T0 + _TTL + 1, _TTL)  # 全过期 → 剪光 + 登记新条
    assert len(c) == 1


def test_reject_does_not_extend_entry_lifetime():
    # 重放尝试(拒绝路径)不改状态:条目过期时刻不被重放摸访问续命(否则攻击者可无限保活自己的键)。
    c = NonceCache()
    assert c.check_and_add("alice", "n1", _T0, _TTL)
    assert not c.check_and_add("alice", "n1", _T0 + _TTL - 1, _TTL)  # 窗内重放:拒,不续命
    assert c.check_and_add("alice", "n1", _T0 + _TTL + 1, _TTL)  # 原始过期时刻一到仍可剪(未被续到 +2TTL-1)
