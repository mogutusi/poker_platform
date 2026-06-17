# 牌桌后端架构

适用范围:单进程、内网、在线玩家 ≤ 20、房间数极少。

> **筹码是积分(point),不是真实货币。** 全平台积分以内存为权威(登录时从 DB 载入一次,之后内存先生效、DB 异步滞后落库),买入即从全局积分转入房间、离桌结算转回。因为是积分而非货币,对一致性与崩溃窗口的容忍度高——这是"内存先生效、异步落库"的前提。

## 技术栈(固定)

FastAPI / asyncio · SQLModel + SQLAlchemy(async) · PostgreSQL(psycopg3) · Alembic · Poetry。
约定不变:WebSocket 入口走 FastAPI;DB 走 async SQLAlchemy;schema 迁移走 Alembic。

## 设计思路

单进程 asyncio,根本矛盾是协程在 `await` 边界交错读写共享的房间状态。对策是把状态变更与 IO 彻底分开:

- **状态变更纯同步**,中间无 `await` ⇒ 对事件循环天然原子,无需锁。
- **所有命令经单一队列串行进入一个游戏循环**,命令之间不交错。
- **IO(WebSocket、DB)外移**到独立协程,游戏循环只把事件投递到队列,绝不在循环里 `await` IO。

内存中的 `world`(全部房间 + 全局用户积分)是**权威状态**;DB 仅作持久层,允许滞后。

## 分层

```
shell(I/O、并发、生命周期)        ← 可以 async / await
  ├─ ws endpoint + ConnectionManager
  ├─ GameLoop(驱动 core)
  ├─ Sender / PersistWriter(delayDB)/ Timer
core(纯同步,游戏规则)            ← 禁止 async / await / I/O / DB / 读墙钟
  └─ reduce(work, cmd) -> (events, err)
```

core 不 import 任何 shell / FastAPI / SQLAlchemy 符号。两层只通过 `Command` / `Event` 两个数据类型耦合。

## 协程构成

| 协程 | 数量 | 职责 | 让出点 |
|---|---|---|---|
| **GameLoop** | 1 | 唯一状态写者:`cmd = await inbox.get()` → 深拷贝工作副本 → `reduce` → 成功 commit 回 world 并 `put_nowait` 派发事件 | **仅** `await inbox.get()`;reduce + 派发全程无 `await` |
| **Receiver** | 每连接 1 | `await ws.receive()`,解析为 `Command`,`inbox.put`。即 FastAPI 的 ws handler | `await ws.receive()` / `await inbox.put` |
| **Sender** | 每连接 1 | 从该连接 outbound 队列取事件 `await ws.send()`;保证单连接**严格保序**、隔离慢客户端 | `await q.get()` / `await ws.send()` |
| **PersistWriter** | 1 | delayDB:周期 flush 写缓冲落库(状态写覆盖、事件写追加,见 [db.md](db.md)) | `await asyncio.sleep`(flush 周期)/ `await commit()` |
| **Timer** | 1 | 行动超时、掉线清理;到点只往 `inbox` 投命令,绝不直接改状态 | `await asyncio.sleep` |

数据流:`Receiver → inbox → GameLoop → (outbound q → Sender) / (写缓冲 → PersistWriter)`。
除 GameLoop 调用的同步 `reduce` 外,任何协程都不得写 `world`。

**队列有界,满了是「卡住」信号而非常态。** ≤20 人、房间极少,正常负载下 `inbox` 与各连接 outbound 队列都远不会满。给它们设一个宽松上限纯属防御:
- **outbound 队列满**(`put_nowait` 抛 `QueueFull`):说明那条连接的 Sender 卡死(客户端假死/极慢)。**丢弃该连接 + 投 `Disconnect`**,别拖累 GameLoop;客户端重连后由 `Personal(StateSnapshot)` 补回全量状态(见连接生命周期)。绝不阻塞、绝不在 dispatch 里等。
- **`inbox` 满**:意味着 GameLoop 自己卡住了(某条 reduce 死循环 / 阻塞)——这是 bug,落 **CRITICAL** 日志,属进程级故障,不是要去优雅处理的常态。

