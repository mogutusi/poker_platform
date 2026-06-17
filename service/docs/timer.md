# Timer 模块

定时器分**两层**,各管一件事,时长一短一长,叠加起来天然实现掉线重连:

| 层 | 常量 | 时长 | 谁驱动 | 到期动作 |
|---|---|---|---|---|
| **行动倒计时**(游戏层) | `ACTION_TIMEOUT` | 短(~15s) | reduce → dispatch | 投 `Timeout(nick, epoch)`,reduce 执行默认动作(能 check 则 check,否则 fold) |
| **在线保活 / 占座**(连接层) | `LIVENESS_TIMEOUT` | 长(~90s) | **Receiver** | 投 `Cleanup(nick)`,reduce 退还筹码、释放座位、广播离场 |

务必 `ACTION_TIMEOUT ≪ LIVENESS_TIMEOUT`:前者让掉线者**不卡牌局**(自动 fold,桌子继续转),后者给**重连留窗口**(座位筹码先留着)。两层一叠加,掉线重连不需要单独写逻辑(见「三条流程」)。

## 与架构的契约(必须守住)

Timer 是 shell 协程,**只做三件事**:维护两张「到期时刻」表 → 周期扫描 → 把过期项变成 `Command` 投进 `inbox`。

1. **绝不直接改 `world`、绝不直接 `ws.send`**;一切经 `inbox`,由 reduce 决定后果(不变量 2/5)。
2. **决策用的时刻只活在 Timer 内(shell),绝不进 `world`。** 到期时刻、保活时刻是 Timer 私有;游戏新鲜度判据一律用单调 `epoch`,不用墙钟。**唯一例外**:`Hand.start_time` 是 shell 盖好、塞进 `world` 的**记录元数据**,core 只存不读、绝不据它分支(见 [core.md](core.md) 的「墙钟外移」),不破坏 core 确定性。
3. **取消 = 隐式**,不设 `CancelTimer`。过期命令一律由 reduce 的 **staleness 校验**挡掉(见下)——这是正确性的最后防线。
4. 用**单调时钟** `loop.time()` / `time.monotonic()`,不用墙钟,避免 NTP 校时提前/延后触发。

## 谁驱动哪一层

- **行动倒计时由 reduce 驱动**:「该给谁起倒计时、起多久」是游戏状态转移的结果——只有 reduce 知道下家是谁、手牌是否结束。Receiver 不掌握「轮到谁」。
- **在线保活由 Receiver 驱动**:「这条连接还活着吗」是 Receiver 第一手知道的(它正在收这条连接的消息),收到任意消息就给该玩家续命。**模型 2 下连接只绑 nick**(见 [connection.md](connection.md)),Receiver 既不知道也不准读 `world` 里的房间(不变量 2),所以**保活表只能按 nick 单键**;`Cleanup(nick)` 进 reduce 后才由 `world.users[nick].room` 解析目标房(在房才退筹释座,在大厅则 no-op)。`_action` 仍按 room 键——它由 reduce 经 `TurnChanged(room,…)` 驱动,reduce 知道房间。

两层都**通过 Timer 的公共方法**操作(封装),无需队列/锁:这些方法是瞬时 dict 写、无 IO,单线程 asyncio 下同步调用本就原子(无 `await`,不与 `run()` 扫描交错)。对比 Sender / PersistWriter 用队列,是因为它们要做慢 IO;Timer 没这问题。

## 数据结构 & 公共接口

```python
def now() -> float:
    return asyncio.get_event_loop().time()   # 单调时钟

@dataclass
class _ActionDeadline:
    nickname: str
    epoch: int          # 回合新鲜度判据 = core.md 的 hand.epoch(每次行动推进/街道切换自增),防误触
    fire_at: float

class Timer:
    TICK = gameconfig.TIMER_TICK_MS / 1000   # 扫描周期;超时最多迟一个 TICK
    def __init__(self, inbox: "asyncio.Queue[Command]"):
        self._inbox = inbox
        self._action: dict[str, _ActionDeadline] = {}    # room -> 当前行动倒计时(每房间至多一人行动;room 由 reduce 经 TurnChanged 给)
        self._liveness: dict[str, float] = {}            # nick -> 到期时刻(按 nick 单键,见下)

    # ── 游戏层:由 GameLoop.dispatch 调用(reduce 产出 TurnChanged / ClearAction)──
    def on_turn_changed(self, room, nickname, epoch, timeout_s=None):
        s = gameconfig.ACTION_TIMEOUT if timeout_s is None else timeout_s
        self._action[room] = _ActionDeadline(nickname, epoch, now() + s)   # 同房间覆盖 = 取消上一回合

    def clear_action(self, room):
        self._action.pop(room, None)

    # ── 连接层:由 Receiver 调用(Receiver 只知 nick、不知房间,也不读 world)──
    def heartbeat(self, nickname):
        self._liveness[nickname] = now() + gameconfig.LIVENESS_TIMEOUT

    def drop_liveness(self, nickname):
        self._liveness.pop(nickname, None)
```

