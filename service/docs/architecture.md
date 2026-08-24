# 牌桌后端架构

适用范围:单进程、内网、在线玩家 ≤ 20、房间数极少。

> **筹码是积分(point),不是真实货币。**
>
> 全平台积分以内存为权威:进房(`JoinRoom`)时从 DB 载入一次,之后先改内存,DB 异步滞后落库。买入是从全局积分转入房间,离桌结算再转回。
>
> 正因为是积分而不是钱,才容得下崩溃窗口,「内存先生效、异步落库」才成立。

## 技术栈(固定)

FastAPI / asyncio · SQLModel + SQLAlchemy(async) · PostgreSQL(psycopg3) · Alembic · Poetry。

约定不变:WebSocket 入口走 FastAPI;DB 走 async SQLAlchemy;schema 迁移走 Alembic。

## 设计思路

单进程 asyncio 的根本矛盾是:协程会在 `await` 边界交错读写共享的房间状态。

对策是把状态变更与 IO 彻底分开:

- **状态变更纯同步**,中间没有 `await`,对事件循环天然原子,因此不需要锁。
- **所有命令经单一队列串行进入一个游戏循环**,命令之间不交错。
- **IO(WebSocket、DB)外移**到独立协程。游戏循环只把事件投递到队列,不在循环里 `await` IO。

内存中的 `world`,即全部房间加全局用户积分,是**权威状态**;DB 仅作持久层,允许滞后。

## 分层

两层:shell 管 IO,core 管规则。

```
shell(I/O、并发、生命周期)        ← 可以 async / await
  ├─ ws endpoint + ConnectionManager
  ├─ GameLoop(驱动 core)
  ├─ Sender / PersistWriter(delayDB)/ Timer
core(纯同步,游戏规则)            ← 禁止 async / await / I/O / DB / 读墙钟
  └─ reduce(work, cmd) -> (events, err)
```

core 不 import 任何 shell / FastAPI / SQLAlchemy 符号。两层只通过 `Command` 与 `Event` 这两个数据类型耦合。

## 协程构成

| 协程 | 数量 | 职责 | 让出点 |
|---|---|---|---|
| **GameLoop** | 1 | 唯一状态写者。一轮的流程是:`cmd = await inbox.get()` → 深拷贝工作副本 → `reduce` → 成功则 commit 回 world 并 `put_nowait` 派发事件;reduce 与派发全程无 `await` | 仅 `await inbox.get()` |
| **Receiver** | 每连接 1 | 收报文、转命令:`await ws.receive()` 后解析为 `Command`,再 `inbox.put`;它就是 FastAPI 的 ws handler。`JoinRoom` 载入之前另有一道载入屏障,先 `inbox.join()` 再 `barrier()`,等 GameLoop 排空并落库(0073) | `await ws.receive()` / `await inbox.put` |
| **Sender** | 每连接 1 | 发报文:从该连接的 outbound 队列取事件后 `await ws.send()`;单连接严格保序,并把慢客户端隔离开 | `await q.get()` / `await ws.send()` |
| **PersistWriter** | 1 | delayDB:周期 flush 写缓冲落库,分状态写覆盖与事件写追加两种语义(见 [db.md](db.md));它的等待可被 `barrier()` 提前唤醒(0073) | `await wait_for(_wake, flush 周期)` / `await commit()` |
| **Timer** | 1 | 行动超时、掉线清理:到点只往 `inbox` 投命令,不直接改状态 | `await asyncio.sleep` |

数据流:`Receiver → inbox → GameLoop → (outbound q → Sender) / (写缓冲 → PersistWriter)`。

除 GameLoop 调用的同步 `reduce` 外,任何协程都不得写 `world`。

**队列有界,满了是「卡住」信号而非常态。** 上限设得宽松,纯属防御。

- **outbound 队列满**,即 `put_nowait` 抛 `QueueFull`:说明该连接的 Sender 卡死了,客户端假死或极慢。处理是丢弃该连接并投 `Disconnect`,不阻塞 GameLoop;重连后由 `Personal(StateSnapshot)` 补回全量状态。
- **`inbox` 满**:说明 GameLoop 自己卡住了,某条 reduce 死循环或阻塞。这是 bug,落 CRITICAL 日志,按进程级故障对待,不做优雅处理。

