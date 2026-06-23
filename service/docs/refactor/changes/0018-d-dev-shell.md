# 0018 · D 阶段:最小明文 dev shell + 端点(串起已实现 reduce,前端真连联调)

日期:2026-06-23 · 范围:新建 `app/shell/{gameloop,dispatch,connection,sender,receiver,timer,persist,lifespan}.py`、`app/gameconfig.py`;`app/core/reduce.py` 加最小 `Connect` 处理 + `ErrorCode.INVALID_MESSAGE`;`tests/shell/`;文档(`TODO.md` D 段、本记录)。承 [0016](0016-replan-wire-first.md) 重排表第 2 步、[0017](0017-wire-first-batch.md) 协议。**明文 dev 脚手架,标 dev-only,P5 国密信道落地即替换。**

## 背景 / 目标

W 阶段(0017)出了协议单一事实源 + `wire.gen.ts`。D 阶段把已实现的 reduce 用最小 shell 串起来,挂一个**明文 WS dev 端点**,让前端按 [wire-protocol-guide.md](../../wire-protocol-guide.md) 真连联调。**无加密、无鉴权**(国密信道原 P5 推到最后,见 0016)。

## 关键设计决策(批判性 + 与文档对齐)

1. **分层照搬 [architecture.md](../../architecture.md)/[connection.md](../../connection.md)/[timer.md](../../timer.md)**:GameLoop 唯一状态写者(`cmd=await inbox.get()` → `checkout` → `reduce` → 成功 `commit`+`dispatch`、失败/异常 `send_error`,处理期间不 `await`、只 `put_nowait`);`Dispatcher` 路由 `Broadcast`/`Personal`/`Persist`/`TurnChanged`/`ClearAction`(B 组同步调 Timer);`ConnectionManager` register/unregister/is_current/get + 顶替;per-connection `Sender` 严格保序;`Timer` 两表(`_action` room 键 / `_liveness` nick 键)+ tick 投 `Timeout`/`Cleanup`(staleness 由 reduce 兜,已 core 测)。

2. **dev bootstrap:预置用户进 dev 房,绕开延后的 `JoinRoom`**。要 sit/buy/play,用户须先在 `world.users` + `room.users_in_room`(由 `JoinRoom` 载入,但其完整实现 + `StateSnapshot` 是延后的 P1 余项)。dev `lifespan` **预置**一个 `dev` 房(空座 + `small_blind`)+ 一组 dev 用户(`UserState(room="dev", points=…)` + `users_in_room[nick]=WATCHING`)。`?nick=` 必须是预置 dev 用户。这避免在 D 阶段提前实现 `JoinRoom`,纯脚手架。

3. **最小 `Connect` = no-op(加 reduce handler,避免 INTERNAL)**。Receiver 接入投 `Connect(nick)`,但 reduce 当前无 `Connect` 臂 → 落 `case _` 的 `Err(INTERNAL)`,每次连接都会回错。加最小 `_connect`:`return [], None`(预置用户已 WATCHING 在房,core 无事可做)。**重连恢复 + `StateSnapshot` 仍延后**(P1 余项「JoinRoom+Connect+StateSnapshot」)——dev 冒烟是「连上即玩」,不测断线重连。

4. **解析错误码 `ErrorCode.INVALID_MESSAGE`**。[error.md](../../error.md):协议/解析错误在 Receiver 层(未成合法 Command)直接构造 `ErrorMessage` 投本连接。`ErrorCode` 当前无对应码,加 `INVALID_MESSAGE`(机器码;Receiver 收到非法 JSON/未知 type 时回发)。

5. **`app/gameconfig.py`(新,目标结构位)带默认值,不依赖 env**。timer.md 要 `ACTION_TIMEOUT`/`LIVENESS_TIMEOUT`/`TIMER_TICK_MS` 走 config;旧 `app/pokertable/gameconfig.py` 绑一个**不存在的** `poker.env`(必填字段、import 即崩),是旧原型物。新建 `app/gameconfig.py`(README §3 目标位)用**带默认值**的简单常量(dev 友好、import 不需 env);加 `INBOX_MAX`/`OUTBOUND_MAX` 背压上限。P8「配置收编」时接 env + example。