**什么才配单开协程 / 队列?判据只有一条:会不会卡住事件循环。** 只有**远端、可能阻塞**的慢 IO 才解耦——`ws.send`(慢客户端能卡几秒)、DB commit(网络 + 锁),所以才有 Sender / PersistWriter。反过来,本地、快的操作(写日志、Timer 的瞬时 dict 写)单线程 asyncio 下同步调用本就原子,直接串行做,不套队列(详见 [coding_principle.md](coding_principle.md))。

## 五种数据类型

| 角色 | 类型 | 是什么 |
|---|---|---|
| 状态 | **World** | 权威内存状态:全部房间(`Room`/`Hand`/`Player`/`Seat`)+ 全局用户积分(`users` 表) |
| reduce 输入 | **Command** | 流入 core 的意图:玩家动作 + 系统命令(超时/清理/连接/断开)。**开放集合**;每条命令**只作用于一个房间** |
| reduce 输出·成功臂 | **Event** | 成功副作用:A 组对外(`Broadcast`/`Personal`/`Persist`)+ B 组驱动 shell 内部快设施 |
| reduce 输出·失败臂 | **Err** | 失败值:`ErrorCode` + `detail`,不含收件人(见 [error.md](error.md)) |
| wire | **Message** | 与前端约定的报文:`ClientMessage` 进 / `ServerMessage` 出(`ErrorMessage` 是其一) |

`reduce(work, cmd) -> (list[Event], Err | None)`:成功给 `(events, None)`、失败给 `([], Err)`,两条臂互斥。

```
ws ──ClientMessage──▶ Receiver ──Command──▶ inbox ──▶ reduce(work, Command)
                                                        ├─成功→ events → dispatch → Sender/PersistWriter/Timer
ws ◀──ServerMessage(含 ErrorMessage)── Sender ◀─────────┴─失败→ Err → GameLoop 转 ErrorMessage 回发发起人
```

边界澄清:

- **Command ≠ Message**:系统命令(`Timeout`/`Connect`/`Cleanup`)没有对应报文,由 Timer / 连接生命周期产生。
- **Event ≠ Message**:`Event` 是 core→shell 的内部信封;`Message` 是信封里发给客户端的信。
- **Err ≠ Message**:`Err` 是 core 的失败值,到 wire 才转成 `ErrorMessage`。
- **DB 模型不在其中**:落库 schema 由 PersistWriter 持有,core 不碰,`Persist` 的 payload 是唯一过桥点。

## 统一回滚:工作副本 commit-or-discard

**这是本架构唯一的状态修改 + 回滚模型,所有命令一视同仁:**

1. GameLoop 处理一条命令时,先对**工作集**深拷贝一份**工作副本**——因为「每条命令只作用于一个房间」,工作集 = 目标房间 + `users` 表(用户表小,整份拷贝即可)。
2. `reduce` 只在工作副本上改,校验与修改可随意穿插(失败就整份丢弃,不必先校验后改)。
3. `reduce` 返回 `(events, err)`:
   - **失败 / 抛异常** → **丢弃工作副本**,真正的 `world` 一字节未动。
   - **成功** → 把工作副本**装回** `world`(替换引用),再 dispatch events(含 `Persist`)。

`checkout` / `commit` 是 **`shell/world.py` 的模块级函数**(`checkout(world, cmd)` / `commit(world, work)`),不是 `World` 的方法——`World` 是 core 的纯 dataclass(见 [models.md](models.md)),把「深拷贝 + 解析目标房 + 返回 shell 的 `Work`」挂成它的方法会让 core 类型背上 shell 职责、依赖 `Work` 这个 shell 类型,破坏分层。