是否单开协程或队列,只有一条判据:会不会卡住事件循环。

- 只有远端慢 IO 才解耦。符合的只有两处:`ws.send`(慢客户端能卡几秒)和 DB commit(涉及网络与锁)。所以只有 Sender 和 PersistWriter 两个 IO 协程。
- 本地快操作直接串行做,不套队列。比如写日志、Timer 的瞬时 dict 写——它们在单线程 asyncio 下同步调用本就原子。详见 [coding_principle.md](coding_principle.md)。

## 五种数据类型

| 角色 | 类型 | 是什么 |
|---|---|---|
| 状态 | **World** | 权威内存状态:全部房间 + 全局用户积分。房间是 `Room`/`Hand`/`Player`/`Seat`,全局用户积分放在 `users` 表 |
| reduce 输入 | **Command** | 流入 core 的意图:玩家动作 + 系统命令(超时、清理、连接、断开)。它是开放集合,可以继续加;每条命令只作用于一个房间 |
| reduce 输出·成功臂 | **Event** | 成功副作用,分 A 组与 B 组:A 组对外,即 `Broadcast`/`Personal`/`Persist`;B 组驱动 shell 内部的快设施 |
| reduce 输出·失败臂 | **Err** | 失败值:`ErrorCode` + `detail`,不含收件人(见 [error.md](error.md)) |
| wire | **Message** | 与前端约定的报文:`ClientMessage` 进 / `ServerMessage` 出;其中 `ErrorMessage` 是 `ServerMessage` 的一种 |

`reduce(work, cmd) -> (list[Event], Err | None)`:成功给 `(events, None)`,失败给 `([], Err)`,两条臂互斥。

```
ws ──ClientMessage──▶ Receiver ──Command──▶ inbox ──▶ reduce(work, Command)
                                                        ├─成功→ events → dispatch → Sender/PersistWriter/Timer
ws ◀──ServerMessage(含 ErrorMessage)── Sender ◀─────────┴─失败→ Err → GameLoop 转 ErrorMessage 回发发起人
```

边界澄清:

- **Command ≠ Message**:系统命令(`Timeout`/`Connect`/`Cleanup`)没有对应报文,由 Timer 或连接生命周期产生。
- **Event ≠ Message**:`Event` 是 core→shell 的内部信封;`Message` 是信封里发给客户端的信。
- **Err ≠ Message**:`Err` 是 core 的失败值,到 wire 才转成 `ErrorMessage`。
- **DB 模型不在其中**:落库 schema 由 PersistWriter 持有,core 不碰;`Persist` 的 payload 是唯一过桥点。

## 统一回滚:工作副本 commit-or-discard

本架构唯一的状态修改与回滚模型,所有命令走同一条路。「工作副本」= GameLoop 为当前这条命令深拷出来的临时状态。

1. GameLoop 处理一条命令时先对工作集深拷一份**工作副本**;因为「每条命令只作用于一个房间」,工作集 = 目标房间 + `users` 表。
2. `reduce` 只在工作副本上改。校验与修改可以穿插,失败就整份丢弃,不必先校验后改。
3. `reduce` 返回 `(events, err)`,然后分两条路:
   - 失败,或抛异常(归一为 `([], Err(INTERNAL))`)→ 丢弃副本,`world` 一字节未动,GameLoop 回发错误。
   - 成功 → `commit` 把副本装回 `world`,做法是替换引用;然后 dispatch events,只用 `put_nowait`。

几点说明:

- `checkout(world, cmd)` 与 `commit(world, work)` 是 `shell/world.py` 的模块级函数。`World` 是 core 的纯 dataclass(见 [models.md](models.md)),把「深拷 + 解析目标房 + 返回 shell 的 `Work`」挂成它的方法,会让 core 类型背上 shell 职责、反过来依赖 shell 的 `Work`,破坏分层。
- 失败安全 = 没 commit。积分和房间状态在同一份副本里,一起 commit 或一起丢弃。
- commit 替换引用,所以旧对象不会再被原地改;事件携带的对象在异步发送前不会被改写。
- 这只隔离逻辑异常。进程级崩溃仍然丢掉进行中的手牌,见「持久化与一致性」。

