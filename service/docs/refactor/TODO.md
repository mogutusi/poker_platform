# 重构 TODO(活清单)

> 规则见 [README.md](README.md) §5:每次收工勾掉完成项、补新发现项,并在 [changes/](changes/) 留一篇变更记录。
> 计划本身可以改——发现顺序不对、任务拆错,直接调整。

> **缺陷去 [BUGS.md](BUGS.md) 看。** 本篇只管「还没做的事」;「已经确认会错、但还没修的代码」集中登记在 [BUGS.md](BUGS.md)(0076 建立),按严重度排序。以前缺陷混在本篇各轮小节里,容易漏掉。

图例:`[ ]` 未开始 · `[~]` 进行中 · `[x]` 完成

---

## 执行顺序(0016 重排:前端解锁前置)

> 前端要联调,故 wire 协议 + 明文 dev 端点前置,国密加密信道(原 P5)推到最后,协议按模块增量交付(每落一个模块补该模块的 wire 切片 + 重 codegen)。详见 [changes/0016](changes/0016-replan-wire-first.md)。只动顺序,不动架构/不变量。

执行序:**P0 ✓ → P1(主体 ✓,余项随后)→ W(wire 首批协议)→ D(最小明文 dev shell + 端点)→ P1 余项 +各模块(每项补协议切片)→ 硬化(delayDB / 背压 / 重连)→ 日志 / 配置收编 → 国密安全信道(最后)→ 收尾**。

---

## P0 · 基线(数据类型 + 工作副本 API)

- [x] `core/enums.py`:四套状态枚举 + `USER_STATUS_TRANSITIONS` 合法转移表(从原型 `pokertable/enums.py` 迁移;原型已于 0027 拆除) — 0002
- [x] `core/domain.py`:`World/Room/Hand/Player/Seat/UserState` dataclass(含 `UserState.uid`、`Hand.epoch/seq/start_time`、`Seat.in_game_points/new_here`、`Room.entry_vote/waive_entry_for`) — 0002(另含 `Hand.last_raise_size`、`Player.has_acted`、`Seat.wait_for_big_blind`、`Room.leaving`、`EntryVote`、`core/cards.py`)
- [x] `core/commands.py`:Command 全集,统一 `origin: str | None`,**不带 room**(`JoinRoom(room, uid, loaded)` 例外带 room) — 0002
- [x] `core/events.py`:`Broadcast(room,msg)`/`Personal(nick,msg)`/`Persist(payload)`/`TurnChanged`/`ClearAction` — 0002
- [x] `core/errors.py`:`ErrorCode` 枚举 + `Err(code, detail)` — 0002
- [x] `shell/world.py`:`checkout(world, cmd)` 按命令类型解析目标房(表见 [storage.md](../storage.md));`commit(world, work)` 处理房间增/删/替换 + users 表替换 — 0002(模块级函数而非 World 方法,见 0002 偏离记录)

## P1 · core 规则(主力,纯单测)

- [x] `core/deck.py`:`SystemRandom` 洗牌 + treys `Evaluator` 单例 — 0007
- [x] `core/rules/blinds.py`:定庄/盲位/heads-up、入局「付盲即玩 / 等大盲免费」、免盲投票 — 0008 落地定位 + 下盲(①.1-①.5);入局资格 established/付盲即玩/bootstrap/尊重 waive 快照(①.6/①.11)随 0010 `_start_hand` 落地;免盲投票(①.12-①.15)随 0020 落地(reduce 投票簇);等大盲再入局时机 + 躲盲被堵(①.7-①.10)随 0023 落地(`sweep_entrant` 大盲扫入 fixpoint + `new_here` 重标防躲盲 + `SitDown.wait_for_big_blind` wire 切片)
- [x] `core/rules/betting.py`:三动作校验、min-raise/重开、`street_closed` 谓词(`has_acted`)— 0007(另含 `settle_street`、`next_active_position`)
- [x] `core/rules/sidepot.py`:退还未叫注 → 分层削池 → 判池 + 奇数零头 — 0007
- [x] `core/reduce.py`:顶层 `match` + 各 helper(开局/动作/推进/摊牌/结束/连接/断线/超时/清理/买入/入座/状态/聊天/投票)——**0067 收账打勾**:所列子砖已随 0010–0049 全部落地,命令全集(含 0043/0044 房配置、0049 动态建房)均有 reduce 臂,无遗留— 0010 落地 `_start_hand`(开局);0011 落地 `_player_action` + 街推进/摊牌/边池结算/手牌记录(`core/records.py`)+ born-all-in runout(接住 0010 §6);0014 落地局中生命周期(rules.md ④):`_timeout`(超时默认动作)/`_leave_room`(局中 auto-fold + 手尾 `_evict` / 局外即时驱逐)/`_disconnect`(标 OFFLINE 保座)/`_cleanup`(staleness 退筹释座)/`_set_user_status`(局中坐出延手尾 + 就座内 ready/sit-out 切换)+ `_finalize_hand` 驱逐整合 + 抽取 `_acted_events`;0015 落地就座/买入:`_sit_down`(观战→就座 new_here)/`_buy_in`(全局→座位 + PointsWrite)/起身(`SetUserStatus`→WATCHING 腾座退筹,补 0014 占位)+ 抽取 `_release_seat`;0020 落地免盲投票簇:`_open_free_entry_vote`/`_vote_free_entry`(真空守门 + reject 即失败 + 快照)+ 投票人离场/坐出重算(挂 `_begin_leave`/`_set_user_status`);0021 落地房聊 `_room_chat`(只读 → `Broadcast(ChatMessage)`,文本防护归 shell);0022 落地进房/重连 core:`_join_room`(装 users+WATCHING+`UserJoined`+快照)/`_connect` 重连(OFFLINE→推断恢复+快照)/`_state_snapshot`(整桌投影,逐收件人自有底牌);0023 落地等大盲再入局:`_eligible_seats` 三分类(core_dealt/paying/waiters)+ `_start_hand` 庄位定于 core_dealt + `blinds.sweep_entrant`(大盲扫入 fixpoint,FIX-1 空 core 守门)+ 末尾 `new_here` 重标防躲盲 + `_sit_down` 透传 `wait_for_big_blind`;0031 落地 `_connect` 顶替再连臂(在房在线 → 只私发 `StateSnapshot` 对齐新连接,状态不变/不广播;承接 0022 重连,解 connection.md 129↔152 矛盾)。**房配置(`SetSmallBlind`/`SetBuyIn`)随 0043 落地(`_set_small_blind`/`_set_buy_in`,授权 0044 放开为任何在房成员 / 仅两手之间 / 上下限 shell 防护);私聊 DM 走 shell 路由(0038-0041,不进 reduce);client `join_room` 报文 + Receiver 读 DB 已随 0030 落地**
- [x] `tests/core/`:按 [rules.md](../rules.md) 编号转穷举单测;守恒 + 隐私断言默认开——**0067 收账打勾**:rules.md ①-④ 用例已全数转测(0007–0031 各批),后续新规则随其砖补测即可 — 0007 落地 ②/③ 穷举(deck/betting/sidepot 34 测试,共 58);0008 落地 ① 定位/下盲穷举(blinds 7 测试,共 65);0010 落地 ① 开局 reduce 集成(test_start_hand 22 测试,共 88;含自 review 修复:bootstrap 看整桌/防躲盲、短牌堆守 Err、事件顺序/分支可分辨断言);0011 落地 `_player_action` 编排集成(test_player_action 12 测试 + born-all-in 改判,共 100;动作校验臂/街内换人/preflop 大盲选择权/多街推进/摊牌+边池还座/无摊牌结束/all-in 跑公共牌/守恒/隐私);0014 落地局中生命周期集成(test_timeout 6 + test_leave_sitout 21 + sidepot/player_action 补 3,共 130;超时默认 check/fold + staleness、局中离桌即时 fold + 手尾驱逐、坐出延手尾、断线 OFFLINE 保座、Cleanup staleness、ALLIN 离桌带奖金、弃牌唯一最高者未叫注 forfeit、heads-up SB 开弃回归;守恒 + 隐私);0015 落地就座/买入(test_seat_buyin 14,共 144;观战→就座 new_here、全局↔座位转账守恒、起身腾座退筹、各错误臂含负额/越界);0020 落地免盲投票(test_free_entry_vote 18,共 196;①.12-15 全票/否决/蹭车快照/离场重算 + 坐出重算 + 开票/投票错误臂 + 真空守门 + 幂等开票 + 候选冻结防蹭/孤儿票失效/残票随开局作废/进度剔除离场赞成/多候选排序/坐出非投票人);0021 落地房聊(test_room_chat 6,共 202;在房广播/观战者可聊/不在房 NOT_IN_ROOM/只读无 Persist + 进行中手牌深比较只读守护 + 不一致成员防御臂);0022 落地进房/重连(test_join_reconnect 10 + wire StateSnapshot 隐私 + codegen 并集括号单测,共 214;进房装 users+WATCHING+UserJoined+快照/局中观战只见公共面【值级隐私】/ALREADY_IN_ROOM/NO_SUCH_ROOM/重连 PLAYING【值级隐私+pot/acting】|SITTING_IN(无手 & 局中有座两路)|WATCHING/在线·大厅幂等/守恒无 Persist);0023 落地等大盲再入局(test_wait_for_big_blind 15,共 229;①.7 大盲扫入免费下结构盲、非大盲位不发、heads-up core 翻 3 人、单 established+单 waiter、双 waiter 取最靠小盲、入局者是真大盲非最小座号(杀 min 变异)、靠后 waiter 随庄入局、FIX-1 空 core NOT_ENOUGH_PLAYERS 不崩、`new_here` 重标坐出/干等、键于发牌集、①.10 坐出再回付盲端到端、waive 优先、短码 waiter all-in 守恒、`SitDown.wait_for_big_blind` 透传);0031 顶替再连快照(test_join_reconnect 在房在线顶替→只私发快照[两手之间无手 / 局中带自有底牌、对手底牌值级不泄、状态不变不广播];拆原「在线幂等 no-op」测,共 278 含 shell 背压/端到端)