```python
from app.shell.world import checkout, commit

class GameLoop:
    world: World
    inbox: asyncio.Queue[Command]
    conns: ConnectionManager     # 把 nick 解析成物理 Sender 队列(连接绑 nick,见 connection.md)
    persist: PersistWriter       # delayDB(见 db.md)
    timer: Timer
    async def run(self):
        while True:
            cmd = await self.inbox.get()              # 唯一让出点
            work = checkout(self.world, cmd)          # ① 按命令类型解析目标房 + 深拷贝(房 + users 表)→ 工作副本
            try:
                events, err = reduce(work, cmd)       # ② 同步,只改副本
            except Exception:
                log.exception("reduce crashed on %s", cmd)
                events, err = [], Err(ErrorCode.INTERNAL)   # 异常归一为失败臂
            if err is not None:                       # ③ 失败/异常:丢弃 work,world 未动
                self.send_error(cmd, err)
            else:
                commit(self.world, work)              # ④ 成功:装回 world(替换引用)
                for ev in events:
                    self.dispatch(ev)                 # 只 put_nowait,不 await
```

要点:

- **失败安全 = 没 commit**:业务校验失败和未预期异常走同一条路(都不 commit),无需"快照后再恢复",积分和房间状态在同一份副本里一起 commit / 丢弃。
- **跨命令隔离天然成立**:commit 是"替换引用",旧对象不再被原地改,事件携带的对象异步发送前不会被改写(不变量 7 由此减负为「同一条 reduce 内产出 event 后别再改它」)。
- 这只隔离**逻辑异常**;进程级崩溃仍丢进行中手牌(见「崩溃语义」)。

> 完整存储模型(内存权威 + 载入一次 + 工作副本回滚 + 大实体 `uRead`/`uWrite` + delayDB)见 **[storage.md](storage.md)**;本节只给 GameLoop 这一处的落点。

## Event 的类别

`reduce` 产出的 Event,按 dispatch 送去哪里分两组:

**A 组 · 对外 IO 事件(走队列、交慢 IO 协程 —— 只有这三种,封闭):**

| Event | 目标 | 消费者 |
|---|---|---|
| `Broadcast(room, msg)` | 整房间广播 | 各连接 Sender 队列 |
| `Personal(nick, msg)` | 私发单个连接(底牌、入桌/重连的 `StateSnapshot` 全量快照);连接全局按 nick,无需 room | 该连接 Sender 队列 |
| `Persist(payload)` | 持久化意图 | PersistWriter(delayDB) |

`Broadcast`/`Personal`/`Persist` 三个变体**就是 dispatch 唯一的路由依据**;信封里直接装消费者要的数据(`msg` 即 wire `ServerMessage`,`payload` 即 delayDB 要落的结构),不套第二层。要新增第四种对外副作用 = 要新增一个慢 IO 消费者协程,受「不过度解耦」约束(见 [coding_principle.md](coding_principle.md)),不是随手能加。

**B 组 · 驱动 shell 内部快设施(同步派发、不走队列):**

`TurnChanged` / `ClearAction` → Timer 的同步方法(见 [timer.md](timer.md))。reduce 产出、dispatch 路由,但目标是本地快设施,**同步调用、不进队列**。

> **错误不在 Event 里。** 失败时 world 没动、零副作用;`Err` 是 reduce 的失败臂,由 GameLoop 转 `ErrorMessage` 回发发起人。

## 并发不变量(任何改动必须守住)

