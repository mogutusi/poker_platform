# 重构 TODO(活清单)

> 规则见 [README.md](README.md) §5:每次收工**勾掉完成项 / 补新发现项**,并在 [changes/](changes/) 留一篇变更记录。
> 这是计划本身,**可以改**——发现顺序不对、任务拆错,直接调整。

图例:`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成

---

## 执行顺序(0016 重排:前端解锁前置)

> 前端要联调 → **wire 协议 + 明文 dev 端点前置**;**国密加密信道(原 P5)推到最后**;**协议按模块增量交付**(每落一个模块补该模块的 wire 切片 + 重 codegen)。详见 [changes/0016](changes/0016-replan-wire-first.md)。**只动顺序,不动架构/不变量。**

执行序:**P0 ✓ → P1(主体 ✓,余项随后)→ W(wire 首批协议)→ D(最小明文 dev shell + 端点)→ P1 余项 +各模块(每项补协议切片)→ 硬化(delayDB / 背压 / 重连)→ 日志 / 配置收编 → 国密安全信道(最后)→ 收尾**。

---

## P0 · 基线(数据类型 + 工作副本 API)

- [x] `core/enums.py`:四套状态枚举 + `USER_STATUS_TRANSITIONS` 合法转移表(从现 [enums.py](../../app/pokertable/enums.py) 迁移) — 0002
- [x] `core/domain.py`:`World/Room/Hand/Player/Seat/UserState` dataclass(含 `UserState.uid`、`Hand.epoch/seq/start_time`、`Seat.in_game_points/new_here`、`Room.entry_vote/waive_entry_for`) — 0002(另含 `Hand.last_raise_size`、`Player.has_acted`、`Seat.wait_for_big_blind`、`Room.leaving`、`EntryVote`、`core/cards.py`)
- [x] `core/commands.py`:Command 全集,统一 `origin: str | None`,**不带 room**(`JoinRoom(room, uid, loaded)` 例外带 room) — 0002
- [x] `core/events.py`:`Broadcast(room,msg)`/`Personal(nick,msg)`/`Persist(payload)`/`TurnChanged`/`ClearAction` — 0002
- [x] `core/errors.py`:`ErrorCode` 枚举 + `Err(code, detail)` — 0002
- [x] `shell/world.py`:`checkout(world, cmd)` 按命令类型解析目标房(表见 [storage.md](../storage.md));`commit(world, work)` 处理房间增/删/替换 + users 表替换 — 0002(模块级函数而非 World 方法,见 0002 偏离记录)

## P1 · core 规则(主力,纯单测)

- [x] `core/deck.py`:`SystemRandom` 洗牌 + treys `Evaluator` 单例 — 0007
- [~] `core/rules/blinds.py`:定庄/盲位/heads-up、入局「付盲即玩 / 等大盲免费」、免盲投票 — 0008 落地定位 + 下盲(①.1-①.5);入局资格 established/付盲即玩/bootstrap/尊重 waive 快照(①.6/①.11)随 0010 `_start_hand` 落地;免盲投票(①.12-①.15)随 0020 落地(reduce 投票簇);**等大盲再入局时机 + 躲盲被堵(①.7-①.10)仍待后续**
- [x] `core/rules/betting.py`:三动作校验、min-raise/重开、`street_closed` 谓词(`has_acted`)— 0007(另含 `settle_street`、`next_active_position`)
- [x] `core/rules/sidepot.py`:退还未叫注 → 分层削池 → 判池 + 奇数零头 — 0007
- [~] `core/reduce.py`:顶层 `match` + 各 helper(开局/动作/推进/摊牌/结束/连接/断线/超时/清理/买入/入座/状态/聊天/投票)— 0010 落地 `_start_hand`(开局);0011 落地 `_player_action` + 街推进/摊牌/边池结算/手牌记录(`core/records.py`)+ born-all-in runout(接住 0010 §6);0014 落地局中生命周期(rules.md ④):`_timeout`(超时默认动作)/`_leave_room`(局中 auto-fold + 手尾 `_evict` / 局外即时驱逐)/`_disconnect`(标 OFFLINE 保座)/`_cleanup`(staleness 退筹释座)/`_set_user_status`(局中坐出延手尾 + 就座内 ready/sit-out 切换)+ `_finalize_hand` 驱逐整合 + 抽取 `_acted_events`;0015 落地就座/买入:`_sit_down`(观战→就座 new_here)/`_buy_in`(全局→座位 + PointsWrite)/起身(`SetUserStatus`→WATCHING 腾座退筹,补 0014 占位)+ 抽取 `_release_seat`;0020 落地免盲投票簇:`_open_free_entry_vote`/`_vote_free_entry`(真空守门 + reject 即失败 + 快照)+ 投票人离场/坐出重算(挂 `_begin_leave`/`_set_user_status`);**进房载入/重连(`JoinRoom`/`Connect`+`StateSnapshot`)/房配置(`SetSmallBlind`/`SetBuyIn`+买入上下限,随 P8 配置收编)/聊天簇待后续**
- [~] `tests/core/`:按 [rules.md](../rules.md) 编号转穷举单测;守恒 + 隐私断言默认开 — 0007 落地 ②/③ 穷举(deck/betting/sidepot 34 测试,共 58);0008 落地 ① 定位/下盲穷举(blinds 7 测试,共 65);0010 落地 ① 开局 reduce 集成(test_start_hand 22 测试,共 88;含自 review 修复:bootstrap 看整桌/防躲盲、短牌堆守 Err、事件顺序/分支可分辨断言);0011 落地 `_player_action` 编排集成(test_player_action 12 测试 + born-all-in 改判,共 100;动作校验臂/街内换人/preflop 大盲选择权/多街推进/摊牌+边池还座/无摊牌结束/all-in 跑公共牌/守恒/隐私);0014 落地局中生命周期集成(test_timeout 6 + test_leave_sitout 21 + sidepot/player_action 补 3,共 130;超时默认 check/fold + staleness、局中离桌即时 fold + 手尾驱逐、坐出延手尾、断线 OFFLINE 保座、Cleanup staleness、ALLIN 离桌带奖金、弃牌唯一最高者未叫注 forfeit、heads-up SB 开弃回归;守恒 + 隐私);0015 落地就座/买入(test_seat_buyin 14,共 144;观战→就座 new_here、全局↔座位转账守恒、起身腾座退筹、各错误臂含负额/越界);0020 落地免盲投票(test_free_entry_vote 18,共 196;①.12-15 全票/否决/蹭车快照/离场重算 + 坐出重算 + 开票/投票错误臂 + 真空守门 + 幂等开票 + 候选冻结防蹭/孤儿票失效/残票随开局作废/进度剔除离场赞成/多候选排序/坐出非投票人);等大盲/连接重连集成待后续

## W · wire 首批协议(前端解锁,增量第 1 批)— 详见 [changes/0016](changes/0016-replan-wire-first.md)

> 已设计/已落地的消息 + 命令 → Pydantic 单一事实源 + codegen TS。reduce 直接产 wire DTO(core 可 import wire DTO,见 [models.md](../models.md)/[README §3](README.md)),收编 `core/messages.py`;`core/records.py` 的 Persist 载荷不上 wire,保留。治理见 [wire.md](../wire.md),旧 [wsm_schemas.py](../../app/pokertable/wsm_schemas.py) 作参考。

- [x] `app/wire/server.py`:`ServerMessage` 可辨识联合(`core/messages.py` 全集升级为 Pydantic:`type` 字面量/扁平/snake_case/core enums):`HandStarted`/`HoleCards`/`HandStatusChanged`/`PlayerActed`/`HandShowDown`/`HandEnded`/`UserStatusChanged`/`UserLeft`/`PlayerBoughtIn` + `ErrorMessage.from_err` — 0017(隐私=结构性缺位,非 field_serializer;见 0017 决策 2)
- [x] `app/wire/client.py`:`ClientMessage` 可辨识联合 = 已落地命令报文(身份不进报文):`SitDown`/`BuyIn`/`SetUserStatus`/`LeaveRoom`/`StartHand`/`PlayerAction` + `parse`(JSON→`ClientMessage`)+ `to_command(msg,origin,now)`(Receiver 盖 `origin=nick`、shell 盖 `now` 墙钟)— 0017
- [x] reduce 投影改产 `app/wire` DTO,删 `core/messages.py`;`tests/core/*` 改 import(字段同名;三处位置构造改关键字)— 0017
- [x] codegen:**自包含 Python 生成器** `scripts/gen_wire_ts.py`(无 node;`pydantic2ts` 不可用)→ `frontend/src/types/wire.gen.ts`(只读产物);漂移守门 `tests/wire/test_codegen_uptodate.py` 骑 `pytest` 门槛 + `--check` 供 pre-commit — 0017(见 0017 决策 3/4)
- [ ] 前端消费 wire.gen.ts(**延后,随前端 WS client 集成**):0017 已生成 `wire.gen.ts` 解锁前端按真类型写 WS client;但 `frontend/src/types/poker.ts` 是 **UI mockup 聚合类型 + 本地 mock 牌局逻辑**(非协议类型),且本批无 `Player`/`StateSnapshot` wire 类型——此刻删它只会破坏 mockup 无替代。删 poker.ts + 改组件归「前端 WS client + StateSnapshot」单元(见 0017 决策 8)
- [~] 协议指南 [wire-protocol-guide.md](../wire-protocol-guide.md):收发消息目录 + 一手牌时序 + 错误码用法 + 现有/待补(增量)+ 形状铁律(type 判别/snake_case/身份不进报文/acting_position=players 下标)— 0017 后补;**dev 连接握手段**(`ws://…/dev?nick=`)随 **D 阶段** 端点补齐

## D · 最小明文 dev shell + 端点(前端真连联调)— 无加密,临时脚手架

> 串起已实现的 reduce,让前端连真端点跑通已落地流。**国密信道(原 P5)最后替换本层明文握手/帧**;明文端点标 `dev-only`、绝不上线。

- [x] `shell/gameloop.py`:`inbox` 串行 → checkout → reduce → commit/discard → dispatch(只 `put_nowait`;异常归一 `Err(INTERNAL)`)— 0018(`handle()` 抽出供同步测试)
- [x] `shell/dispatch.py`:`Broadcast`(按 world 房成员 + conns;容错销毁房)/`Personal`/`Persist`(交桩)/`TurnChanged`·`ClearAction`(调 Timer)+ `send_error`(Err→origin)— 0018
- [x] `shell/connection.py`:`ConnectionManager`(register/unregister/is_current/get/顶替)+ `Connection`(**明文 outbound,无 `SecureChannel`**)— 0018
- [x] `shell/receiver.py`:**dev 明文握手**(`?nick=`,**无 MAC/加密**,dev-only)→ 登记(顶替)→ 起 Sender → `Connect` → 收帧 `parse`→`Command` 盖 `origin`+`now`→inbox;每帧 `heartbeat`;退出 `is_current` 才投 `Disconnect`;解析失败回 `INVALID_MESSAGE` — 0018
- [x] `shell/sender.py`:per-connection outbound → `ws.send_text`(明文 JSON `model_dump_json`),严格保序 — 0018(队列满丢连在 dispatch._enqueue)
- [x] `shell/timer.py`:`_action`(room 键)+ `_liveness`(nick 键);`Timeout`/`Cleanup` 投 inbox;staleness 由 reduce 兜;单调时钟 `time.monotonic` — 0018(`tick()` 抽出供测试)
- [x] `shell/persist.py` 桩:最小 `WriteBuffer`(内存 list,`put`/`snapshot`;先不接 DB;P4 换双缓冲 + PersistWriter + ORM)— 0018
- [x] `shell/lifespan.py` 最小:`build_dev_world` 预置 dev 房 + dev 用户、起 GameLoop/Timer、挂 dev ws 端点 `/dev/ws?nick=` — 0018(+ `app/gameconfig.py` 带默认值的可调参数)
- [x] `tests/shell/`:工作副本回滚 + dispatch 路由 + 顶替身份判定 + Timer 触发/epoch + Sender 保序 + 异步端到端(26 测试,共 177)— 0018
- [x] 冒烟:命令穿 GameLoop → reduce → commit → dispatch → 各连接 outbound;sit/buyin/ready/start/action → `HandStarted`/`HoleCards`(私发)/`PlayerActed`/`HandEnded` 广播分流 — 0018(fake-ws,sync + async 两版)
- 注:`Connect` 加最小 no-op reduce 臂(避免 INTERNAL);进房 `JoinRoom` + 重连 `StateSnapshot` 仍是延后的 **P1 余项**(dev 预置用户绕开,见 changes/0018 决策 2/3)

## P1 余项(继续,每项**补该模块协议切片** + 重 codegen)

- [x] 免盲投票(rules.md ①.12-15):`OpenFreeEntryVote`/`VoteFreeEntry` + `room.entry_vote` 结算(真空守门 + reject 即失败)+ `waive_entry_for` 快照 + 投票人离场/坐出重算 + wire `FreeEntryVoteUpdated`/`FreeEntryVoteClosed`(+ `CANNOT_OPEN_VOTE`)— 0020
- [ ] 等大盲再入局时机(rules.md ①.7-10):`_start_hand` 中 BB 路过 `wait_for_big_blind` 座位免费入局 + 躲盲被堵(换座/退房/坐出再回算 new_here)
- [ ] `JoinRoom` + `Connect` + `StateSnapshot`:进房载入 `world.users` + 重连恢复 + **整桌快照报文设计**(座位/筹码/button/board/pot/acting/自己底牌)→ 补 wire `JoinRoom`/`StateSnapshot`/`UserJoined`
- [ ] `RoomChat` + `ChatMessage`(房聊走 reduce);`SetSmallBlind`/`SetBuyIn`(随配置收编接 `gameconfig` 上下限)

## 硬化 / 子系统(每模块补协议切片)

- [ ] P4 delayDB:`shell/persist.py` 双缓冲 swap + `PersistWriter`(先 swap 后 await)+ `to_orm`;`db/` 模型(`User` 加 uid/salt/rounds/K_user、`HandRecord` 对齐 `HandRecordWrite`)+ Alembic 迁移;回灌「更新者优先」/drain 测试
- [ ] shell 硬化:背压(inbox/outbound 上限 + 队列满丢连)、顶替/重连 `StateSnapshot`、`tests/shell/`
- [ ] P7 lobby/REST/messaging:`GET /lobby/rooms`、leaderboard/hands(游标)/profile(改昵称仅大厅)、房聊环形缓冲 + 私聊未读收件箱(见 [messaging.md](../messaging.md) + changes/0012)、presence 只读;REST 走 `openapi-typescript`
- [ ] 日志:GameLoop 边界审计 + 脱敏红线(底牌/密钥不进日志)
- [ ] 配置收编:可调参数进 `gameconfig`(买入上下限/超时/盲注上下限…),`poker.env` + `*.example` 同步

## P5 · 国密安全信道(**最后做**,替换 D 的明文层)

- [ ] 密码哈希:`salt$rounds$digest` + `compare_digest` + 数据迁移脚本
- [ ] 登录握手:`/user/login` SM4 护住密码、返回 session + JWT
- [ ] 逐帧加密:`SecureChannel` 入站「先验 seq → 验 MAC → 才解密」、出站加密(替换 Receiver/Sender 的 dev 明文帧)
- [ ] `K_user` 双钥 + 每周轮换任务 + 版本/宽限
- [ ] `tests/crypto/`:MAC 拒伪 / seq 拒重放 / 先验后解 / IV 不复用

## P8 · 收尾

- [ ] `shell/lifespan.py` drain:关闭反序 drain(超 `DB_DRAIN_TIMEOUT_MS` 落 CRITICAL)
- [ ] 端到端冒烟:前端 ↔ 后端(先明文 dev、后国密)走通一手牌全程

---

## 持续项(随时回看)

- [ ] **协议增量交付**:每落一个模块 → 补该模块 wire client/server 切片 + 重 codegen,前端跟随(见 [changes/0016](changes/0016-replan-wire-first.md))
- [ ] 文档与实现漂移时**改文档**并在 changes/ 记录
- [ ] 新增可调参数 → 进 `gameconfig` + env + example(不留裸字面量)
- [ ] 新增持久化实体 → 归「状态写 / 事件写」,不新开通道