6. **persist 是桩**:`shell/persist.py` 最小 `WriteBuffer`(内存 list + 日志),`put(payload)` 同步入缓冲,先不接 DB(P4 换双缓冲 + PersistWriter + ORM)。

7. **明文帧**:Receiver `wire.client.parse(text)` → `to_command(msg, origin=nick, now)`(墙钟 shell 盖);Sender `ws.send_text(msg.model_dump_json())`。无 `SecureChannel`。每帧 `timer.heartbeat(nick)`。退出 `unregister` + `is_current` 才投 `Disconnect`。

## 打算改什么(开工前)

- `app/gameconfig.py`:dev 可调参数(超时/tick/队列上限),带默认值。
- `app/shell/timer.py`:`Timer`(on_turn_changed/clear_action/heartbeat/drop_liveness + `run()` tick → Timeout/Cleanup)。
- `app/shell/persist.py`:`WriteBuffer` 桩(put + drain/snapshot 供测试)。
- `app/shell/connection.py`:`Connection`(明文 outbound,无 channel)+ `ConnectionManager`(register/unregister/is_current/get)。
- `app/shell/dispatch.py`:`Dispatcher`(world/conns/persist/timer/inbox)→ dispatch(event) + enqueue + drop_connection + send_error(Err→origin)。
- `app/shell/gameloop.py`:`GameLoop`(inbox 串行 → checkout → reduce → commit/discard → dispatch;异常归一 `Err(INTERNAL)`)。
- `app/shell/sender.py`:`sender_loop(conn)` per-connection,严格保序;队列满丢连。
- `app/shell/receiver.py`:dev 明文握手(`?nick=`)→ register(顶替)→ 起 Sender → Connect → 收帧 parse→Command→inbox + heartbeat → 退出清理。
- `app/shell/lifespan.py`:预置 dev 房 + dev 用户、起 GameLoop/Timer、FastAPI app + dev ws 端点。
- `app/core/reduce.py`:加 `case Connect(): return _connect(...)`(no-op)+ `ErrorCode.INVALID_MESSAGE`。
- `tests/shell/`:GameLoop 回滚 + dispatch 路由 + 错误回发、ConnectionManager 顶替/身份判定、Timer 触发/键、Sender 保序、端到端冒烟(fake ws 跑通 sit→buy→ready→start→action→showdown)。
- `TODO.md` D 段勾项 + 本记录。

## 实际改了什么