> 完整存储模型(内存权威、载入一次、`checkout`/`commit` 伪码与设计理由、大实体 `uRead`/`uWrite`、delayDB)见 **[storage.md](storage.md)**。

## Event 的类别

`reduce` 产出的 Event,按 dispatch 送去哪里分两组。

**A 组 · 对外 IO 事件**:走队列,交给慢 IO 协程。只有这三种,是封闭集合。

| Event | 目标 | 消费者 |
|---|---|---|
| `Broadcast(room, msg)` | 整房间广播 | 各连接 Sender 队列 |
| `Personal(nick, msg)` | 私发单个连接;典型用途是底牌,以及入桌或重连时的 `StateSnapshot` 全量快照。连接是全局按 nick 索引的,所以它不需要 room | 该连接 Sender 队列 |
| `Persist(payload)` | 持久化意图 | PersistWriter(delayDB) |

这三个变体就是 dispatch 唯一的路由依据。信封里直接装消费者要的数据,不套第二层:`msg` 就是 wire 的 `ServerMessage`,`payload` 就是 delayDB 要落的结构。

新增第四种对外副作用,意味着新增一个慢 IO 消费者协程,受「不过度解耦」约束(见 [coding_principle.md](coding_principle.md)),不能随手加。

**B 组 · 驱动 shell 内部快设施**:同步派发,不走队列。

目前只有 `TurnChanged` 与 `ClearAction`,指向 Timer 的同步方法(见 [timer.md](timer.md)):由 reduce 产出、由 dispatch 路由,但目标是本地快设施,所以同步调用、不进队列。

> **错误不在 Event 里。** 失败时 world 没动、零副作用;`Err` 是 reduce 的失败臂,由 GameLoop 转 `ErrorMessage` 回发发起人。

## 并发不变量(任何改动必须守住)

1. **core 纯同步、不阻塞、不靠墙钟做决策**:禁止 `async`/`await`、网络/文件/DB、`sleep`。
   - 判据一,会不会卡住事件循环——慢 IO 一律外移;判据二,会不会拿墙钟当游戏判据——超时与新鲜度只用单调自增的 `epoch`,不读 `time.time()`,以免 NTP 校时误触。
   - 非阻塞的本地计算不违反本条:`random.SystemRandom()` 洗牌可用。shell 盖好的时间戳也可以作为记录元数据穿过 core,它只存进 `Hand.start_time`,不参与任何分支。
   - 根本要求是 reduce 须「给定 `world+cmd` 可断言输出」,所以墙钟一律由 shell 盖,core 不主动读。
2. **`world` 只由 GameLoop 经「工作副本 commit」更新**:`reduce` 只改副本,其它协程只读已提交状态、不写。Receiver 允许读 DB(那是 shell IO),但它读 DB、不读 `world`——载入与否的决定权在 reduce(见 [user.md](user.md))。
3. **GameLoop 处理一条命令期间不 `await`**(派发只用 `put_nowait`)。
4. **对外发送只经 per-connection Sender 队列**:禁止 `create_task(ws.send())`,也禁止在别处直接 `ws.send()`。
5. **定时器、连接、断线一律转 `Command` 进 `inbox`**,不旁路改状态。
6. **错误是 reduce 的返回值(`Err`)**,不是 Event,也不用异常做控制流;失败时 `events` 为空、`world` 未动。
7. **事件不持有会被后续改写的引用**:跨命令的隔离由工作副本 commit 保证。剩下的要求只有一条——同一条 reduce 内,产出某个 event 之后别再改它引用的对象。事件一般在末尾构造,自然满足。
8. **每条命令只作用于一个房间**:工作副本与回滚都据此界定。
   - `JoinRoom` 的目标房写在命令里;其余命令的目标房是 `world.users[cmd.origin].room`,因为模型 2 下游戏命令不带 room、`origin` 就是发起人 nick。`checkout` 据此解析工作集。
   - 跨房间操作目前不支持,需要时另议。