> 配套事件(属 [architecture.md](architecture.md) **Event B 组**:同步派发、不走队列),由 dispatch 路由到上面方法:
> `TurnChanged(room, epoch, acting_nick, timeout_s)` → `on_turn_changed`;`ClearAction(room)` → `clear_action`。

## tick 主循环(唯一让出点 `asyncio.sleep`)

```python
    async def run(self):
        while True:
            await asyncio.sleep(self.TICK)
            t = now()
            for room, d in list(self._action.items()):
                if t >= d.fire_at:
                    self._inbox.put_nowait(Timeout(nickname=d.nickname, epoch=d.epoch))   # 模型 2:不带 room,reduce 用 world.users[nick].room 解析
                    del self._action[room]                    # 一次性,触发即删
            for nick, fire_at in list(self._liveness.items()):
                if t >= fire_at:
                    self._inbox.put_nowait(Cleanup(nickname=nick))   # 模型 2:不带 room,reduce 用 world.users[nick].room 解析
                    del self._liveness[nick]
```

## staleness 校验(取消机制的真正落点,写在 reduce 里)

Timer 永远可能投出已过期的命令(玩家刚好在最后一刻行动 / 重连)。**正确性不靠取消,而靠 reduce 进门先校验是否仍新鲜:**

```python
# reduce 处理 Timeout:回合是否还停在当初那个点?
if room.hand is None or not is_still_acting(room.hand, cmd.nickname, cmd.epoch):
    return [], None                 # 回合早已推进 → 过期,忽略
# 仍是该回合该玩家 → 执行默认动作(能 check 则 check,否则 fold)

# reduce 处理 Cleanup:仍离线才退筹释座
if room.users_in_room.get(cmd.nickname) is not UserStatus.OFFLINE:
    return [], None                 # 已重连 → 忽略
```

> **新鲜度判据已定:`hand.epoch`**(见 [core.md](core.md) 「手牌标识与 staleness」)——每次行动推进 / 街道切换自增的内存计数,**不引入 wall-clock 的 `hand_id`**。机制:Timer 投 `Timeout` 时带上调度时的 `epoch`,reduce 进门比对 `hand.epoch != cmd.epoch` 即过期、忽略。

## 三条流程

**行动倒计时**
1. reduce 推进到玩家 A 的回合,`epoch += 1`,产出 `TurnChanged(room, epoch, "A", 15)`。
2. dispatch 调 `on_turn_changed`,记下 `fire_at`(覆盖上一回合)。
3. A 在 15s 内行动 → reduce 再次推进 → 新 `TurnChanged` 覆盖旧 deadline;旧的即便漏触发,`Timeout` 也因 `epoch` 不符被忽略。
4. A 超时未动 → 投 `Timeout` → reduce 校验仍是该回合 → 执行默认动作。

**掉线 → 占座 → 清理**
1. 连接期间 Receiver 每收一条消息(含 ping)就 `heartbeat`,保活时刻不断后移。
2. ws 断开:Receiver 停止 `heartbeat`,投 `Disconnect(nick)`。
3. reduce 标记 `OFFLINE`、广播、**保留座位**(清理由保活到期负责)。
4. 期间若轮到该玩家,行动倒计时(短)先触发 → 自动 fold,牌局不卡。
5. 距最后一条消息超 `LIVENESS_TIMEOUT` → 投 `Cleanup` → reduce 校验仍 `OFFLINE` → 真正退筹释座。

**重连**(隐式取消清理)
1. 同一 nick 在保活窗口内重新连上、发消息 → Receiver `heartbeat` 续命 + 投 `Connect`。
2. reduce 把状态从 `OFFLINE` 改回(恢复座位),私发 `Personal(StateSnapshot)` 让客户端一次对齐全量桌面(含自己的底牌)。
3. 即便此前 `Cleanup` 已投,reduce 见状态已非 `OFFLINE` → 忽略。座位筹码安然无恙。

## 注意点

- **在线但沉默会被误判掉线**:保活靠「收到消息续命」,所以纯观战、不操作的玩家也必须周期性 **ping**(或用 WS 协议层 ping/pong),**ping 间隔须 < `LIVENESS_TIMEOUT`**。
- **封装**:`_action` / `_liveness` 私有,外部只经公共方法,不直接戳内部 dict。
- **单调时钟 / 一次性触发**:fire 后立刻从表删,避免重复投。
- **精度 = `TICK`**:0.5~1s 足够,到点最多迟一个 tick,打牌无感。
- **配置驱动**:`ACTION_TIMEOUT` / `LIVENESS_TIMEOUT` / `TIMER_TICK_MS` 走 [config.md](config.md),不硬编码。
