# Timer 模块

定时器分两层,时长一短一长。两层叠加起来天然实现掉线重连(见「三条流程」),不需要单独的重连逻辑。

| 层 | 常量 | 时长 | 谁驱动 | 到期动作 |
|---|---|---|---|---|
| **行动倒计时**(游戏层) | `ACTION_TIMEOUT` | 短(~15s) | reduce → dispatch | 投 `Timeout(nick, room, hand_seq, epoch)`,reduce 执行默认动作:能 check 则 check,否则 fold |
| **断线占座窗口**(连接层) | `LIVENESS_TIMEOUT` | 长(~90s) | 断线装表 / 重连拆表(0070) | 投 `Cleanup(nick)`,reduce 退还筹码、释放座位、广播离场 |

务必 `ACTION_TIMEOUT ≪ LIVENESS_TIMEOUT`。前者让掉线者不卡牌局(自动 fold),后者给重连留窗口(座位筹码先留着)。

## 与架构的契约(必须守住)

Timer 是 shell 协程,只做三件事:维护两张「到期时刻」表 → 周期扫描 → 把过期项变成 `Command` 投进 `inbox`。

1. 绝不直接改 `world`、绝不直接 `ws.send`;一切经 `inbox`,由 reduce 决定后果(不变量 2/5)。
2. **决策用的时刻只活在 Timer 内(shell),不进 `world`。** 游戏新鲜度判据一律用单调 `epoch`,不用墙钟。唯一例外:`Hand.start_time` 是 shell 盖好、塞进 `world` 的记录元数据,core 只存不读、不据它分支(见 [core.md](core.md)「墙钟外移」)。
3. **取消 = 隐式**,不设 `CancelTimer`。过期命令由 reduce 的 staleness 校验挡掉(见下),这是正确性的最后防线。
4. 用单调时钟 `loop.time()` / `time.monotonic()`,不用墙钟,避免 NTP 校时导致提前/延后触发。

## 谁驱动哪一层

**行动倒计时由 reduce 驱动**

只有 reduce 知道下家是谁、手牌是否结束;Receiver 不掌握「轮到谁」。`_action` 按 room 键,由 reduce 经 `TurnChanged(room,…)` 驱动。

**断线占座窗口由连接生命周期驱动(0070 重设计)**

- 「谁掉线了」由传输层权威判定,本表不做探活,只回答一个问题:「已断线的人座位再留多久」。正常断开时 Receiver 的 `receive` 立刻报错。
- 拔电/NAT 失效的死连接由 ws 协议级 ping/pong 兜住:uvicorn+websockets 默认 20s ping / 20s 超时,死连接 ≤~40s 变成正常断线。浏览器自动回 pong,客户端零实现。
- 装表/拆表规则:凡投 `Disconnect` 处必 `arm_cleanup`,共两处——Receiver 退出清理、`dispatch._drop_connection`;新连接接入时 `cancel_cleanup`。
- 在线用户不进表。所以纯观战者静默任意久也不会空触发,也没有「触发即删后断线漏清」的坑(0070 修复的 A1)。
- 模型 2(连接只绑 nick、不绑房间)下本表按 nick 单键。`Cleanup(nick)` 进 reduce 后才由 `world.users[nick].room` 解析目标房。

> 历史:0018–0069 间本层语义是「收到任意帧续命」并要求客户端周期 ping。该要求从未进协议,且「触发即删 + 断线不重装」使断线清理实际失效(0070 审计 A1)。现语义不需要任何客户端心跳。

**为什么两层都不需要队列/锁**

两层都经 Timer 的公共方法操作:瞬时 dict 写、无 IO、方法内无 `await`,单线程 asyncio 下同步调用本就原子,不会与 `run()` 的扫描交错。Sender / PersistWriter 用队列是因为它们要做慢 IO。

## 数据结构 & 公共接口

```python
def now() -> float:
    return asyncio.get_event_loop().time()   # 单调时钟

@dataclass
class _ActionDeadline:
    nickname: str
    hand_seq: int       # 这一手的房内单调号 = hand.seq;与 room/epoch 一起构成 Timeout 的身份(0090)
    epoch: int          # 回合新鲜度判据 = core.md 的 hand.epoch(每次行动推进/街道切换自增),防误触
    fire_at: float

class Timer:
    TICK = gameconfig.TIMER_TICK_MS / 1000   # 扫描周期;超时最多迟一个 TICK
    def __init__(self, inbox: "asyncio.Queue[Command]"):
        self._inbox = inbox
        self._action: dict[str, _ActionDeadline] = {}    # room -> 当前行动倒计时(每房间至多一人行动)
        self._liveness: dict[str, float] = {}            # nick -> 到期时刻(按 nick 单键,见上)

    # ── 游戏层:由 GameLoop.dispatch 调用(reduce 产出 TurnChanged / ClearAction)──
    def on_turn_changed(self, room, nickname, hand_seq, epoch, timeout_s=None):
        s = gameconfig.ACTION_TIMEOUT if timeout_s is None else timeout_s
        self._action[room] = _ActionDeadline(nickname, hand_seq, epoch, now() + s)   # 同房间覆盖 = 取消上一回合

    def clear_action(self, room):
        self._action.pop(room, None)

    # ── 连接层:断线装表 / 重连拆表(0070;调用方只知 nick、不读 world)──
    def arm_cleanup(self, nickname):        # 断线时刻起算占座窗口(Receiver 退出 / dispatch 踢慢客户端)
        self._liveness[nickname] = now() + gameconfig.LIVENESS_TIMEOUT

    def cancel_cleanup(self, nickname):     # 窗口内重连/顶替:拆表(竞态漏拆由 reduce OFFLINE staleness 兜)
        self._liveness.pop(nickname, None)
```

