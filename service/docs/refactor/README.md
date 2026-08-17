# 重构总纲(先读我)

> 德州扑克后端从原型重构到 [docs](../) 新架构的总体规划与工作约定。开工前读完本篇,再按需翻对应设计文档。

## 0. 这份文档的地位(免责声明,最重要)

一句话:计划不是命令,设计文档也不是圣经。**带着批判性思考干活,别闷头执行。**

- [TODO.md](TODO.md) 的阶段划分和 [../](../) 下的文档都可能出错:拆错粒度、定错顺序、与实现冲突,或者有更优解。盲目执行一份可能有问题的计划 = 把计划的错误固化进代码。
- **每写一段代码,先问:这一步本身对吗?放在整体架构里是最好的做法吗?有没有更简洁/更正确的解?** 发现问题当场改文档和计划,在变更记录里论证理由,再写代码。可改范围:[architecture.md](../architecture.md)、各模块设计文档、本篇、[TODO.md](TODO.md)。
- 目标永远是「结合架构判断写出最好的代码」,不是「不改计划地把 TODO 跑完」。
- 别硬凑代码去迁就文档。实现服从正确性和 [coding_principle.md](../coding_principle.md) 的硬规则;文档里的 `.py` 伪码只是示意,字段名、签名以实现为准。
- 唯一不能破的是不变量:core 纯同步、单写者、工作副本回滚、内存权威。要破,先在变更记录里论证并改架构文档。
- 任何对设计或计划的偏离、任何设计讨论(问题 + 结论),都写进 [changes/](changes/) 变更记录,并同步更新对应设计文档(见 §5)。

## 1. 目标(重构要达成什么)

原型的问题:IO 与状态混杂、单房间硬编码、ws 零鉴权、一堆 runtime bug。

重构目标见 [architecture.md](../architecture.md):

- **core / shell 两层**:core 是纯同步游戏规则,签名 `reduce(work, cmd) -> (events, err)`;shell 管 IO、并发、生命周期。
- **单写者 + 工作副本 commit/discard**:唯一 GameLoop 串行处理命令,无锁原子;失败时整份工作副本(本条命令的状态草稿)丢弃。
- **内存权威 + delayDB**:全局积分和手牌记录先在内存生效、再异步落库。唯一 DB 写者,无行锁。
- **连接模型 2**:连接绑 nick、不绑房间。命令不带 room,目标房由 `world.users[origin].room` 推定。
- **国密自建安全信道**:无 TLS,用 SM4+HMAC-SM3+序号逐帧加密,解决 ws 零鉴权和明文密码。
- **wire 单一事实源 + codegen**:后端 Pydantic 写一份,前端 TS 自动生成。

适用规模锁定:单进程、内网、在线 ≤20、房间极少、筹码是积分不是货币。最终一致、崩溃丢进行中手牌、全局串行等取舍都基于此。

## 2. 历史:被取代的原型代码(已于 [0027](changes/0027-prototype-teardown.md) 拆除)

> 原型五包(`app/pokertable`/`app/user`/`app/auth`/`app/handrecord`/`app/database`)+ 三入口(`app/main.py`/`app/app_route.py`/`app/init.py`)已删,代码在 git history(见 [changes/0027](changes/0027-prototype-teardown.md))。下表是历史问题清单,留作 reduce/shell 设计的 bug 备忘,不作为事实来源(见 [core.md](../core.md))。

原型特征:全局单例 `game_room`、IO 里直接改内存状态、`with_for_update` 行锁散落、ws 端点用 `?user_nickname=` 明文 query 当身份、单房硬编码 `room1`。

| 原模块(已删) | 历史职责 | 历史问题 | 已被谁取代 |
|---|---|---|---|
| `main.py` / `app_route.py` / `init.py` | FastAPI 启动、路由汇总、硬编码初始化 9 个用户 | `init.py` 导入时 `asyncio.run`;无 lifespan 编排 | `shell/lifespan.py` |
| `database/core.py` | async engine / session / `DBsession` 依赖 | 注释掉的死代码 | shell PersistWriter 自持 session |
| `user/` | 登录(SM3 裸哈希)、改密、改昵称、排行 | 密码无盐单轮;`points` 直接读写 | P5 国密信道 + `db/` + P7 REST |
| `auth/` | JWT access/refresh、refresh 用行锁 + 内存池 | `services.py.bak` 死代码;ws 无鉴权 | P5 国密安全信道 |
| `pokertable/models.py` | `Card/Player/Hand/Room/Seat` (Pydantic) | 域模型与 wire/DB 未分离 | `core/domain.py` + `wire/` + `db/` |
| `pokertable/enums.py` | 四套状态枚举 + 转移规则 | 基本可复用 | 已迁 `core/enums.py`(0002)|
| `pokertable/gamelogic.py` | 发牌/轮转/动作/边池(treys) | `get_blind` 未定义即导入;循环变量覆盖入参 | `core/rules/` + `core/deck.py` |
| `pokertable/services.py` | 业务流程编排(async generator) | 硬编码 room1(即 `only_room_name`);行锁不一致;多个 runtime bug:`pots.values().sum()`、`hand.next_player_position`、`turn_card=community_cards`、`list().extend()` | `core/reduce.py` |
| `pokertable/websocket.py` | `GameRoom` 单例、连接/断线/延迟清理、广播 | IO 直接改状态;`card_message` 可能 NameError;`disconnect_tasks` 未初始化导致 KeyError;绕过注入另开 session | `shell/` |
| `pokertable/wsm_schemas.py` | ws 消息 Pydantic + parse/serialize | 原计划作 wire 起点 | `wire/server.py`+`client.py`(0017)|
| `pokertable/routes.py` | ws 端点 `/room?room_id=&user_nickname=` | 零鉴权,可冒充任意身份 | `shell/receiver.py`(dev 明文)→ P5 国密握手 |
| `handrecord/` | 手牌记录 SQLModel + 分页查询 | 查询访问 `participant.player.nickname`,但 select 返回元组(bug)| `db/models.py`(0026)+ P7 REST 查询 |
| [frontend/src/](../../../frontend/src/) | Next.js;`types/poker.ts` 手写类型 | `chips`/`phase` 已与后端 enum 漂移 | `wire.gen.ts` codegen(0017)|

