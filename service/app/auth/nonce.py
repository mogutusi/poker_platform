# 登录 nonce 短窗去重(P5 登录重放守卫之一半,见 docs/auth.md §登录握手 / changes/0063;另一半是
# blob.ts 的 freshness 窗,两者相与:重放包要么 ts 过期、要么 nonce 撞库)。内存 shell 状态,活在
# make_login_router 内(单 create_app 单实例);进程重启清空 → freshness 窗内旧包可复活一次,记档接受。
# 时钟外移(同 SessionStore/ReplayWindow):now/ttl 逐调用传,无隐藏时钟依赖、过期逻辑可测。

class NonceCache:
    # (name, nonce) → 过期时刻;窗口内重复即重放。只收 authenticate 已验过的真凭证(伪造包灌不进来)。
    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], float] = {}  # (登录账号, client_nonce) → expires_at(epoch 秒)

    def check_and_add(self, name: str, nonce: str, now: float, ttl: float) -> bool:
        # 判定并登记:True = 首见(登记至 now+ttl);False = 窗口内重复(重放),不改状态。
        # 每次调用先惰性剪过期项(登录频率极低,全量扫代价可忽略;免长跑进程缓存无界长)。
        # 剪枝用严格大于:条目在 expires_at 当刻仍有效——闭掉「恰在到期瞬间重放」的边界(changes/0063)。
        expired = [key for key, exp in self._seen.items() if now > exp]
        for key in expired:
            del self._seen[key]
        key = (name, nonce)
        if key in self._seen:
            return False
        self._seen[key] = now + ttl
        return True

    def __len__(self) -> int:
        # 当前登记(含尚未剪的过期)条数;供测试/监控。
        return len(self._seen)
