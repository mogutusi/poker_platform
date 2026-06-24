# 令牌桶限速(见 messaging.md):shell 防滥用的纯计算工具,不进 world、不影响控制流。
# 单调时钟由调用方注入(同 timer.md:决策不读墙钟、用 time.monotonic),纯计算 → 可注入 now 单测。

from dataclasses import dataclass


@dataclass
class TokenBucket:
    capacity: float  # 桶容量 = 突发上限(静默后最多连发几个)
    refill_per_sec: float  # 稳态补充速率(每秒补几个令牌)
    tokens: float  # 当前可用令牌(create 时满桶)
    updated_at: float  # 上次补充的单调时刻(秒)

    @classmethod
    def create(cls, capacity: float, refill_per_sec: float, now: float) -> "TokenBucket":
        return cls(capacity=capacity, refill_per_sec=refill_per_sec, tokens=capacity, updated_at=now)

    def try_consume(self, now: float, cost: float = 1.0) -> bool:
        # 先按 elapsed 补令牌(封顶 capacity),再尝试扣 cost:够则扣 + 返 True,否则不扣 + 返 False。
        # now 倒退(单调钟一般不会)按 0 elapsed 处理,绝不凭空生令牌。
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        self.updated_at = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False
