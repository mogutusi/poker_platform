# 测试策略

## 一句话

正确性主要靠 core 的纯单测,shell 只需少量集成测试验证接线与保序。

`reduce` 是同步纯函数:给定 `world + 命令序列`,断言改后状态和 `(events, err)`;它不碰 DB / WS / asyncio,单测不必起服务、不必有事件循环——架构本身就是为可测性设计的(见 [architecture.md](architecture.md))。

## 三层测试

| 层 | 测什么 | 需要 | 占比 |
|---|---|---|---|
| core 单测(主力) | `reduce` 的牌局规则、状态机、错误臂 | 纯同步,无 IO | 最大 |
| shell 集成 | 队列接线、保序、ConnectionManager 顶替(同一 nick 用新连接登录时旧连接被踢下线)、delayDB flush(内存攒下的写请求成批异步落库)、lifespan drain(进程关闭前把缓冲里没落库的写请求排干) | `pytest-asyncio` | 中 |
| crypto 单测 | `SecureChannel` 逐帧:MAC 拒伪、seq 拒重放、先验后解 | 纯同步 | 小但关键 |

工具 `pytest` + `pytest-asyncio`(`poetry add --group dev pytest pytest-asyncio`),目录建议 `tests/core/`、`tests/shell/`、`tests/crypto/`。跑法:工作目录切到 `service/`,命令一律走项目 venv,即 `poetry run` 或 `service/.venv/bin/<cmd>`;系统裸 `pytest` 没装依赖,跑不起来(细节见 [dev.md](dev.md))。

```bash
cd service
poetry run pytest                  # 全量
poetry run pytest tests/core -q    # 只跑 core 单测
```

## core 单测怎么写

形状固定:构造 `world` → 跑一条或一串命令 → 断言三样:① 改后 `world` 的关键字段;② `events` 列表;③ `err`。

```python
def test_preflop_bb_option():
    world = make_world(room_with(seats=[P("A",100), P("B",100), P("C",100)], button=0))
    world, events, err = run(world, StartHand(seat=0, started_at=T0, deck=DECK_FIXED))
    # ...UTG 跟、SB 补、轮到 BB 仍未关
    world, events, err = run(world, PlayerAction("C", BET, 2))   # UTG 跟
    ...
    assert acting(world) == "B"            # 大盲选择权:轮到 BB
```

两个关键 fixture:

- `make_world` / `room_with` / `P`:用具名 builder 拼局面,别手搓裸 dict(见 [coding_principle.md](coding_principle.md))。
- 固定牌堆:`StartHand(deck=...)` 注入确定牌堆,让「谁赢」可断言。core 唯一的不确定源是 `random.SystemRandom` 洗牌,它正是为此被外移成命令参数;测试传 `deck`,生产不传(见 [core.md](core.md))。时间戳同理由 shell 盖,core 不读墙钟,输出因此可断言。

断言不变量(测试期开 `assert`):

- 筹码守恒:`Σ Player.points + Σ bet_amount + Σ contributed == 开局锁入总额`;结算后 `Σ 还回 Seat.points == 同额`(见 [rules.md](rules.md) / [core.md](core.md))。
- 隐私:任何 `Broadcast`/`Persist` payload 不含 `hole_cards`/`deck`。只有 `Personal(HoleCards)` 与 `HandShowDown` 例外。

## 必须覆盖的清单(core)

牌局规则在 [rules.md](rules.md) 都给了带数字的用例,直接转成单测。

牌局规则:

- 盲注/位次:3 人/6 人定位、heads-up 特例、短码盲注 all-in、庄推进跳过非 ready。
- 入局/防躲盲:付盲即玩(中途入座者投一个大盲,下一手立刻被发牌,不必等到大盲位)、等大盲、换座/退房/坐出躲盲被堵、bootstrap、免盲投票(已入局玩家全票同意,免掉新人这次的入局盲;要覆盖全票、否决、蹭车被挡、投票人离场重算四种)。
- 下注轮关闭:preflop 大盲选择权、postflop 全 check、加注重开、min-raise 非法、短 all-in 不重开、all-in 超注重开、heads-up。
- 边池:单池、多档 all-in 边池、未叫注退还、弃牌者投入计入低池、奇数零头归属、全 all-in 跑公共牌、无摊牌结束。
- 中途:LeaveRoom 即时 fold + 手尾驱逐、SITTING_OUT 延到手尾、断线 vs 主动离开。

core 状态/存储(见 [user.md](user.md)/[storage.md](storage.md)):

- 买入是纯内存转账;积分不足回 `Err(INSUFFICIENT_POINTS)`,失败时丢弃工作副本(即本命令的状态草稿),`world` 未动。
- 单房间约束:`JoinRoom` 到别的房 → `ALREADY_IN_ROOM`。
- 驱逐时机:退分 `Persist` 之后才 `del`。
- 重连恢复:`Connect` 命中 `world.users` 里的 OFFLINE 记录 → 恢复 + `StateSnapshot`。
- timer staleness(「过期作废」):过期 `Timeout` 的 `epoch` 与当前不符,被忽略;`Cleanup` 仅在 OFFLINE 时才退筹。

## shell 集成(少量)

只验接线,不重测规则。

- 保序:同一连接 `outbound` 严格按 enqueue 顺序发出(Sender)。
- 顶替/身份判定:同 nick 新连接顶掉旧连接;旧连接退出时 `is_current=False`,因此不投 `Disconnect`(见 [connection.md](connection.md))。
- delayDB(见 [db.md](db.md)):`put` 分流(状态写同键覆盖,事件写追加)、`swap` 双缓冲(取走并清空)、失败 `requeue` 回灌按「更新者优先」即 `setdefault`、drain。
- 队列满:`outbound` 满 → 丢连接 + `Disconnect`,不阻塞 GameLoop。
- lifespan:关闭按序 drain,缓冲落净(见 [connection.md](connection.md))。

## crypto 单测(小而关键)

auth 最容易出问题(见 [auth.md](auth.md)),逐条覆盖:

- 正常帧:`seq‖iv‖ct‖mac` 往返解出原文。
- 篡改密文 → MAC 校验失败 → 拒;比对是常量时间的。
- 重放(seq ≤ last_seen)→ 拒。
- 先验 MAC 后解密:构造坏 padding 的未验帧,确认它在解密前就被 MAC 挡掉(防 padding-oracle)。
- IV 每帧随机,不复用。

## CI 钩子(随实现接)

- `pytest` 全部通过。
- wire codegen 校验:`.py` 改了必须重新生成 TS;产物与源不一致即 CI 失败(见 [wire.md](wire.md))。
- 迁移一致性:模型与已落迁移无未生成的 diff,用 `alembic check` 或等价手段(见 [dev.md](dev.md))。

## 约定(必须守住)

1. 每个规则用例对应 [rules.md](rules.md) 的编号;改规则先改测试。
2. shell 测试只验接线与保序,不重测 core 已覆盖的规则;core 单测不引 DB/WS/asyncio。
3. 测试全绿不等于可提交:push 前还要做对抗式复审,分工见 [review.md](review.md)。