## W · wire 首批协议(前端解锁,增量第 1 批)— 详见 [changes/0016](changes/0016-replan-wire-first.md)

> 已设计/已落地的消息 + 命令 → Pydantic 单一事实源 + codegen TS。reduce 直接产 wire DTO(core 可 import wire DTO,见 [models.md](../models.md)/[README §3](README.md)),收编 `core/messages.py`;`core/records.py` 的 Persist 载荷不上 wire,保留。治理见 [wire.md](../wire.md);原型 `wsm_schemas.py` 曾作参考,已于 0027 拆除(见 git history)。

- [x] `app/wire/server.py`:`ServerMessage` 可辨识联合(`core/messages.py` 全集升级为 Pydantic:`type` 字面量/扁平/snake_case/core enums):`HandStarted`/`HoleCards`/`HandStatusChanged`/`PlayerActed`/`HandShowDown`/`HandEnded`/`UserStatusChanged`/`UserLeft`/`PlayerBoughtIn` + `ErrorMessage.from_err` — 0017(隐私=结构性缺位,非 field_serializer;见 0017 决策 2)
- [x] `app/wire/client.py`:`ClientMessage` 可辨识联合 = 已落地命令报文(身份不进报文):`SitDown`/`BuyIn`/`SetUserStatus`/`LeaveRoom`/`StartHand`/`PlayerAction` + `parse`(JSON→`ClientMessage`)+ `to_command(msg,origin,now)`(Receiver 盖 `origin=nick`、shell 盖 `now` 墙钟)— 0017
- [x] reduce 投影改产 `app/wire` DTO,删 `core/messages.py`;`tests/core/*` 改 import(字段同名;三处位置构造改关键字)— 0017
- [x] codegen:**自包含 Python 生成器** `scripts/gen_wire_ts.py`(无 node;`pydantic2ts` 不可用)→ `frontend/src/types/wire.gen.ts`(只读产物);漂移守门 `tests/wire/test_codegen_uptodate.py` 骑 `pytest` 门槛 + `--check` 供 pre-commit — 0017(见 0017 决策 3/4)
- [ ] 前端消费 wire.gen.ts(**延后,随前端 WS client 集成**):0017 已生成 `wire.gen.ts` 解锁前端按真类型写 WS client;但 `frontend/src/types/poker.ts` 是 **UI mockup 聚合类型 + 本地 mock 牌局逻辑**(非协议类型),且本批无 `Player`/`StateSnapshot` wire 类型——此刻删它只会破坏 mockup 无替代。删 poker.ts + 改组件归「前端 WS client + StateSnapshot」单元(见 0017 决策 8)
- [x] 协议指南 [wire-protocol-guide.md](../wire-protocol-guide.md):收发消息目录 + 一手牌时序 + 错误码用法 + 现有/待补(增量)+ 形状铁律(type 判别/snake_case/身份不进报文/acting_position=players 下标)— 0017 后补;**dev 连接握手段**(`ws://…/dev/ws?nick=`)已随 D 阶段端点补齐(guide §9,0018 落地 / 0047 truth-up 修正状态+路径);**0067 truth-up 补齐至 0066 全量**:§3 动态建房(杀 0049 前的 `NO_SUCH_ROOM` 失真)、§4 表情 `[code]` 渲染注、§10 REST 面(公开三读 + 登录 blob/`rotate` + 会话信封契约[分域密钥/seq 回显/重试=新 seq] + profile 三件)、§8 已交付/还没有刷新。后续随模块增量(归「持续项」)

## D · 最小明文 dev shell + 端点(前端真连联调)— 无加密,临时脚手架