9. **一个用户同一时刻只在一个房间**:`UserState.room` 记其所在房间。
   - 已在某房的人再 `JoinRoom` 到别房会被 reduce 拒掉,回 `ALREADY_IN_ROOM`,要先 `LeaveRoom`。见 [lobby.md](lobby.md)/[user.md](user.md)。
   - 因此全局积分的驱逐没有歧义:唯一那个房间的 `LeaveRoom`/`Cleanup` 就是彻底离场。

## 连接生命周期(全部走命令)

细节见 [connection.md](connection.md),这里只给全貌。

**连接**:ws 握手成功(鉴权见 [auth.md](auth.md))→ 建 outbound 队列、起 Sender → 投 `Connect(nick)` 接入**大厅**。连接绑 nick、不绑房间,这是模型 2。

reduce 按 world 的真相分三类处理:

- 在房 + `OFFLINE` → 重连恢复:`Broadcast(UserStatusChanged)` 加私发 `Personal(StateSnapshot)`。
- 在房 + 在线 → **顶替再连**,即新 ws 接管这个 nick、挤掉旧连接:只私发 `Personal(StateSnapshot)` 对齐,不改状态、不广播。
- 纯大厅 → core 无事可做。

进房与载入积分发生在 `JoinRoom`,见 [lobby.md](lobby.md) / [user.md](user.md)。

**断开**:ws 异常 → 投 `Disconnect(nick)`。命令不带 room,reduce 用 `world.users[nick].room` 解析。观战者即时离场(0070);在座者标记离线、保留座位。

**重连**:同一 nick 再次 `Connect`。reduce 取消其清理、恢复状态、补发 `Personal(StateSnapshot)`,并忽略本次从 DB 读到的值,因为内存比 DB 新。

**超时/清理**:Timer 到点投 `Timeout` / `Cleanup`,由 reduce 处理,包括自动 fold、退还筹码、释放座位。

「什么时候真正离场」「断线保留多久」都是 reduce 里的规则;shell 只负责把事件变成 IO 与计时。

## 持久化与一致性

- **统一范式**:凡需落库的数据都走「内存权威 + delayDB」——从 DB 读一次进内存,之后改内存,由 delayDB 异步追平 DB。当前全局积分是唯一持久化的内存实体;手牌结束时写手牌记录;room 状态目前不落库,需要时按同一范式接入。机制见 [db.md](db.md),模型见 [storage.md](storage.md)。
- **无并发写者**:PersistWriter 是唯一 DB 写者,所以不需要行锁或 `with_for_update`。原型里散落的行锁已随 0027 拆除原型时移除。
- **买入是纯内存转账**:reduce 在工作副本上校验积分,通过就改内存并产出 `Persist`。DB 不是买入的关卡——不存在「落库失败需回滚」,也没有 `BuyInFailed` 命令。落库失败只由 PersistWriter 重试并落 ERROR。
- **崩溃语义**:单进程,任何崩溃都带走全部内存状态。丢失的是进行中的手牌(已结束的已落库)和尚未 flush 的积分变更。积分不是货币,本规模直接接受;重启时从 DB 载入积分初值即可,无需对账。

## 客户端协议契约(单一事实源 + 代码生成)

wire 消息(`ServerMessage`/`ClientMessage`)只在后端 Pydantic 写一份,前端 TS 类型自动生成,不手写第二份。反例就在手边:[frontend/src/types/poker.ts](../../frontend/src/types/poker.ts) 的 `chips`/`phase` 已经和后端 enum 漂移,原因就是手写。

- 每条消息带一个 `type` 字面量,构成可辨识联合(discriminated union),Pydantic 与 TS 一一对应。
- 后端 Pydantic 是唯一事实源,TS 由 codegen 产出。ws 消息走自包含的 Python 生成器 [scripts/gen_wire_ts.py](../scripts/gen_wire_ts.py),不依赖 node;REST 走 OpenAPI 加 `openapi-typescript`(P7)。治理与缘由见 [wire.md](wire.md)。消息清单在 .py 及其生成产物里,不在文档里。
- 漂移守门是 `pytest` 里的 [tests/wire/test_codegen_uptodate.py](../tests/wire/test_codegen_uptodate.py):改了 `.py` 不重新生成就红。**仓库目前没有 CI,也没装 pre-commit**——提交规约写在 [dev.md](dev.md)「提交」,靠人执行;要不要自动化是独立决策(见 [BUGS.md](refactor/BUGS.md) DEBT-1)。前端只消费生成产物,禁止手写或手改。
- 多语言文案不在协议里:后端只回机器可读的 `code`(见 [error.md](error.md)),文案由前端按 `code` 映射。