> 行号级历史问题详见 git history 中的原型代码与重构起点的代码审计。

## 3. 目标结构(提案,可改)

按 core/shell 物理分层重组。这是建议布局,落地时可调整命名和拆分:

```
service/app/
  core/                  # 纯同步,禁 async/await/IO/DB/读墙钟(硬规则 1)
    domain.py            # World/Room/Hand/Player/Seat/UserState(dataclass)
    enums.py             # RoomStatus/HandStatus/UserStatus/PlayerStatus(+ 合法转移表)
    commands.py          # Command 全集(origin: str|None,不带 room)
    events.py            # Broadcast/Personal/Persist/TurnChanged/ClearAction
    errors.py            # ErrorCode / Err
    reduce.py            # reduce(work, cmd) 顶层 match + 各 _handler
    rules/               # rules.md 的三块,带穷举测试
      blinds.py          #   ① 座位与盲注(含 heads-up、入局防躲盲、免盲投票)
      betting.py         #   ② 行动规则 + 下注轮关闭判据(has_acted/min-raise)
      sidepot.py         #   ③ 边池分层 + 退还未叫注 + 奇数零头
    deck.py              # SystemRandom 洗牌 + treys 评估(纯计算,允许)
  shell/
    world.py             # World.checkout(cmd) / commit(work)(工作副本)
    gameloop.py          # 取命令→checkout→reduce→commit→dispatch
    dispatch.py          # Event → Sender 队列 / PersistWriter / Timer
    connection.py        # ConnectionManager / Connection / SecureChannel
    receiver.py          # ws handler:握手鉴权→收帧验解→Command→inbox
    sender.py            # per-connection 出站:加密成帧→ws.send,严格保序
    timer.py             # 行动倒计时(room 键) + 保活(nick 键)
    persist.py           # delayDB:WriteBuffer + PersistWriter + Persister 协议(见 0028)
    lifespan.py          # 启动正序 / 关闭反序 drain
  wire/                  # Pydantic ClientMessage/ServerMessage(单一事实源)+ codegen 脚本
  db/                    # SQLModel:User/HandRecord…(对齐 *Write)+ Alembic
  auth/                  # 国密信道:登录握手 / 逐帧加密 / 密码哈希 / K_user 轮换
  rest/                  # leaderboard / hands / profile(读 DB,不碰 world)
  config.py              # 基础设施(.env:DATABASE_URL/JWT…)
  gameconfig.py          # 游戏可调参数(poker.env:超时/盲注/delayDB/日志…)
```

依赖方向铁律:**core 不 import shell / fastapi / sqlalchemy / websocket**。core 可以 import wire 的 Pydantic DTO,因为构造 ≠ 序列化(见 [models.md](../models.md))。

## 4. 任务分解(分阶段,细化清单见 [TODO.md](TODO.md))

先做能纯单测的 core,再接 shell IO——最大的正确性风险(牌局规则)先脱离 DB/WS 解决。