> 串起已实现的 reduce,让前端连真端点跑通已落地流。国密信道(原 P5)最后替换本层明文握手/帧;明文端点标 `dev-only`,绝不上线。

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
- [x] 等大盲再入局时机(rules.md ①.7-10):`_start_hand` 中 BB 扫到 `wait_for_big_blind` 座位免费入局(`blinds.sweep_entrant` fixpoint,庄位定于 core_dealt 解循环依赖)+ 躲盲被堵(末尾把未发牌在座者重标 `new_here`,统一覆盖坐出/干等/断线跨手)+ wire `SitDown.wait_for_big_blind` 切片 — 0023(残留简化:空 core 停摆 / 单 est+单 waiter 仍下结构盲 / 任何原因错过一手都收费,见 changes/0023 + rules.md ① 实现细节)
- [x] `JoinRoom` + `Connect` + `StateSnapshot` — 0022 落地 **core + 出站快照 wire**:`_join_room`(装 `world.users`+WATCHING+`UserJoined`+快照)、`_connect` 重连(OFFLINE→按 world 推断恢复+快照)、`_state_snapshot`(座位/筹码/button/board/pot/acting/players + 收件人自有底牌,隐私=结构性)、wire `StateSnapshot`/`SeatView`/`UserJoined`(+`RoomStatus` 入 wire);0030 落地 client `join_room{room}` 报文 + Receiver 读 DB 富化 `uid`/`loaded`;0031 落地 `_connect` 顶替再连快照(在房在线 → 只私发快照对齐新连接)。**`ROOM_FULL` v1 不强制(待容量上限,见 changes/0022 决策 5)**
- [x] `RoomChat` + `ChatMessage`(房聊走 reduce,只读 Broadcast;文本非空/长度/限速归 shell 文本防护)— 0021
- [x] `SetSmallBlind`/`SetBuyIn`(房间参数配置)— 0043 落地 + **0044 放开授权(去房主)**:reduce `_set_small_blind`/`_set_buy_in`(**授权=任何在房成员**[含观战者,无房主,0044]、时机=仅两手之间 `HAND_IN_PROGRESS` correctness 门、big_blind 派生、≤0 兜底)+ shell `_guard_room_config` 按 `gameconfig.MIN/MAX_SMALL_BLIND`·`MIN/MAX_BUY_IN` 防上下限(core 不 import config)+ wire `set_small_blind`/`set_buy_in` client + `RoomConfigChanged` server + `StateSnapshot.buy_in` + codegen + 测(core 17 + shell guard 8 + 协议/配置补;共 419)。0044 删 `NOT_ROOM_OWNER` 码 + 重 codegen。房配不落库(storage.md),重启回 gameconfig 缺省;无房管理(用户明示不需要)
- [x] 动态房间(**用户明示设计:谁都可创建 / 空则消失**)— 0049 落地:`_join_room` 房不存在则用 `JoinRoom.create`(`RoomCreate{small_blind,buy_in,seats}`,shell 从 gameconfig 盖,core 不 import config)建空房再加入(创建者无特权,peer)+ `reduce()` 顶层「空房销毁」归一(成功命令后目标房 `users_in_room` 空 → `work.room=None` → commit 销毁,覆盖 leave/cleanup/手尾驱逐)+ `build_dev_world` 改空(无静态预置)+ Receiver `_build_join` 盖 create + 测(core 建/销 5:建房 / leave 最后一人销 / 非最后不销 / cleanup 退筹销 / 手尾驱逐清空销;共 445)。wire `join_room{room}` 不变(建房配置不进报文)。**余**:建房自定参(往报文加 create 字段)、房名冲突/建房上限(本规模不设),见 changes/0049

## 硬化 / 子系统(每模块补协议切片)

