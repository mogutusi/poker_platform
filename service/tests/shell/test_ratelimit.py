"""令牌桶限速(messaging.md / ratelimit):纯计算,注入单调 now 断言补充/扣减/封顶。"""

from app.shell.ratelimit import TokenBucket


def test_create_starts_full():
    b = TokenBucket.create(capacity=5, refill_per_sec=1, now=100.0)
    assert b.tokens == 5 and b.capacity == 5 and b.updated_at == 100.0


def test_burst_then_exhaust():
    b = TokenBucket.create(capacity=3, refill_per_sec=1, now=0.0)
    assert all(b.try_consume(now=0.0) for _ in range(3))  # 突发 3 条:满桶连发
    assert b.try_consume(now=0.0) is False  # 第 4 条:同一时刻无补充 → 拒
    assert b.tokens == 0


def test_steady_refill_recovers_one_per_sec():
    b = TokenBucket.create(capacity=3, refill_per_sec=1, now=0.0)
    for _ in range(3):
        b.try_consume(now=0.0)  # 耗空
    assert b.try_consume(now=0.5) is False  # 0.5s 补 0.5 < 1 → 仍拒
    assert b.try_consume(now=1.0) is True  # 1.0s 补满 1 个 → 过
    assert b.try_consume(now=1.0) is False  # 又耗空


def test_refill_caps_at_capacity():
    b = TokenBucket.create(capacity=2, refill_per_sec=10, now=0.0)
    b.try_consume(now=0.0)  # 用 1,剩 1
    b.try_consume(now=100.0)  # 长静默后补充封顶 capacity=2,扣 1 → 剩 1(不会涨到 1+1000)
    assert b.tokens == 1


def test_now_going_backwards_does_not_mint_tokens():
    b = TokenBucket.create(capacity=2, refill_per_sec=1, now=10.0)
    b.try_consume(now=10.0)  # 剩 1
    b.try_consume(now=5.0)  # now 倒退 → elapsed 视为 0,不凭空生令牌
    assert b.tokens == 0  # 剩 1 - 1 = 0,而非被倒退补回


def test_cost_greater_than_one():
    b = TokenBucket.create(capacity=5, refill_per_sec=1, now=0.0)
    assert b.try_consume(now=0.0, cost=3) is True and b.tokens == 2
    assert b.try_consume(now=0.0, cost=3) is False and b.tokens == 2  # 不够则不扣