| 阶段 | 目标 | 关键产出 | 主要参考 |
|---|---|---|---|
| **P0 基线** | 定死数据类型 + 工作副本 API | `domain.py`/`enums.py`/`commands.py`/`events.py`/`errors.py`、`World.checkout/commit` | [core.md](../core.md) [storage.md](../storage.md) |
| **P1 core 规则**(主力) | `reduce` + rules 三块 + 纯单测 | `reduce.py`/`rules/`/`deck.py`、`tests/core/` | [rules.md](../rules.md) [core.md](../core.md) [testing.md](../testing.md) |
| **P2 shell 骨架** | GameLoop 串行 + dispatch + 工作副本回滚 | `gameloop.py`/`dispatch.py`/`world.py`、集成测试 | [architecture.md](../architecture.md) |
| **P3 连接层** | Receiver/Sender/ConnectionManager/Timer + 顶替(同一 nick 用新连接登录时踢掉旧连接)/重连 | `connection.py`/`receiver.py`/`sender.py`/`timer.py` | [connection.md](../connection.md) [timer.md](../timer.md) |
| **P4 delayDB** | 写缓冲 + PersistWriter + DB 模型对齐 + Alembic | `persist.py`/`db/`、迁移 | [db.md](../db.md) [storage.md](../storage.md) [dev.md](../dev.md) |
| **P5 鉴权信道** | 登录握手 + 逐帧加密 + 密码哈希迁移 + K_user 轮换 | `auth/`、crypto 单测 | [auth.md](../auth.md) |
| **P6 wire codegen** | Pydantic 单一事实源 + TS 自动生成进 CI | `wire/`、codegen 脚本 | [wire.md](../wire.md) [models.md](../models.md) |
| **P7 大厅/查询/聊天** | lobby/REST/messaging/presence | `rest/`、lobby/messaging 落点 | [lobby.md](../lobby.md) [rest.md](../rest.md) [messaging.md](../messaging.md) [presence.md](../presence.md) |
| **P8 收尾** | lifespan drain、日志、配置收编、前端对接 | `lifespan.py`、日志、配置 | [config.md](../config.md) [log.md](../log.md) |

阶段之间不是硬墙:P1 可与 P0 末段交叠。但 **P1 必须能在无 DB/WS 下测试全绿**,这是整个架构可测性的兑现点。

> **执行顺序已重排(0016)**。上表是职责地图,不是执行序。
>
> - 为让前端尽早联调,wire 协议(P6)+ 明文 dev 端点(P2/P3 最小版)提前到 P1 主体之后。
> - 国密安全信道(P5)推到最后,协议按模块增量交付。
> - 实际执行序以 [TODO.md](TODO.md)「执行顺序」和 [changes/0016](changes/0016-replan-wire-first.md) 为准。
> - 本表的分层、产出、参考不变。

## 5. 工作流程约定(每次干活都照做)

核心规则:**变更记录先行**。动代码、改设计之前,先在 [changes/](changes/) 开一篇写下打算改什么;干完回填实际改了什么、为什么、踩了什么坑。一篇对应一个工作单元:一个讨论、一个 TODO 项、或一次重构。

1. **开工前**:
   - 从 [TODO.md](TODO.md) 挑一个 `[ ]` 项,读它指向的设计文档;先质疑这一项的粒度、顺序、方案是否最优(见 §0),该改就先改 TODO 和设计文档。
   - 然后在 [changes/](changes/) 新建 `NNNN-<slug>.md`,序号递增;记录里写:要动哪些文件、预期的设计与接口、可能要调整的文档。
2. **干活中**:
   - 守 [coding_principle.md](../coding_principle.md) 的硬规则;文档与实现冲突或有更优解,当场改文档(见 §0)。
   - 设计讨论(含对话里定的)把问题和结论写进当前变更记录,并据结论改对应设计文档。
3. **收工前**(每次):
   - 回填变更记录:实际改了哪些文件、为什么、动了哪些文档和计划、留下什么待办;与开工前的「打算」有差异也记。
   - 回 [TODO.md](TODO.md) 勾掉完成项、补新发现项;计划要调就调。
   - 若改了不变量或架构决策,确认已同步更新对应 docs,并在变更记录里点名是哪几篇。
   - push 前按 [review.md](../review.md) 做对抗式自 review,七维是分层/文档同步/文档一致/数据模型/规范/测试/账本。问题当场修,结论记进变更记录的「自 review」段。**无此段不 push;测试全绿不等于可以提交。**
   - 提交代码(git 规约见 [dev.md](../dev.md)),提交信息引用变更记录编号。
4. **测试纪律**:core 改动配 core 单测(参考 [testing.md](../testing.md));规则用例对齐 [rules.md](../rules.md) 编号。

一句话:代码、文档、计划三者始终对齐;变更记录是这条链的账本。

## 6. 文档导航(按需深读)

- 入口规约:[coding_principle.md](../coding_principle.md)(硬规则,先读)
- 架构与不变量:[architecture.md](../architecture.md)
- 存储/回滚/落库:[storage.md](../storage.md) · [db.md](../db.md)
- 游戏规则:[core.md](../core.md) · [rules.md](../rules.md)
- 三套表示:[models.md](../models.md) · wire 治理 [wire.md](../wire.md)
- shell 装配:[connection.md](../connection.md) · [timer.md](../timer.md) · [error.md](../error.md)
- 子系统:[lobby.md](../lobby.md) · [user.md](../user.md) · [messaging.md](../messaging.md) · [presence.md](../presence.md) · [rest.md](../rest.md) · [auth.md](../auth.md)
- 工程:[config.md](../config.md) · [log.md](../log.md) · [dev.md](../dev.md) · [testing.md](../testing.md) · [review.md](../review.md)(提交前复审)