- [x] P4 delayDB:0024 落地 `WriteBuffer` 双缓冲(状态写按键覆盖 / 事件写追加 / `put` 单入口 `_state_key` 分流 / `swap` / `requeue` 更新者优先;test_persist 12);0025 落地 `PersistWriter`(`Persister` 协议 + `NullPersister`;`flush_once` 先 swap 后 await / 失败回灌 / 毒丸丢批 / `drain` 有界 + 节流;gameconfig DB 旋钮;接进 DevShell start/stop;test_persist_writer 11→13,共 254);0026 落地 `app/db/` SQLModel 模型(`User`(uid/nickname/points)、`HandRecord`(dedupe_key/start/end/pot)、`HandParticipant`((hand_id,uid) 复合主键 + FK)对齐 Write 载荷)+ Alembic 重定向 env.py(只导 app.db、`DATABASE_URL` 读 env、真 FK、`render_as_batch`、模板带 `import sqlmodel`)+ 删 4 原型迁移 + 新基线(sqlite 验 upgrade/downgrade 通)+ **Alembic 用法文档 [db-migrations.md](../db-migrations.md)**;**P4 三之二全落地**:`OrmPersister` 写路径(0028)+ DB-backed dev shell(0029)+ per-join wire-load(0030),见下三条;**0027 原型拆除已解除 metadata collision**。**delayDB 写+载入全链在 dev 跑通**(connect→join 读 DB→play→Persist→OrmPersister→DB);余 drain 边界细化(P8);`DATABASE_URL` 已进 `app/config`(0045)。
- [x] P4 三之二写路径(0028):`app/db/orm_persister.py`(`OrmPersister`:async session 一批一短事务、状态写**定向 UPDATE**(只盖 points、保 nickname)、事件写**SELECT-by-dedupe_key 再 INSERT** record+participants 幂等)+ `app/db/engine.py`(async engine/session,缺省 `sqlite+aiosqlite`,sqlite 装 `PRAGMA foreign_keys=ON`)+ `HandRecordWrite.end_time` 由 shell 在 dispatch 盖墙钟 + `aiosqlite` 依赖 + aiosqlite 穷举(test_orm_persister 11 + dispatch 盖戳 1)
- [x] P4 三之二 DB-backed dev shell(0029):`DevShell.setup()` async engine + `create_all` 建表 + 幂等种子 dev 用户进 DB + 从 DB 载入积分建 world + `OrmPersister` 替 `NullPersister` + 关闭 `engine.dispose()`;端到端冒烟(命令穿 gameloop → reduce → Persist → OrmPersister → 真 DB 行:买入 PointsWrite UPDATE / 一手牌 HandRecord+participants INSERT;test_dev_db_e2e 4)
- [x] P4 三之二 per-join wire-load(0030):wire `client.JoinRoom`(`join_room{room}`,身份/积分不进报文)+ Receiver `_build_join` 拦截读 DB(`app/db/queries.py` `load_user_by_nick`)富化 `uid`/`loaded` 构 `JoinRoom(room,uid,loaded)` + `to_command` JoinRoom 特例 raise + dev 流翻转(`build_dev_world()` 空房、退役启动整载、连接→大厅→`join_room` 载入,Receiver 传 `sessionmaker`)+ 重 codegen(`wire.gen.ts` 加 `JoinRoom`)+ 测(receiver join 读 DB / 全链 connect→join→buy→DB / protocol 注册;共 272)
- [x] 原型拆除:删原型五包(`pokertable`/`user`/`auth`/`handrecord`/`database`)+ 三入口(`main`/`app_route`/`init`)+ `config.py`(原型期配置,自 review 改判一并删)+ `docs_generator`/`extensibility`(共 27 文件);解除 `app/db` 与原型同名表 metadata collision(P4 三之二前置)+ 兑现「不留死代码」;全量设计文档去链历史化(README §2 表格 + 散链 14 处);基础设施配置(`app/config.py`)由 P8 配置收编新建(已于 0045 落地)— 0027
- [x] shell 硬化:背压(inbox/outbound 上限 + 队列满丢连)、顶替/重连 `StateSnapshot`、`tests/shell/` — 0031:背压主体早随 0018/0024 落地(有界 `inbox`/`outbound` + outbound 满丢连 + Timer/Receiver inbox 满 CRITICAL),本批补**最后一处缺口** `dispatch._drop_connection` 的 `inbox.put_nowait` 加 `QueueFull→CRITICAL` 守护(原裸 put 在 inbox 满时会崩唯一 GameLoop)+ `_connect` 顶替再连快照(承接 0022 重连;详见 P1 余项)+ tests/shell(bounded-inbox 丢连不崩落 CRITICAL 回归 + receiver 顶替端到端收快照)。**余:lifespan drain 硬化归 P8**
- [ ] P7 lobby/REST/messaging:~~`GET /lobby/rooms`~~(**0048 已落地**:`app/rest/lobby.py` `RoomMeta`+`list_rooms`+`make_lobby_router` 挂 `create_app`;唯一读 committed world 的 REST、纯同步投影原子读、dev 无鉴权;rest.md↔lobby.md 契约张力已调和)、~~leaderboard~~(**0050 已落地**:`GET /leaderboard` 读 DB 结算积分降序[同分 nick 定序]+ `db/queries.top_users_by_points` + `LEADERBOARD_DEFAULT/MAX_LIMIT` 入 gameconfig;REST DTO `LeaderboardEntry` 不进 wire)/~~hands(游标)~~(**0051 已落地**:`GET /hands?user=&limit=&before=` 读 DB,游标=`HandRecord.id`、user 过滤按参与者、DTO 带 participants+net、`HANDS_*_LIMIT` 入 gameconfig,见 0051)+ ~~room 过滤~~(**0052 已落地**:`HandRecord` 加 denormalized `room` 列 + 迁移 `010d8e8a08d7` + `?room=` 精确匹配,免 dedupe_key LIKE 对动态房名之脆弱)/profile:~~`/user/me`~~(**0062 已落地**:`POST /user/me` 走 P5 加密信封[首个消费者],`app/rest/profile.py` + `db/queries.load_profile_by_name`,见 changes/0062)/ ~~改密码~~(**0064 已落地**:`POST /user/password` 走信封,验旧 → 重算 `salt$rounds$digest` → **同步直写** `db/user_writes.update_password_hash`[鉴权列 DB 权威、不走 delayDB、与 PersistWriter 列不相交],错误分层 401/403/400/500,见 changes/0064)/ ~~改昵称~~(**0065 已落地**:`POST /user/nickname` 走信封,仅大厅[`Presence.current_room` 判 **DB** 当前昵称,有陈旧会话名测钉]→ **CAS** 直写 DB[`WHERE id AND nickname=old`,并发双改名输者 409 跳联动,防三处发散]→ `SessionStore.rename_nickname`[该账号全部会话]→ `ConnectionManager.rekey`[await 前捕获的对象,`is` 判定防误挂他人];撞名预查 409 + `IntegrityError` 兜底;错误分层 401/403/409/400[含首尾空白冒充面]/500;dev `?nick=` 加 DB 行守门;残留记档[旧 nick liveness no-op / 登录 racing 改名 TOCTOU / rename×join_room 幽灵占位(presence.md 记准)],见 changes/0065)——**profile 三件闭环(0062/0064/0065)**、~~房聊 shell 文本防护(非空 + `ROOM_CHAT_MAX_TEXT_LEN`)+ 令牌桶限速~~(**0033 已落地**:Receiver `_guard_room_chat` 空/超长/限速 → `INVALID_MESSAGE`/`MESSAGE_TOO_LONG`/`RATE_LIMITED` + `shell/ratelimit.TokenBucket` 每连接桶)、~~房聊环形缓冲 + `FetchRoomChat`~~(**0036 已落地**:`shell/history.RoomChatBuffer` 每房定长环形缓冲,dispatch 写 / Receiver `FetchRoomChat{room}` shell 直服务回 `RoomChatHistory`,不进 GameLoop)+ 私聊 DM:~~发路~~(**0038 已落地**:`DirectMessage` shell 路由 → 防护 + 解析 uid + 落库 `DMWrite`(未读)+ 在线投 `DMDelivered` / 对端不存在 `DMUndelivered`;`DMMessage` 表 + 迁移 `79d1fd60fc7f` + `dm_records.DMWrite` + OrmPersister 幂等 INSERT + `dm_bucket` 限速)+ ~~读路·游标写~~(**0039 已落地**:`DMMarkRead` shell 路由 → `DMReadCursorWrite`(状态写,按 (reader,peer) 覆盖,行非必存走 UPSERT)+ 对端在线回 `DMRead` 回执;`DMReadCursor` 表 + 迁移 `7ff9cb0a8db1`)+ ~~登录补收~~(**0040 已落地**:`deliver_dm_catch_up` (重)连读 DB → 补发未读 `DMDelivered`(`load_unread_dms`,尊重游标)+ 已读回执 `DMRead`(`load_read_receipts`),复用现有报文、不进 GameLoop、best-effort)+ ~~保留清理~~(**0041 已落地**:`PersistWriter.maybe_cleanup` 周期 → `OrmPersister.cleanup_dms` DELETE 已读满期私信,未读永不删、唯一写者不另起协程)**【私信收件箱功能闭环:发 0038/读游标 0039/补收 0040/清理 0041】**(见 [messaging.md](../messaging.md) + changes/0012/0038/0039/0040/0041)、~~presence 只读~~(**0037 已落地**:`shell/presence.Presence` 只读聚合 is_online/current_room/room_headcount/online_nicks + `ConnectionManager.rename`/`online_nicks`)、REST DTO→TS 走 `openapi-typescript`(无 node → 待解,见 wire.md;**后端端点** `GET /lobby/rooms` 已 0048 落地,余 leaderboard/hands/profile + 其 TS 生成)
- [x] 日志:GameLoop 边界审计 + 脱敏红线(底牌/密钥不进日志)— 0032:`shell/logsetup.py`(JSON/console formatter + contextvars 关联字段 filter + `setup_logging`,同步直写、QueueHandler-ready)+ GameLoop `handle` 边界审计(命令受理 DEBUG / 业务失败 WARNING / 未预期异常 ERROR+traceback / 事件类型计数 DEBUG / 手牌里程碑 INFO,关联字段 cmd_type/nick/room/hand_seq/epoch 绑定)+ lifespan 启动配日志 + `LOG_*` dev 常量(P8 env 化)+ 英文化既有中文日志 + 脱敏红线测(跑携底牌事件、断言牌面/deck 不入日志,共 290)。**余:QueueHandler 兜尾(实测尾延迟才上)**
- [x] 聊天表情(emoji,设计 [0034](changes/0034-emoji-catalog-design.md) + 实现 [0035](changes/0035-emoji-implementation.md)):`app/wire/emoji.py` 封闭目录(`EmojiCode` 12 项 + `EMOJI_CATALOG{label,glyph}`)+ `gen_wire_ts._emit_emoji_catalog` 无条件吐 + 前端 `utils/emoji.ts`(`tokenizeChat`/`chatToPlainText`,按 `[code]` 渲染、未知原样)+ 测(目录全覆盖 / code 形制 `[a-z0-9_]+` / meta 非空 / codegen 吐目录,共 311)。**后端纯透传、`ChatMessage`/`_room_chat` 不变、无新协议字段**;房聊现可用、私聊落地后自动适用
- [x] 配置收编:`gameconfig` 转 `GameConfig(BaseSettings)`(无代码默认 + `Field(ge/le/gt)` 边界 + `LOG_LEVEL`/`LOG_FORMAT` `Literal` 收敛 + 模块 `__getattr__` 委托单例,保持 `gameconfig.XXX` 访问不变)+ 新建提交基线 `app/poker.env.example`(两层 `env_file`:example 基线 / 本地 `poker.env` 覆盖,缺文件静默跳过、锚 `app/` 不依赖 CWD)+ `tests/test_gameconfig.py`(边界拒/缺字段崩/Literal/委托,14 测,共 390) — 0042。~~余项:`SetSmallBlind`/`SetBuyIn` 上下限~~(0043 落地)~~+ 基础设施 `DATABASE_URL`/JWT 收编进 `app/config.py`~~(**0045 落地**:`app/config.Settings`(`DATABASE_URL: str|None=None` 有安全 dev 默认、`env_file=service/.env`)+ `.env.example` 模板 + `engine.py`/`alembic/env.py` 都经 `settings` 读(env > `.env`,消除 .env↔alembic 错配)+ `test_config` 5,共 424;JWT 随 P5 无默认 fail-closed)。**配置两轨齐全**:游戏参数(poker.env+gameconfig)/ 基础设施(.env+config)

## P5 · 国密安全信道(**最后做**,替换 D 的明文层)

> **设计定案([changes/0057](changes/0057-p5-unified-encrypted-channel-design.md),据用户设计改)**:登录后**一切流量(ws + REST)走同一加密信封** `selector‖iv‖ct‖mac`(selector=session_id、seq 入 ct、入站 `MAC→decrypt→seq`、密钥=会话密钥),**解密即认证、去 JWT**。0053/0055/0056 不变;0054 逐帧原语内核复用、信封/顺序/密钥来源待改;已删未提交的 JWT 助手。