> **传输层另有一层加密信封**:这里的 `ServerMessage`/`ClientMessage` 是明文 JSON;在无 TLS 的前提下,外面还套一层 SM4 + HMAC-SM3 + 序号的安全帧(见 [auth.md](auth.md))。codegen 只覆盖 JSON 这层;前端除消费生成的 JSON 类型外,还要自己实现帧的加解密。

## 错误处理(详见 [error.md](error.md))

错误是值不是异常:`reduce -> (events, Err | None)`。

- 业务校验失败:工作副本被丢弃,`world` 未动;GameLoop 把 `Err` 转成 `ErrorMessage` 回发命令发起人(`cmd.origin`),不广播。
- 协议/解析错误:发生在 Receiver 层,还没形成合法 `Command`。直接构造 `ErrorMessage` 投该连接的 Sender 队列,绕过 reduce,但不绕过保序。
- 未预期异常(bug):GameLoop 用 `try/except` 接住 → 以 `Err(INTERNAL)` 回发并 `log.exception`,然后继续处理下一条命令。**兜底罩住整条链**(checkout / reduce / commit / 派发),不只是 `reduce`——否则一次异常就杀掉唯一的状态写者协程,服务器从此不再处理任何命令(0083)。
  - 落点不同,处置也不同:崩在 commit **之前** = 工作副本被丢、`world` 一字节未动,正是 `INTERNAL` 的定义,照常回发;崩在 commit **之后** = `world` 已经改了,这时再回 `INTERNAL` 等于告诉客户端「什么都没发生」,还会诱导它重试而重复生效——改为落 CRITICAL 留人工介入,客户端的真相以后续 `StateSnapshot` 为准。
  - 派发**逐事件**兜:一个事件炸了不牵连同批其余事件。丢一条 `Persist` 是手牌记录永久丢失(写缓冲里只有 `put` 进去的),丢一条 `TurnChanged` 是 Timer 不装表、该行动的人能无限拖住整桌。
  - 三条常驻协程(GameLoop / Timer / PersistWriter)另挂 watchdog:非取消而退出即落 CRITICAL。「进程还在、ws 还连着,但状态机已经哑了」是最难察觉的故障(见 [log.md](log.md) 级别约定)。

## 定时器(详见 [timer.md](timer.md))

- **行动倒计时**:进入某玩家回合时由 reduce 产出事件,Timer 起倒计时;玩家行动后覆盖或取消;到点投 `Timeout`,reduce 校验仍是该回合后执行默认动作,能 check 则 check,否则 fold。
- **过期防护**:Timer 永远可能投出已经过期的命令。正确性靠 reduce 进门先做 staleness(过期)校验。判据是 `hand.epoch`,它是每次行动推进或街道切换时自增的内存计数,见 [core.md](core.md);`hand.epoch != cmd.epoch` 即视为过期、忽略。不引入基于 wall-clock 的 `hand_id`。

## 测试

- `reduce` 给定 `work + 命令序列`,断言改后的状态与产出的 `Event` 列表,不需要 DB 或 WebSocket。
- core 层单测要覆盖:边池、入局的付盲即玩(交了一个大盲,下一手就被发牌,不必等大盲位轮到自己)与等大盲、all-in、断线重连、超时默认动作。
- shell 只需少量集成测试,验证队列接线与保序。

## 优缺点

**优势**:

- 无锁原子:reduce 是同步的,不可打断。
- 单一序列化点,状态机易推理。
- 慢客户端与慢 DB 都被隔离。
- core 可以纯单测。

**缺点**:

- 全局串行:所有房间共用一个 GameLoop,存在跨房间的队头阻塞。本规模可忽略;房间多时改为每房间一个 GameLoop,core 不变。
- 单进程,不可水平扩展,崩溃会丢进行中的手牌。
- 最终一致:DB 落后内存。
- 保序靠纪律:违反不变量 4 就会乱序。