- **`app/shell/timer.py`(新)**:`Timer`(`on_turn_changed`/`clear_action`/`heartbeat`/`drop_liveness` + `run()` tick)+ `_ActionDeadline`;`now()` 用 `time.monotonic`(无需运行中事件循环,timer.md 许可);`tick()` 抽出供同步测试;`put_nowait` 经 `_fire` 守 inbox 满(落 CRITICAL 不崩协程)。
- **`app/shell/persist.py`(新)**:`WriteBuffer` 桩(`put`/`snapshot`/`__len__`,内存 list)。
- **`app/shell/connection.py`(新)**:`Connection`(明文 outbound,无 `SecureChannel`;`create` 建有界队列)+ `ConnectionManager`(`register` 返回被顶旧连接 / `unregister` 仅删自己 / `is_current` / `get`)。
- **`app/shell/dispatch.py`(新)**:`Dispatcher.dispatch`(`Broadcast`/`Personal`/`Persist`/`TurnChanged`/`ClearAction`)+ `send_error`(Err→origin)+ `_enqueue`(满则 `_drop_connection`:unregister + 投 Disconnect)。
- **`app/shell/gameloop.py`(新)**:`GameLoop.run`/`handle`(checkout→reduce→commit/discard→dispatch;异常归一 `INTERNAL`;`handle` 抽出供同步测试)。
- **`app/shell/sender.py`(新)**:`sender_loop`(outbound→`ws.send_text(model_dump_json)`,严格保序;cancel 正常退出)。
- **`app/shell/receiver.py`(新)**:`run_receiver`(登记/顶替→起 Sender→`Connect`→收帧 `parse`→`to_command(origin,now)`→`await inbox.put`→退出 `is_current` 才投 `Disconnect`)、`_to_command`(解析失败回 `INVALID_MESSAGE`)、`_displace`(cancel 旧 Sender + 关旧 ws)。
- **`app/shell/lifespan.py`(新)**:`build_dev_world`(预置 dev 房 + dev 用户 WATCHING)、`DevShell`(装配 inbox/conns/persist/timer/dispatcher/gameloop + start/stop)、`create_app`(FastAPI + `/dev/ws?nick=` 端点)。
- **`app/shell/world.py`(改)**:+ 模块说明(checkout/commit 职责)。
- **`app/gameconfig.py`(新)**:超时/tick/队列上限 + dev 房/用户常量 + `ERROR_DETAIL_MAX_LEN`,皆带默认值(import 不依赖 env)。
- **`app/core/reduce.py`(改)**:+ `case Connect()` + `_connect`(no-op,重连/StateSnapshot 延后)。**`app/core/errors.py`(改)**:+ `INVALID_MESSAGE`。**`frontend/src/types/wire.gen.ts`(重生成)**:`ErrorCode` 联合 + `INVALID_MESSAGE`。
- **测试 `tests/shell/`(新)**:`_fakes.py`(FakeWS + Shell 装配 + drain)、`test_connection`/`test_timer`/`test_gameloop`/`test_dispatch`/`test_sender`/`test_dev_smoke`/`test_receiver`(含异步顶替生命周期)——共 26 测试(178 全绿)。
- **文档**:`connection.md`(`TurnChanged` 模式字段序)、`TODO.md`(D 段全勾)。

**偏离计划**:范围与「打算」一致(8 shell 模块 + gameconfig + Connect/INVALID_MESSAGE + shell 测 + dev 端点)。**超出「打算」的部分全部来自对抗式 review 的确认项**(见下「自 review」):`receiver` Connect 移入 try(QueueFull 泄漏修复)、`timer._fire`/`lifespan.stop` 抗崩、`gameconfig.ERROR_DETAIL_MAX_LEN`、`world.py` 模块说明、`connection.md` 字段序、异步顶替测试。延后项(`JoinRoom`/`Connect` 重连/`StateSnapshot`)如计划保留。

## 自 review

方法:逐维过 [review.md](../../review.md) 7 维 + 跑**对抗式 review 工作流**(7 维各派审查者 → 每个候选再派独立反驳者)。结果:**16 候选、12 确认、4 驳回**。确认项已全部当场处理(代码/文档/测试/账本)。逐项:

**已修(代码 / 测试)**
- **[1 major] `receiver` QueueFull 泄漏**:`inbox.put_nowait(Connect)` 在 try 之前——若 inbox 满则抛、连接已登记 + Sender 已起却不清理(半初始化泄漏)。**改**:Connect 移入 try 用 `await inbox.put`(背压安全),`finally` 必清理(cancel Sender + unregister);`finally` 的 Disconnect 也加 QueueFull 守护,清理不抛。
- **[2 nit] 关闭健壮性 + Timer 抗崩**:`Timer.tick` 的 `put_nowait` 抽成 `_fire`,inbox 满时 **落 CRITICAL 不让 Timer 协程崩**(对齐 architecture.md「inbox 满 = CRITICAL」);`DevShell.stop` 兼捕 `Exception`(协程意外死亡也不阻断关闭)。
- **[9 major] 异步顶替生命周期漏测**:补 `test_async_displacement_old_connection_exits_silently`——新连接接管、旧 ws 关、旧 Sender cancel、旧 Receiver `is_current=False` **静默退出不投 Disconnect**(否则误标新连接 OFFLINE);并验顶替后新连接正常收发。`FakeWS.close` 加哨兵唤醒阻塞的 `receive_text`。
- **[7 minor] 魔法数 200** → `gameconfig.ERROR_DETAIL_MAX_LEN`。
- **[6 minor] `Connection.sender_task` 缺注释** / **[8 minor] `shell/world.py` 缺模块说明** → 补。