- [x] 密码哈希:`salt$rounds$digest` + `compare_digest`(**0053 落地原语**:`app/auth/passwords.py` `hash_password(pw,rounds)`/`verify_password(pw,stored)`/`_derive` —— 每用户随机盐 16B + N 轮 SM3 拉伸,verify 按存储轮数重放[改配置不废旧哈希] + fail-closed 非法串 + 常量时间比对;`PWD_HASH_ROUNDS` 进 gameconfig + env;`tests/crypto/test_passwords.py` 穷举 23 测,共 486)。余项 `User` 加 `name`/`hash_password`/`k_user` 列 + Alembic 迁移 `49417b108733` 已随 **0056** 落地(见下「登录握手」)——**本项闭环**
- [x] 登录握手:`/user/login` SM4 护住密码、返回**会话凭证**(session_id/session_token,**无 JWT**,见 [changes/0057](changes/0057-p5-unified-encrypted-channel-design.md))。**0055 落地会话表**:`app/auth/session.py` `SessionStore`(`create`/`lookup` 过期删 /`revoke`/`prune`)+ `Session{name,nickname,token,expires_at}`,内存 shell 态、时钟外移、`SESSION_TTL_SECONDS` 进 gameconfig + env;`tests/auth/test_session.py` 11 测,共 524。**0056 落地列 + 查询 + authenticate**:`User` 加 `name`/`hash_password`/`k_user`(均 nullable)+ 迁移 `49417b108733`;`load_user_for_login(name)->LoginUser` + `authenticate(hash,k_user,iv,blob)->LoginProof|None`(SM4 解 blob + verify_password,fail-closed);`tests/auth/test_credentials.py` 22 + `test_login_query.py` 6。**0059 落地登录端点**:`app/rest/login.py` `make_login_router`(`{name,iv,blob}`→load→authenticate→`SessionStore.create`→K_user 加密下发 `{session_id,session_token,exp}`,fail-closed 统一 401,**无 JWT**)+ `SessionStore` 进 DevShell + 挂 create_app;`tests/rest/test_login.py` 8(含 DB 错归 401),共 562。**0060 dev 种子 login-enable**:`seed_dev_users` 给 DEV_USERS 补 `name`=昵称/共享 `DEV_PASSWORD` 哈希(lru_cache)/共享 `DEV_KUSER`(dev-only)+ 回填 pre-P5 行;端到端登录 + 回填测,共 565。**0061 落地 ws 信道接线**:`/ws?sid=` 加密端点(查会话 → get-or-derive **挂 Session 的** `SecureChannel` → `Connection.channel` 引用)+ Receiver 收二进制帧 `open`(FrameError 关连接)/ Sender `seal` 出站 + 明文 dev `?nick=` 并存 + 逐会话 seq(跨重连挡重放);`tests/shell/test_secure_channel_wiring.py` 8 测(Sender seal↔客户端 open / Receiver open 穿管线 / FrameError 关连接 / `_channel_for` 缓存 / 逐会话 seq 挡重放 / `/ws` 路由注册 + 未知 sid 拒 4401 + 有效 sid 建加密连接),共 573。**0063 落地重放守卫(本项闭环)**:blob 形升级 `{password, client_nonce, ts}`(ts 必填,`authenticate` fail-closed)+ 双守卫相与(freshness `|now-ts|≤LOGIN_REPLAY_WINDOW_SECONDS`[ts 须**有限**数值,NaN/±Inf 拒] + `(name,nonce)` 去重 `app/auth/nonce.py` `NonceCache`,**条目 TTL=2W、严格过期才剪**[否则 ts 超前的 blob 有「条目先死、blob 还新鲜」重放缝,自 review 抓修],活在 login router、惰性剪枝、authenticate 后才查[探测包灌不进缓存,有测钉])+ 记档残余窗(重启清缓存 → freshness 窗内可复活一次);credentials +10 / nonce 5 / login +7 / 配置 +1 测,共 621(见 [changes/0063](changes/0063-p5-login-replay-guard.md))
- [x] 逐帧加密:`SecureChannel` 入站铁序 + 出站加密(**0054 落地原语**:`app/auth/channel.py` `hmac_sm3`/`derive_keys(token,nonce)`/`FrameError`/`SecureChannel`(`derive`/`seal`/`open`)—— encrypt-then-MAC 帧 `seq‖iv‖ct‖mac`、入站铁序[结构→seq>已见→验 MAC→才解密]、IV 每帧新鲜、序号每连接从 1 严格递增、跨连接密钥隔离;`WS_FRAME_MAX_BYTES` 进 gameconfig + env;`tests/crypto/test_channel.py` 穷举 23 测,共 511)。**0058 落地统一信封改造**:`derive_keys(session_token)`(去 server_nonce)+ `SecureChannel` 信封 `iv‖ct‖mac`(seq 入 ct)+ 入站序 `结构→MAC→解密→seq` + mac 盖 `iv‖ct`(selector 传输层剥)+ seq 按会话计;`test_channel.py` 重写 24 测,共 554。**0061 落地 Receiver/Sender 接线**:`SecureChannel` 挂 Session(逐会话、跨重连复用 → seq 连续)、`Connection.channel` 引用、Receiver `receive_bytes→open`、Sender `seal→send_bytes`、`/ws?sid=` 握手剥 selector 查会话、FrameError 关连接;明文 dev 帧并存(见上 0061 / [changes/0061](changes/0061-p5-ws-secure-channel-wiring.md))。**0062 落地 REST 信封**:抽无状态 `seal_envelope`/`open_envelope`(SecureChannel 委托)+ `derive_rest_keys`(info 03/04 与 ws 分域,杀跨信道重放)+ `ReplayWindow` 滑动窗(挂 `Session.rest_window`,REST 并发乱序不误拒)+ 响应 seq 回显绑定 + `app/rest/secure.py` 信封助手(`{sid,frame}` hex JSON,统一 401)+ 首个消费者 `POST /user/me`(`app/rest/profile.py`,信封后 DB 错如实 500)+ `REST_FRAME_MAX_BYTES`/`REST_REPLAY_WINDOW` 进 gameconfig+env;crypto 9(含 KDF info 字节 known-answer 钉契约)+ profile 14(含两旋钮端到端消费)+ 配置 2 测,共 598(见 [changes/0062](changes/0062-p5-rest-envelope-user-me.md))
- [x] `K_user` 双钥 + 每周轮换任务 + 版本/宽限 — **0066 落地**:`User` 扩列 `k_user`→`k_cur`(重命名,迁移 `b8ca88a687af` 手改 alter_column 保数据)+ `k_cur_ver/k_cur_until/k_prev/k_prev_ver/k_prev_until`(until=epoch 秒;`k_cur_until`=**到期应轮换排程**、登录不查[免 cron 迟跑锁死全员]、NULL=不排程[dev 种子];`k_prev_until`=宽限截止、登录**查**)+ 登录**双钥两次尝试**(先 `k_cur` 后宽限内 `k_prev`,**协议不带 key_version**——用户手输密钥无从知版本,偏离 auth.md 原设计已改文档;响应用**匹配键**加密 + `rotate=true` 旧钥提示)+ 轮换写 `rotate_kuser`(单 UPDATE 列到列原子搬移)/`issue_login`(首发/`--reset` 补发即换代清 prev)+ **管理员 CLI** `scripts/kuser_admin.py`(`list` 记账无键材料 / `rotate` cron 幂等轮到期者·`--name` 强制 / `issue` 生成口令+钥打 stdout=带外起点,进程内不轮换——新钥进日志违脱敏红线)+ `KUSER_ROTATION_DAYS`/`GRACE_DAYS` 旋钮 + 测(rotation 17 + login 双钥 7 + 配置 2,共 688)。轮换不动会话密钥(派生自 session_token),只影响后续登录。自 review 抓修:`rotate_due` 边轮边出 + 单账号失败隔离(攒批打印会吞已 commit 未导出的密钥)、`rotate_kuser` RETURNING 回版本(去 commit 后二次回读)、issue 版本如实回报
- [x] `tests/crypto/`:MAC 拒伪 / seq 拒重放 / 先验后解 / IV 不复用 — 随 0054/0058 `test_channel.py`(24 测)全覆盖(hmac 性质 / 派生 / round-trip / 先验后解[改 ct→bad_mac] / 重放 stale_seq / IV 每帧新鲜 / 跨会话 bad_mac / 结构 / fuzz);0061 补 ws 接线端到端集成(`test_secure_channel_wiring.py`:Sender seal↔客户端 open、Receiver open 穿 GameLoop、FrameError 关连接、逐会话 seq 挡重放)