1. **core 纯同步、不阻塞、不靠墙钟做决策**:禁止 `async`/`await`、网络/文件/DB、`sleep`。判据两条——①**会不会卡住事件循环**(慢 IO 一律外移);②**会不会拿墙钟当游戏判据**(超时/新鲜度只用单调自增的 `epoch`,绝不读 `time.time()`,免得 NTP 校时误触)。非阻塞本地计算不违反本条:`random.SystemRandom()` 洗牌可用;shell 盖好的时间戳作为**记录元数据穿过 core**(只存进 `Hand.start_time`、绝不参与任何分支)也可用。reduce 仍须「给定 `world+cmd` 可断言输出」,所以墙钟一律由 shell 盖、core 不主动读。
2. **`world` 只由 GameLoop 经「工作副本 commit」更新**;`reduce` 只改副本,其它协程只读已提交状态、绝不写。Receiver 从 DB 读数据(shell IO)是允许的,但它**读 DB、不读 `world`**——载入与否的决定权在 reduce(见 [user.md](user.md))。
3. **GameLoop 处理一条命令期间不 `await`**(派发只用 `put_nowait`)。
4. **对外发送只经 per-connection Sender 队列**:禁止 `create_task(ws.send())` 或别处直接 `ws.send()`。
5. **定时器、连接、断线一律转 `Command` 进 `inbox`**,不旁路改状态。
6. **错误是 reduce 的返回值(`Err`)**,不是 Event、不用异常做控制流;失败时 `events` 为空、`world` 未动。
7. **事件不持有会被后续改写的引用**:跨命令隔离由工作副本 commit 天然保证(提交即替换引用,旧对象不再被原地改);唯一纪律是**同一条 reduce 内,产出某 event 后别再改它引用的对象**(事件一般在末尾构造,自然满足)。
8. **每条命令只作用于一个房间**:工作副本、回滚都据此界定。目标房 = `JoinRoom` 命令里的 room,或其余命令的 `world.users[cmd.origin].room`(模型 2:游戏命令不带 room,`origin` = 发起人 nick),`checkout` 据此解析。跨房间操作目前不支持,需要时另议。
9. **一个用户同一时刻只在一个房间**:`UserState.room` 记其所在房间;已在某房者 `JoinRoom` 到别房被 reduce 拒掉(`ALREADY_IN_ROOM`,要先 `LeaveRoom`),见 [lobby.md](lobby.md)/[user.md](user.md)。这让全局积分驱逐**无歧义**——唯一房间的 `LeaveRoom`/`Cleanup` 就是彻底离场。

## 连接生命周期(全部走命令)

- **连接**:ws 握手(鉴权见 [auth.md](auth.md))成功 → 建 outbound 队列 + 起 Sender → 投 `Connect(nick)` 接入**大厅**(连接绑 nick、不绑房间,模型 2)。reduce:若该 nick 正在某房(之前 OFFLINE)→ 重连恢复 + 私发 `Personal(StateSnapshot)`;否则纯大厅接入。**进房与载入积分在 `JoinRoom`**(见 [lobby.md](lobby.md) / [user.md](user.md))。
- **断开**:ws 异常 → 投 `Disconnect(nick)`(模型 2:不带 room,reduce 用 `world.users[nick].room` 解析)。reduce 标记离线、对局中保留座位。
- **重连**:同一 nick 再次 `Connect`,reduce 取消其清理、恢复状态、补发 `Personal(StateSnapshot)`,**忽略本次从 DB 读到的值**(内存比 DB 新)。
- **超时/清理**:Timer 到点投 `Timeout` / `Cleanup`,由 reduce 处理(自动 fold、退还筹码、释放座位)。

「什么时候真正离场」「断线保留多久」都是 reduce 里的规则,shell 只负责把事件变成 IO 与计时。

## 持久化与一致性

- **统一范式**:凡需落库的数据都走「内存权威 + delayDB」——从 DB 读一次进内存,之后改内存、由 delayDB 异步追平 DB。**全局积分**是当前唯一持久化的内存实体;手牌结束写**手牌记录**。room 状态目前不落库,需要时按同一范式接入。通用机制(写缓冲、覆盖/追加、周期 flush、失败重试、drain)单独成模块,见 [db.md](db.md)。
- **无并发写者**:PersistWriter 是唯一 DB 写者 ⇒ **不需要行锁 / `with_for_update`**(旧 [services.py](../app/pokertable/services.py) 里的行锁全部删除)。
- **买入是纯内存转账**:reduce 在工作副本上校验积分够不够、过了就改内存并产出 `Persist`;DB 不是买入的关卡,**不存在「落库失败需回滚」、无 `BuyInFailed` 命令**。落库失败只由 PersistWriter 重试 + 落 ERROR,内存权威始终自洽。
- **崩溃语义**:单进程,任何崩溃带走全部内存状态——丢失进行中手牌(已结束的已落库)+ 尚未 flush 的积分变更。因是积分非货币,本规模直接接受;重启从 DB 载入积分初值即可,无需对账。