**已修(文档)**
- **[4 minor] connection.md `TurnChanged` 模式字段序**:`case` 模式改为与 `events.py` 数据类同序(`room, acting_nick, epoch`)。

**记为「已记录的示意例外」(不改)**
- **[5 minor] timer.md 伪码用 `nickname`,实现用 `nick`**:[README §0](../README.md) 明定「文档里的 .py **伪码是示意**,字段名/签名以实现为准」——timer.md 伪码的字段命名属此类,**非漂移**;实现(`shell/timer.py`)以 `nick` 为准。不为示意伪码做命名 churn。

**驳回(4,核实后不成立)**
- timer/dispatch 的 `put_nowait` QueueFull「使协程崩」:inbox 满本就是架构定义的 CRITICAL 故障态(GameLoop 已卡死),非可优雅处理的常态;且 Timer 现已加 `_fire` 守护。`receiver` finally 的 Disconnect QueueFull 同理(已加守护)。`delayDB` 术语:项目刻意命名(storage.md/db.md),非英文混乱。

**逐维结论**
- **① 分层/不变量**:core 仍纯(`grep app/core` 无 forbidden import;`_connect`/`INVALID_MESSAGE` 是纯 core 加项);GameLoop 处理期间不 await、dispatch 只 `put_nowait`/同步调 Timer;对外只经 Sender 队列;`_connect` 是真 no-op、不碰工作副本。
- **② 代码↔文档**:偏离均记本篇 + connection.md 已同步;TODO D 勾项与产出一致。
- **③ 文档一致**:`time.monotonic` 经 timer.md:19 明许;dev 端点/握手与 0016 D 详化一致。
- **④ 数据模型**:`build_dev_world` 用 `Room(seats,small_blind,buy_in)`/`UserState(uid,nickname,points,room)` 必填齐;预置用户 WATCHING + `users_in_room` 一致(无座位但可 sit/buy,与 `_sit_down`/`_buy_in` 校验相容);`INVALID_MESSAGE` 契合 ErrorCode 语义;`ACTION_TIMEOUT(15)≪LIVENESS_TIMEOUT(90)`。
- **⑤ 规范**:无死代码、无裸魔法数(均入 gameconfig);8 个 shell 模块无未用 import(`-W error` import 复验);命名一致、英文可提交。
- **⑥ 测试**:178 全绿(144 core + 8 wire + 26 shell;新增异步顶替测试);shell 测只验接线/保序(rollback、dispatch 路由、顶替身份、Timer 触发/epoch、Sender 保序、async 端到端、慢客户端丢连)。
- **⑦ 账本**:打算 = 实际;偏离均记;提交将引用 `0018`、全英文。

> 批判性:dev shell 是**临时明文脚手架**(dev-only),`Connect` no-op / dev 预置用户绕开延后的 `JoinRoom`+`StateSnapshot`,均显式标注;P5 国密信道落地即替换握手/帧,dispatch/GameLoop/reduce 不变。

## 待办 / 下一步

- **本批延后(有意,已记决策 2/3)**:`JoinRoom`(真·进房载入)+ `Connect` 重连恢复 + `Personal(StateSnapshot)` 整桌快照 → P1 余项;dev 预置用户暂绕开。
- **dev shell 限制(可接受,脚手架)**:`_drop_connection` 不主动关 ws/cancel Sender(靠下次 IO 错误兜);inbox 满是架构定义的 CRITICAL 态(Timer 已守护、GameLoop/dispatch 未额外加固——本规模不触发)。
- **下一执行单元**:沿 0016 重排表——P1 余项(免盲投票 / 等大盲 / `JoinRoom`+`StateSnapshot` / `RoomChat` / `Set*Blind`),每项补 wire 协议切片 + 重 codegen;或硬化(P4 delayDB 双缓冲 + PersistWriter,替换本桩)。**P5 国密信道最后做**,替换本明文层。