## P8 · 收尾

- [x] `shell/lifespan.py` drain:关闭反序 drain(超 `DB_DRAIN_TIMEOUT_MS` 落 CRITICAL)— 0046:有界 drain 本体(timeout CRITICAL/毒丸/取消回灌)早随 0025 落 `PersistWriter.drain()` + 穷举测;本批补 `DevShell.stop()` **反序四步**(cancel Timer+GameLoop → 同步排空 inbox 在途命令 → cancel PersistWriter 循环 + `drain()` → cancel 各 Sender + `dispose()`)+ 集成测 `test_lifespan_drain`(inbox 排空落 DB / 缓冲 drain 落 DB / start→stop 不挂死 / 未 start 安全 / Sender-cancel / 并发交接 exactly-once,6 测,共 432)。**余**:端到端冒烟(前端↔后端一手牌,见下)
- [x] 端到端冒烟:前端 ↔ 后端走通一手牌全程 — 0078 落地(`npm run smoke`),0085 扩到加注/min-raise/三人边池(`npm run smoke:raise`);**明文 dev 那半已作废**:端点随 0086 退役,三条冒烟全走国密加密信道

---

## 审计跟进(0072,2026-07-14)— 台账见 [changes/0072](changes/0072-architecture-audit.md)

- [~] **0072·R1** 手牌记录跨房间世代撞键(**用户定案暂缓,2026-07-28**:「先不修」;已确认未修非接受,方案两案留档 0072):`dedupe_key="room:seq"` 同名房销毁重建/进程重启后与旧世代撞键 → 幂等 INSERT 静默丢新记录;启动时须带「销房重建 + 重启两路径」回归测试 —— 详见 [BUGS.md#BUG-2](BUGS.md)
- [ ] **0072·R2** Timeout staleness 跨手失效:epoch 每手归零,`Timeout` 补带 `hand.seq` 双键校验 + 构造交错的回归测试(可与 R1 同批) —— 详见 [BUGS.md#BUG-3](BUGS.md)
- [ ] **0072·D2-D5** 文档 truth-up 一批(仿 0047/0067):~~messaging.md §房聊历史 0071 残留旧段(D1)~~ **D1 已由 0075 文档重写连带解决**(四处反事实全消失)/ architecture.md 不变量 2 补「只读 committed world 豁免」判据(D2)/ connection.md·lobby.md 待定段陈旧(D3)/ 四处陈旧注释含两处 JWT 反事实(D4)/ 小项(D5)
- [x] **0072·C2** codegen 守门口径 — **0086 改实**:architecture.md/wire.md 两处都改成「pytest 守门;仓库没有 CI 也没装 pre-commit,提交规约见 dev.md」。**搭 CI 与否是独立决策,未做**(用户指出:提交规约本来就有文档,缺的是自动化不是约定)
- [ ] **0072·C3** `rest/lobby.py` 的 `big_blind=2*` 改引 `blinds.BIG_BLIND_MULTIPLE`(一行,可并入任意批)
- 注:0072·C1(前端消费 wire.gen.ts)已有 W 段既有项,不重复登记

**新增缺陷(第五次工作流 N 系列对抗验证坐实,均 medium;台账 0072「N 系列」节)**:
- [x] **0072·N1** 离房→flush 窗口内快速重进房静默回退积分 — **0073 已修**:运行期落库屏障(`JoinRoom` 载入前 `inbox.join()` + `PersistWriter.barrier()`,fail-closed);N1 主钉 e2e(同连接 + 跨连接 Cleanup 驱逐两路)+ 关屏障必红反证 + 三视角复审抓修毒丸在飞洞,712 全绿
- [x] **0072·N2** 慢客户端被丢弃只摘键 → 幽灵命令源 + 同 nick 双 Receiver — **0083 已修**:`Connection.receiver_task` + drop 时 cancel Sender 与 Receiver。**修法与登记时不同**:只关 ws 堵不住「读慢写健」的非对称慢客户端(关闭帧和数据一样发不出去),故改为 cancel(同步,不违反「dispatch 不 await」);顺带补上 `stop()` 收 Sender 时按 `online_nicks()` 遍历、够不着已被 drop 的连接那个泄漏
- [x] **0072·N3** GameLoop 兜底只罩 `reduce()` + 常驻协程死了无人告警 — **0083 已修**:`handle` 兜底提到罩住 checkout/commit/审计/派发 + GameLoop·Timer·PersistWriter 三条常驻协程挂 watchdog(非取消退出即 CRITICAL,兑现 log.md 早就写着的那条)。**定性更正**:对抗核实逐条走过后确认当前无可达抛出路径,是潜在缺口而非活的崩溃路径,详见 [BUGS.md#BUG-7](BUGS.md)
- [ ] **0072·N5** `SessionStore.revoke` 全仓零调用者——无登出端点/无管理员吊销通道,泄露应对(issue --reset)后已建会话仍活至 SESSION_TTL。补吊销通道(登出端点 或 name→sessions 索引供改密/reset 时撤销),或若确认 v1 不做则在 auth.md 显式记档「不吊销、靠 TTL+重启」
- [ ] **0072·N9** StateSnapshot 不投影 `room.entry_vote` → 顶替/重连快照清空进行中免盲投票面板、重连的必需投票人不知有票。给 StateSnapshot 加投票公开态投影(或 reduce 重连臂补发 FreeEntryVoteUpdated)
- [ ] **0072·N-e32** Broadcast 收件人取 commit 后成员表:LeaveRoom 触发 fold-to-one 终手时,离场者收不到同批 PlayerActed/HandShowDown/HandEnded(看不到自己参与底池的结算)。离场结算事件改 Personal 补发给离场者,或调整驱逐与结算广播的顺序
- 注:~~**N4**(Timeout 跨房)并入 **R2** 修复~~ —— **0090 两条一起修掉**:`Timeout` 改带三元身份 `(room, hand_seq, epoch)`,三项全等才新鲜;`room` 只作校验不作路由,硬规则 8 原样成立
- 注:**N7**(每房一 GameLoop「core 不变」承诺过宽)、**N-r4/N-r6/N-e21/N-d33/N-dev22 及 N-d8~N-d29 共 12 条文档漂移**并入 D 批 truth-up;**N-r5 已 REFUTED 不采纳**
- [ ] **0072·N-低危设计边角**(low,择机):N-e9 DM 游标无单调防护 / N-e10·N-e11 db-migrations.md 示例配置致启动崩·违自家铁律 / N-e16 `_evict` 不清 `waive_entry_for` 致离房重进免盲 / N-e26 `scripts/scripts.py` 原型孤儿脚本删除 / N-e34 NullPersister 无生产消费者 / N-e35 Presence 三方法零消费者 / N-e36 profile.py 手抄 `_NICKNAME_MAX_LEN` 二份事实源 / N-e38·N-e40 演进面与快照 min-raise 记档

## 代码缺陷排查(0074,2026-07-29)— 台账见 [changes/0074](changes/0074-code-defect-hunt.md)

> 第六轮排查:0072 已把文档一致性问题查到收敛,本轮改找纯代码缺陷,专攻此前从未审的面(国密库内部/真扑克语义/reduce 崩溃点/codegen/迁移/CLI/前端)。验证纪律升级为「验证者须实跑 repro」。
> ⚠️ 本轮曾把 rules.md 明写的设计决策当 bug 改掉(0074·B,已回滚)。**凡动行为必先读该行为的设计文档**;「A/B/C 都做了 X 唯独 D 没做」时先假设 D 是有意的。教训见 [changes/0074](changes/0074-code-defect-hunt.md)「反思」。

- [x] **0074·A** `authenticate` 巨整数 ts → `math.isfinite`/`float` 抛 OverflowError 逃出 try(校验在 try 外)→ 端点 500 破 fail-closed + **「500 vs 401」成 K_user 猜测预言机** — **已修**(ts 校验改 `try: float() except OverflowError`)+ 变异验证
- [~] ~~**0074·B**~~ `_disconnect` 不重算免盲投票 — **误报,已回滚**:rules.md ①.15 与实现同批(0020)明写「不为断线单独触发通过」,是**有意设计**(断线可逆、占座窗口内可重连,全票制下按减员结算等于剥夺其否决权;离场/坐出才不可逆)。已补**反向钉** `test_voter_disconnect_does_not_trigger_vote` 防再犯
- 注:**裸库脆弱面记档**——`ttxsgm` 的 SM4 去填充无校验、非对齐密文抛异常(实跑复现),当前被各 app 入口守卫挡住(MAC 先行 / 长度预校验)故非缺陷;**日后新增任何直喂 `sm4_cbc_*` 的入口,必须自带同款长度守卫**
- [x] **0074·C** 改昵称「仅大厅可改」检查与内存联动之间隔两次 DB await:窗内 JoinRoom → world 挂 old_nick 而 DB/会话/连接键变 new_nick,**四处永久发散**(幽灵成员收不到广播 / 用户一切命令 NOT_IN_ROOM 无法自救 / 座位筹码永不回收 / 可二次进房复制积分)— **已修**(窗后复查 + CAS 回滚 + 403)+ 变异验证
- [x] **0074·D** `PersistWriter.drain()` 的 deadline 只在循环顶部判,罩不住 flush 本身 → DB 挂起时 drain 无限等、`stop()` 永不返回、进程无法优雅退出(非 0073 引入,0025 起就在)— **已修**(`wait_for(flush_once, remaining)` + 节流收进 deadline)+ 变异验证
- [x] **0074·E**(high)顶替链 A←B←C → 复活已 OFFLINE 用户 + 抹占座清理表 → 座位/筹码永久泄漏 — **0083 已修**:`_displace` 后复查 `is_current`,不是当前连接就地退出(不起 Sender / 不拆表 / 不投 Connect)。三连顶替交错回归测钉住「不复活 + 清理照常触发 + 桌上筹码退回」,并做了反向变异验证
- [x] **0074·F**(medium)改昵称窗内 ws 顶替 → `rekey` 只改死对象,活连接永久挂旧键 — **0083 已修**:连接改为全部 await **之后**当场按 old_nick 查(那之后到 rekey 全程同步)+ 归属校验(按会话账号名;dev 明文无会话则认本人)。两个方向各一条回归测:窗内顶替要重挂到活连接、窗内他人占走旧键不许误挂
- [x] **0074·G** 改昵称落在 DM 路由 DB await 窗内:`uids` 用旧 nick 建表却用被 `rekey` 就地改写的 `conn.nick` 查 → 私信静默不落库 + 假 INTERNAL(`route_dm_mark_read` 同款)— **已修**(进路由即快照 nick,建表/查表/投递全程同源)+ 变异验证
- [x] **0074·H** `_buy_in` 的「局中」判据用 `users_in_room is PLAYING` 而非 `_player_in_hand` → 手内掉线者可给已锁筹座位加筹,手牌记录 initial/final 凭空多筹码 — **已修**(判据换 `_player_in_hand`)+ 变异验证。**实跑推翻了「`_set_user_status` 同款错位」**:那处被 `userself_can_change_to` 挡住(OFFLINE 起身 → INVALID_STATUS_TRANSITION),不可达
- [x] **0074·I/J**(medium)关闭路径两处失守 — **0083 已修**:lifespan `yield` 包 `try/finally`(关闭无条件跑到 `stop()`);`_cancel_and_await` 按 `current_task().cancelling()` + `t.cancelled()` 区分「我 cancel 的子任务」与「取消冲我来的」,后者上抛。注:cancel 一个正等着别的 task 的 task,asyncio 会连它等的 future 一起 cancel ⇒ 只看 `t.cancelled()` 判不出来,`cancelling()` 不可省(有测钉)

## 前端(0076 起由本团队开发)— 台账见 [changes/0076](changes/0076-frontend-merge.md)

> 上游 `YangBaiii/poker_platform@f7463fe` 的 `frontend/` 已合入(79 文件 / +5602 行):登录页 + 大厅页 + 牌桌页 + shadcn/ui + 54 张牌面图。
> **只合前端**:上游的 `service/` 是 0027 已拆除的旧原型,合了就是把重构退回去。`.next/` 构建缓存也没合(已补进 `.gitignore`)。
> ⚠️ 合入的 1200+ 行页面代码**一行都没跑过**——本机无 node,连 `tsc --noEmit` 都跑不了。

- [x] **0076·M2** 装 node 工具链(Node 24.19.0 装在 `~/.local/node`,无 sudo)— **0077 已解决**,`build`/`type-check`/`test` 三项全绿;顺带抓到两个静态检查看不见的真错误(tsconfig `target: es5` 卡住 `matchAll`;`/game` 的 `useSearchParams` 未包 Suspense 致构建失败)
- [x] **0076·M1** 旧原型端点 — **0077 已解决**:删掉 `src/lib/api.ts`,新建 `src/transport/`(login 走 `POST /user/login` 加密信封;公开读接 `/lobby/rooms`·`/leaderboard`)。登录页、大厅页已接真后端
- [x] **前端接 WebSocket** — **0078 完成**:`src/store/` 快照为真相 + 事件增量;牌桌页完全由服务器驱动,本地发牌/街道推进/牌力计算已从仓库删除(不是注释掉)
- [x] **端到端冒烟** — **0078 完成**:`npm run smoke` 用前端自己的加密代码对真后端跑通登录 → ws → 进房 → 入座 → 买入 → 准备 → 开局 → 一手牌 → 聊天 → 离桌,并验底牌隐私、seq 单调、离桌后筹码守恒;可重复跑
- [x] **传输层补测** — **0078 完成**:46 项单测(加密向量 29 + 状态归并 + seq 纪律)
- [x] **0078·A** 「上次会话残留」自愈 — **已完成**:`store/joinFlow.ts` 的 `decideJoinMessage` 判「先退再进」且只做一次;`scripts/smoke-stale-room.mjs` 在真后端验过
- [x] **0078·B** 冒烟扩展到完整摊牌 — **已完成**:preflop → flop → turn → river → 摊牌比牌 + 筹码守恒。**过程中抓到真 bug**:`acting_position` 是 `players[]` 下标不是座位号,我的 `isMyTurn` 拿它当座位号用了(单测夹具恰好让两者相等所以没抓到,已把夹具改成下标≠座位号)
- [x] **0078·D** 冒烟再扩 — **0085 做掉两项**:加注与 min-raise、多人边池已由 `npm run smoke:raise` 覆盖(两人加注→再加注钉住 `last_bet+max(last_raise_size,BB)` 的正反例;三人短码 all-in **且随后再加注**才算分层,判据是「分配总额 > 主池上限」)。**余**:断线重连后 seq 继续累加 —— **0087 已验**(浏览器里真断一次线,重连后发出的命令服务器照收,且全程没有一条连接被 4400 关掉)
- [x] **0078·C** 真实界面验证 — **0079 完成**:Playwright 4 个用例走通登录 → 大厅 → 进房观战。**一次抓出三个问题**:后端没配 CORS(浏览器连登录都发不出去)、空大厅没有进房入口(动态建房下是死路)、ws 在组件卸载时的竞态
- [x] **0079·A** 浏览器里两人同桌 — **0080 完成**:入座 → 买入 → 准备 → 开局 → 弃牌 → 手牌结束。**又抓出三个缺陷**:空座位不渲染导致观战者无法入座、开局后 `playerHands[别人座位]` 为 undefined 致整页白屏、行动按钮在别人回合仍可点却静默无反应。顺带修了 `UserStatusChanged` 只处理三种情形之一(入座事件被整条吞掉)
- [x] **0080·A** 界面走完整牌局 — **已完成**:`e2e/showdown.spec.ts` 两人 Check/Call 推进到手牌结束,6 个浏览器用例全绿。顺带记录一个工具链坑:`npm run dev` 跑着时执行 `npm run build` 会冲掉 dev server 的 `.next` chunk,症状是页面 200 但 React 不水合
- [x] **0080·B** 界面上验加注与 min-raise — **0088 补齐 min-raise**(下限上 wire 后,浏览器里「别人大额加注之后不填金额直接点 Raise」被服务器接受,输入框的 `min` 也断言成服务器给的那个数);**0085 做掉加注**:`e2e/raise.spec.ts` 在真浏览器里输入金额点 Raise,断言两边底池都变大(广播回来的,不是本地画的);顺带发现**底池从来没渲染过**(`state.pot` 算了不用),已补。**余**:三人以上与边池的**界面**表现仍未验(界面只显示一个总底池,不显示分层)
- [x] **0079·B** 断线重连在浏览器里验 — **0087 完成**:`e2e/reconnect.spec.ts` 两个用例(掉线重连 / 同账号别处登录)。seq 跨重连继续累加与快照对齐**本来就对**,被验证的是它们;而**一次抓出四个缺陷**:每次重连都把自己从座位上退下来(`ALREADY_IN_ROOM` 被当成「挂在别的房间」)、开局底池显示 0、整轮 preflop 的跟注都发成 `bet(0)` 被拒(前端自己推「换街即清零」,而开局那条 `PRE_FLOP` 上盲注已下)、被顶替后两边无限互顶(顶替关闭码是 1000,客户端分不出掉线,新增 **4409**)。顺带发现 `showdown.spec.ts` 是假绿的(推进循环一路空转到 `ACTION_TIMEOUT`),已改真
- [ ] **0076·M7** 协议面换 `wire.gen.ts`,`poker.ts` 退回纯 UI 用途(`chips`/`phase` 与后端 enum 的漂移仍在;新合入的 `game/page.tsx` 还在用它)。这条即上文「前端消费 wire.gen.ts」的落地时机
- [ ] **0076·M3** Tailwind 配置 v3/v4 并存:`globals.css` 已是 v4(`@import "tailwindcss"`),`tailwind.config.js` 仍是 v3 风格且 v4 不自动读它 → 加 `@config` 或迁进 CSS 的 `@theme` 并删文件(`components.json` 也仍指着它)
- [ ] **0076·M4** `layout.tsx` 的 `import '/src/styles/globals.css'` 用根绝对路径,不稳;惯用写法是 `'@/styles/globals.css'`(合并前本仓那句 `'./styles/globals.css'` 本就是坏的)。待有 node 能验证时改
- [ ] **0076·M5/M6**(low)`package.json` 声明约 50 个 Radix 包、实际只用 2 个,可裁剪;`src/pics/poker-room.png` 单张 3.4M,建议转 WebP/AVIF

## 前端页面与功能(0081 起)— 台账见 [changes/0081](changes/0081-lobby-hub-settings-history-dm.md)

> 设计规则(用户定):**大厅是枢纽,每个模块在大厅只给摘要,点进去是细分页面承载全量与操作。**
> 已落地 5 页:登录 `/` · 大厅 `/lobby` · 牌桌 `/game` · 账号设置 `/settings` · 手牌历史 `/history`;私聊是全局右侧抽屉,不是页面。

- [x] **0081** 大厅枢纽化(摘要+入口、椭圆桌=当前房间可切换、排行榜标注不含桌上筹码)+ `/settings`(含 K_user 轮换出口)+ `/history`(游标翻页)+ 私聊抽屉。协议命令覆盖 9/15 → 11/15
- [x] **0081·A** 结算结果与连接状态上界面 — **已完成**:`HandResult`(赢取与退还分开显示,新一手自动清)+ `ConnectionBanner`(只在异常时出现,断线时说明座位筹码会保留)。浏览器用例已断言面板真的弹出
- [x] **0081·B** 免盲投票面板 + 房间参数配置 — **0082 完成**,协议命令覆盖 **15/15**;顺带补了错误码中文文案(此前把 `NOT_ENOUGH_PLAYERS` 这种机器码直接显示给用户),并由测试保证覆盖协议全集
- [x] **0082·A** `new_here` 缺传达渠道 — **0084 已解决**:`UserStatusChanged` 加 `new_here: bool | None`(未就座为 None,与 seat_position 同语义)+ `_start_hand` 末尾重标之后对**值真的变了**的座位各补一条广播(排在 HandStarted/HoleCards 之后,稳态每手 0 条)。前端删掉硬写的 `new_here: true`(那是替服务器裁定规则)、座位上标「等入局」;**开票入口仍不预判**——候选能如实显示了,但「有没有合格投票人」仍是规则,交服务器裁决。反向变异 5 处含一条端到端:去掉补广播后浏览器里标志真的挂着不掉
- [x] **0082·B** 免盲投票走完整流程 + 房间买入额越界 — **0089 完成**:浏览器里把票投到**全票通过**,断言三方都看到结果、新人下一手真被发牌且底池只有 SB+BB;买入额越界被服务器拒且当前值不变,合法值改完就是入座时真买进去的数。**抓到两件**:`free_entry_vote_closed` 此前只被用来关面板(`passed`/`waived` 直接丢掉,面板凭空消失,没人知道结果);以及「结束时还停在手牌里的用例会卡住下一个用同名账号的用例」——局中的 `leave_room` 要等手牌打完才驱逐,单跑绿、全套必红(已给需要真进房的用例配专属账号 `gina`)
- [ ] **0081·C** 私聊两人真实收发的浏览器验证;历史页有真实记录时的渲染验证;设置页改密码/改昵称的实际执行验证(需想清楚怎么隔离 dev 账号状态)
- [ ] **0081·D** 排行榜详情页(大厅「查看完整排行」目前标注「待建」且不可点,没有造假链接)

## 持续项(随时回看)

- [ ] **协议增量交付**:每落一个模块 → 补该模块 wire client/server 切片 + 重 codegen,前端跟随(见 [changes/0016](changes/0016-replan-wire-first.md))
- [ ] 文档与实现漂移时**改文档**并在 changes/ 记录(最近一次全量 truth-up:[0047](changes/0047-doc-code-truth-up.md) —— Dispatcher 抽取 + 去房主注释 + 陈旧占位语)
- [ ] **变动涉及前端可见面(协议/连接语义/关闭码/加密细节)→ 同一变更内同步 [frontend/BACKEND_GUIDE.md](../../../frontend/BACKEND_GUIDE.md)**(用户指示,0070 起;协议指南 wire-protocol-guide.md 照旧)
- [ ] 新增可调参数 → 进 `gameconfig` + env + example(不留裸字面量)
- [ ] 新增持久化实体 → 归「状态写 / 事件写」,不新开通道