配套事件属 [architecture.md](architecture.md) 的 Event B 组:同步派发、不走队列。dispatch 把 `TurnChanged(room, acting_nick, hand_seq, epoch)` 路由到 `on_turn_changed`,把 `ClearAction(room)` 路由到 `clear_action`。

事件不带 `timeout_s`,字段以 [events.py](../app/core/events.py) 为准。原因:core 不读配置,时长由 Timer 自己取 `gameconfig.ACTION_TIMEOUT`。`timeout_s` 留参仅作可选覆盖。

## tick 主循环(唯一让出点 `asyncio.sleep`)

```python
    async def run(self):
        while True:
            await asyncio.sleep(self.TICK)
            t = now()
            for room, d in list(self._action.items()):
                if t >= d.fire_at:
                    self._inbox.put_nowait(Timeout(nickname=d.nickname, room=room, hand_seq=d.hand_seq, epoch=d.epoch))
                    del self._action[room]                    # 一次性,触发即删
            for nick, fire_at in list(self._liveness.items()):
                if t >= fire_at:
                    self._inbox.put_nowait(Cleanup(nickname=nick))   # 同上,不带 room
                    del self._liveness[nick]
```

## staleness 校验(取消机制的真正落点,写在 reduce 里)

staleness = 「这条命令还新鲜吗」。Timer 永远可能投出已过期的命令(玩家刚好在最后一刻行动、或刚好重连上),所以正确性不靠取消,而靠 reduce 进门先校验:

```python
# reduce 处理 Timeout:这条队是为「哪一手的哪一回合」排的?三项全等才算新鲜
if room.hand is None or work.room_name != cmd.room or room.hand.seq != cmd.hand_seq \
        or not is_still_acting(room.hand, cmd.nickname, cmd.epoch):
    return [], None                 # 换房 / 换手 / 回合已推进 → 过期,忽略
# 仍是该回合该玩家 → 执行默认动作(能 check 则 check,否则 fold)

# reduce 处理 Cleanup:仍离线才退筹释座
if room.users_in_room.get(cmd.nickname) is not UserStatus.OFFLINE:
    return [], None                 # 已重连 → 忽略
```

**新鲜度判据是三元身份 `(room, hand_seq, epoch)`**,全是内存里的单调量,不引入 wall-clock 的 `hand_id`(0090)。三项缺一不可:

| 判据 | 挡什么 | 为什么单靠别的挡不住 |
|---|---|---|
| `epoch`(= `hand.epoch`) | 本手内回合已推进 | —— |
| `hand_seq`(= `hand.seq`,房内单调) | **跨手撞号** | `epoch` 每手从 0 起,「上一手的第 N 回合」和「这一手的第 N 回合」长得一样(BUG-3) |
| `room` | **跨房撞号** | `seq` 只在房内单调,两个房的第 1 手同为 `seq=1`;而 `checkout` 按「他**现在**在哪」解析目标房,人换了房陈旧命令就落进新房(0072·N4) |

> **`Timeout.room` 不是路由字段。** 目标房照旧由 `world.users[nick].room` 推定(硬规则 8 不变),命令自报的 `room` 只参与**校验**:两者不符即忽略。这是对规则 8 的澄清,不是给它开第二个例外。

> **`Cleanup` 不需要同款身份。** 它同样按 nick 解析房,但判据是「仍 `OFFLINE`」——人若已经换到别的房,必然是在线的,陈旧 `Cleanup` 天然被挡住。别「顺手对齐」给它加字段。

## 三条流程

**行动倒计时**

1. reduce 推进到玩家 A 的回合,`epoch += 1`,产出 `TurnChanged(room, "A", hand_seq, epoch)`。
2. dispatch 调 `on_turn_changed`,记下 `fire_at`(覆盖上一回合)。
3. A 在 15s 内行动 → 新 `TurnChanged` 覆盖旧 deadline;旧的即便漏触发,`Timeout` 也因身份不符被忽略。
4. A 超时未动 → 投 `Timeout` → reduce 校验仍是该回合 → 执行默认动作。

**掉线 → 占座 → 清理**(0070 语义)

1. ws 断开:正常断开立即,死连接由协议级 ping 在 ≤~40s 内判死。Receiver 退出清理 → `arm_cleanup(nick)` + 投 `Disconnect(nick)`。
2. reduce 分两类处理。观战者即时离场(无座无筹码,重进零成本;末人离房则销房);在座者标 `OFFLINE`、广播、保留座位。
3. 期间若轮到该玩家,行动倒计时先触发 → 自动 fold,牌局不卡。
4. 断线起 `LIVENESS_TIMEOUT` 满 → 投 `Cleanup` → reduce 校验仍 `OFFLINE` → 真正退筹释座。观战者已在步 2 离场,`Cleanup` 见其不在房,no-op。

**重连**(拆表 + 快照对齐)

1. 同一 nick 在占座窗口内重新连上 → Receiver 接入时 `cancel_cleanup` 拆表 + 投 `Connect`。
2. reduce 把状态从 `OFFLINE` 改回(恢复座位),私发 `Personal(StateSnapshot)` 一次对齐全量桌面,含自己的底牌。
3. 即便拆表前 `Cleanup` 已投(竞态),reduce 见状态已非 `OFFLINE` → 忽略,座位筹码不受影响。

## 注意点

- **精度 = `TICK`**:0.5~1s 足够,到点最多迟一个 tick,打牌无感。
- **配置驱动**:`ACTION_TIMEOUT` / `LIVENESS_TIMEOUT` / `TIMER_TICK_MS` 走 [config.md](config.md),不硬编码。
- `_action` / `_liveness` 私有,外部只经公共方法;fire 后立刻从表删(一次性),避免重复投。