## 客户端协议契约(单一事实源 + 代码生成)

wire 消息(`ServerMessage`/`ClientMessage`)**只在后端 Pydantic 写一份**,前端 TS 类型**自动生成,绝不手写第二份**(现有 [frontend/src/types/poker.ts](../../frontend/src/types/poker.ts) 的 `chips`/`phase` 已和后端 enum 漂移,就是反例)。

- **每条消息带 `type` 字面量**,构成可辨识联合(discriminated union),Pydantic 与 TS 1:1 对应。
- **后端 Pydantic = 唯一事实源**;TS 经 codegen 产出(`pydantic2ts`,REST 走 OpenAPI + `openapi-typescript`)。
- **生成步骤进 CI / pre-commit**;前端只消费生成产物,禁止手写/手改。
- 多语言文案不在协议里:后端只回机器可读 `code`(见 [error.md](error.md)),文案由前端按 `code` 映射。

具体消息清单随协议模块细化(治理见 [wire.md](wire.md),清单在 .py),本节只锁定「单一事实源 + 自动生成 + 前端不手写」。

> **传输层另有一层加密信封**:这里说的 `ServerMessage`/`ClientMessage` 是**明文 JSON**;在无 TLS 的前提下,它外面还套了一层 SM4 + HMAC-SM3 + 序号的安全帧(见 [auth.md](auth.md))。前端除消费 codegen 的 JSON 类型外,还要实现这层帧的加解密——codegen 只覆盖 JSON 那层,不覆盖加密。

## 错误处理(详见 [error.md](error.md))

错误是**值不是异常**:`reduce -> (events, Err | None)`。

- 业务校验失败(非你回合、积分不足等):工作副本被丢弃、`world` 未动;GameLoop 把 `Err` 转 `ErrorMessage` 回发**命令发起人**(`cmd.origin`),不广播。
- 协议/解析错误:在 Receiver 层(没形成合法 `Command`),直接构造 `ErrorMessage` 投该连接 Sender 队列(绕过 reduce,不绕过保序)。
- 未预期异常(bug):GameLoop `try/except` 接住 → 丢弃工作副本 → 以 `Err(INTERNAL)` 回发 + `log.exception`,继续处理下一条命令。

## 定时器(详见 [timer.md](timer.md))

- **行动倒计时**:进入某玩家回合时由 reduce 产出事件、Timer 起一个倒计时;玩家行动后覆盖/取消;到点投 `Timeout`,reduce 校验仍是该回合后执行默认动作(能 check 则 check,否则 fold)。
- **过期防护**:Timer 永远可能投出已过期的命令,正确性靠 reduce 进门先做 staleness 校验——判据是 `hand.epoch`(每次行动推进/街道切换自增的内存计数,见 [core.md](core.md)),`hand.epoch != cmd.epoch` 即过期、忽略。**不引入 wall-clock 的 `hand_id`**。

## 测试

- `reduce` 给定 `work + 命令序列`,断言改后的状态与产出的 `Event` 列表,无需 DB / WebSocket。
- 边池、dead blind、all-in、断线重连、超时默认动作全部在 core 层单测覆盖。
- shell 仅需少量集成测试验证队列接线与保序。

## 优缺点

**优势**:无锁原子(reduce 同步即不可打断);单一序列化点,状态机易推理;慢客户端 / 慢 DB 隔离;core 可纯单测。

**缺点**:全局串行(所有房间共用一个 GameLoop,跨房间队头阻塞——本规模可忽略,房间多时改为每房间一个 GameLoop,core 不变);单进程不可水平扩展、崩溃丢进行中手牌;最终一致(DB 落后内存);保序靠纪律(违反不变量 4 即乱序)。
