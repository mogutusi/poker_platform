# 0031 · shell 硬化:顶替再连 StateSnapshot + dispatch inbox 满守护

日期:2026-06-24 · 范围:`app/core/reduce.py`(`_connect` 加「在房在线 → 顶替再连」臂)、`app/shell/dispatch.py`(`_drop_connection` 的 `inbox.put_nowait` 加 `QueueFull→CRITICAL` 守护)、`tests/core/test_join_reconnect.py`(拆「在线幂等 no-op」测 → 两条顶替再连快照测)、`tests/shell/_fakes.py`(`Shell` 加 `inbox_maxsize`)、`tests/shell/test_dispatch.py`(+ bounded-inbox 丢连不崩回归)、`tests/shell/test_receiver.py`(顶替端到端补「新连接收快照」断言 + 既有断言改按帧类型取)、文档(`connection.md`/`core.md`/`TODO.md`)。讨「硬化 / 子系统」的 **shell 硬化:背压 + 顶替/重连 `StateSnapshot` + tests/shell** 项。

## 背景 / 为什么

[TODO.md](../TODO.md) 「硬化 / 子系统」列 `shell 硬化:背压(inbox/outbound 上限 + 队列满丢连)、顶替/重连 StateSnapshot、tests/shell/`。开工前对照代码与文档审计(含一轮对抗式 gap-audit 子代理工作流),结论:**背压主体早已落地**(0018 有界 `inbox`/`outbound` + 0024/0025 delayDB;`dispatch._enqueue` outbound 满 → `_drop_connection`;`timer._fire` / `receiver` finally 的 `inbox.put_nowait` 均 `QueueFull→CRITICAL`),只剩**两处真实缺口**:

1. **`dispatch._drop_connection` 的 `inbox.put_nowait(Disconnect)` 裸调用**(`dispatch.py:89`):它在 **GameLoop 内同步执行**(commit 后 `for ev: dispatch(ev)` → `_enqueue` outbound 满 → `_drop_connection`)。inbox 满时 `QueueFull` 会**冒出 dispatch 崩掉唯一状态写者 GameLoop**——直接违反 [architecture.md](../../architecture.md):48-50「inbox 满 = 落 CRITICAL 的进程级故障,不是要去优雅处理的常态、更不该崩」。这是 **5 处 inbox/outbound put 站点里唯一一处在单写者协程内不守 QueueFull 的**(Timer/Receiver 都守)。且**自放大**:本批要硬化的「outbound 满 → 丢连」正是触发它的路径。

2. **顶替再连不补 `StateSnapshot`**:[connection.md](../../connection.md) §会话过期与密钥轮换明确「新连接走**顶替**接管该 nick → reduce 私发 `StateSnapshot` 对齐其当前房」;但 `_connect`(0022)对**在房在线**用户的 `Connect` 是 no-op(`reduce.py:551`「已在线 → 不重发快照」)。顶替时旧连接被静默关闭、**未投 `Disconnect`**(顶替语义),故 `world` 仍记其在线 → 落进 no-op 臂 → **新 ws 拿不到任何桌面状态**(密钥轮换本应「用户无感」,实际整桌空白)。这同时是 **connection.md 自身的矛盾**::129(step 4)说「在线 → 幂等 no-op、不重发快照」与 :152(会话轮换)说「顶替 → 私发 StateSnapshot」直接打架。

> **0022 决策 6 的「在线 no-op」当时是为保 0018/0029 预置 WATCHING dev 用户的 Connect 仍 no-op**(不破 dev 冒烟)。但 [0030](0030-p4-per-join-wire-load.md) 已退役该预置(`build_dev_world()` 建空房、用户经 `join_room` 入房),其前提消失——如今**在房在线的第二次 `Connect` 只可能来自顶替**(新 ws)。故本批把该决策**就顶替反转**(回指 0022 决策 6,不改 0022 历史记录)。

## 关键设计决策(批判性)

1. **`_connect` 改为「在房在线 → 只私发 `Personal(StateSnapshot)`,不改状态、不广播」**。理由链:
   - **reduce 不感知「连接」**,无法分辨「顶替再连」与「同一连接重复 `Connect`」。但 Receiver **每条连接只投一次 `Connect`**(`receiver.py:40`),对**已在房在线** nick 的第二次 `Connect` 必来自新 ws(= 顶替)。
   - 正确性**不依赖「证明这是顶替」**,而依赖**快照本身无害可重发**:`_state_snapshot` 隐私逐收件人(`your_hole_cards` 只取 `for_nick`、`PlayerView` 结构上无 `hole_cards`,0022 已值级测过),只读、幂等。即便极端下对同一在线连接重发一次快照,也无副作用。这条框定避免未来读者误以为 reduce「知道」顶替。
   - **不广播**:用户状态未变(顶替对房内他人无信息变化),`Broadcast(UserStatusChanged)` 只属 OFFLINE→恢复臂。顶替「用户无感」。

2. **OFFLINE 重连臂的快照仍在「恢复状态之后」构造,不与顶替臂共用提前构造**。`_state_snapshot` 的 `SeatView.status` 读 `room.users_in_room`(`reduce.py:604`);若把快照提到 `room.users_in_room[nick] = restored` 之前,OFFLINE 重连者自己的座位会显示成 `OFFLINE` 而非恢复后的 `PLAYING/SITTING_IN`。故两臂各自构造:顶替臂状态已对、直接投影;重连臂**先恢复后投影**。

3. **`dispatch._drop_connection` 的 `inbox.put_nowait` 加 `try/except QueueFull→log.critical`**,与 `timer._fire`(`timer.py:69-72`)、`receiver` finally(`receiver.py:55-58`)同构。**不**额外包住 `GameLoop.handle` 的 dispatch 循环:守住这一处后 dispatch 全程不再抛(`persist.put`/Timer 方法是同步 dict 写;outbound put 由 `_enqueue` 守、`_drop_connection` 现已守),包循环只会**吞掉 commit 后的真实 bug**、掩盖问题。丢失这一条 `Disconnect` 是 inbox 满(已 CRITICAL)窗口内的可接受降级:该 nick 已 `unregister` 停路由,**但因没标成 OFFLINE,`Cleanup` 不会自动退座**(`_cleanup` 只回收 OFFLINE 座位)——座位占用至该 nick 重连(走顶替再连补回)或进程重启。shell 不写 `world`(不变量 2),无法在此层标 OFFLINE,故无法兜;这是已知的有界占座泄漏(对抗 review 抓到「Cleanup 兜」是不成立的承诺,已据实改正,见自 review)。

4. **测试桩 `Shell` 加 `inbox_maxsize`(默认 0=无界)**。原 `_fakes.Shell` 用无界 `asyncio.Queue()`,既有「慢客户端丢连」测**永远触发不了 inbox 满**,故抓不到决策 3 的 bug。加可选有界参数(默认不改既有测试行为),新回归测用 `inbox_maxsize=1` 灌满 inbox 后丢连,断言 `_drop_connection` 落 CRITICAL **且不抛**。已临时撤守护复验:裸 put 版本该测以 `asyncio.QueueFull` 失败(确为真回归)。

5. **顶替端到端只在 shell 测断言「新连接收到 `state_snapshot` 帧」**(报文级),底牌值级隐私归 core 测(0022 已穷举)。[testing.md](../../testing.md):shell 测只验接线与保序,不重测 core。

## 打算改什么(开工前)

- `app/core/reduce.py`:`_connect` 在「纯大厅 no-op」与「OFFLINE 重连」之间插入「在房在线 → 顶替再连」臂(只 `Personal(StateSnapshot)`)。
- `app/shell/dispatch.py`:`_drop_connection` 的 `inbox.put_nowait` 包 `QueueFull→CRITICAL`。
- `tests/core/test_join_reconnect.py`:`test_connect_online_user_is_noop` → 拆成「在房在线顶替再连快照(两手之间)」+「局中顶替带自有底牌、对手不泄」两测。
- `tests/shell/_fakes.py`:`Shell` 加 `inbox_maxsize`。
- `tests/shell/test_dispatch.py`:+ bounded-inbox 丢连不崩落 CRITICAL 回归。
- `tests/shell/test_receiver.py`:顶替测补「新连接收快照」断言。
- 文档:`connection.md`(解 129↔152)、`core.md`(Command 表 + 事件一览)、`TODO.md`(勾项 + 状态行)。

## 实际改了什么

- **`app/core/reduce.py` `_connect`**:三类显式分臂——① 纯大厅 no-op;② 在房在线 → `[Personal(nick, _state_snapshot(...))]`(不改状态/不广播);③ 在房 OFFLINE → 先 `_reconnect_status` 恢复、再投影、`Broadcast(UserStatusChanged)`+`Personal(StateSnapshot)`。注释写明「reduce 不感知连接、安全靠快照可重发」。
- **`app/shell/dispatch.py` `_drop_connection`**:`inbox.put_nowait(Disconnect)` 包 `try/except asyncio.QueueFull → log.critical(...)`;注释点明本调用在 GameLoop 内,并据实写明残留——丢了 `Disconnect` 该 nick 仍记在线,`Cleanup` 只回收 OFFLINE 座位故不会自动退座,座位占用至重连或重启(inbox 满 CRITICAL 窗口内可接受的有界占座泄漏)。
- **`tests/core/test_join_reconnect.py`**:删 `test_connect_online_user_is_noop`;加 `test_connect_online_in_room_resends_snapshot`(恰 1 个 `Personal(StateSnapshot)`、无 `Broadcast`、无 `Persist`、状态不变)与 `test_connect_in_hand_takeover_carries_own_cards_not_others`(局中 PLAYING 顶替:`your_hole_cards` 为自己、值级断言对手 Qh/Jc 不出现、不广播、状态留 PLAYING);更新模块 docstring;保留 `test_connect_lobby_user_is_noop`。
- **`tests/shell/_fakes.py`**:`Shell.__init__(world, *, inbox_maxsize=0)` → `asyncio.Queue(maxsize=inbox_maxsize)`。
- **`tests/shell/test_dispatch.py`**:+ `test_drop_connection_inbox_full_logs_critical_not_crash`(`inbox_maxsize=1` 灌满 + outbound 灌满 → dispatch 一条 → 断言落 CRITICAL、不抛、连接已 unregister)。
- **`tests/shell/test_receiver.py`**:import `StateSnapshot` + `_non_snapshot` 助手;`_run_one_frame` 改等「首个非 `state_snapshot` 帧」(预置在房在线 alice 的初始 `Connect` 现会先回一帧顶替快照);`test_valid_frame...`/`test_invalid_frame...` 改用 `_non_snapshot(...)` 取响应帧;`test_async_displacement...` 补断言「新连接 c2 收到 `state_snapshot` 帧」+ sit_down 断言改等 `user_status_changed` 帧。
- **文档**:`connection.md` step 4 重写为三分类并显式与 §会话轮换 :152 对齐;`core.md` Command 表 `Connect` 行 + 事件一览拆「重连(OFFLINE)」「顶替再连(在线)」两行;`TODO.md` 勾 `shell 硬化` + `JoinRoom/Connect/StateSnapshot`、回填 reduce/tests 状态行。

**偏离计划**:范围与「打算」一致,额外发现并修一处**测试连带影响**——`test_receiver._world()` 预置 alice 在房 WATCHING,其初始 `Connect` 在新行为下会先回一帧顶替快照,打乱了三处「按 `sent[0]` 取帧」的断言;改为按帧类型(`_non_snapshot` / 等 `user_status_changed`)取,既修正又顺带验证了顶替快照在真实管线里确实送达。

## 自 review

方法:对照 [review.md](../../review.md) 跑对抗式 7 维 review **子代理工作流**(7 维各 1 审查者 → 每候选 1 反驳者;开工前另跑一轮 gap-audit 工作流,5 lens 反驳式核实「背压唯一缺口」「在房在线 Connect ⟺ 顶替」「快照只读可重发」「文档同步面」「TODO 完整性」,`takeover_claim_holds=true`)。复审结论 **go-after-must-fix:14 候选 / 7 存活,最高风险面(GameLoop 崩溃安全 + 顶替快照隐私)两面均判 sound,1 个 must-fix 为纯文档**——见下「存活并修 (a0)」。最高风险面 = **并发/不变量(GameLoop 不崩)** + **隐私(顶替快照不泄他人底牌)**,实跑核实。逐维:

- **① 分层 / 不变量**:`grep app/core` 无 `fastapi|sqlalchemy|app.shell|app.db` import(复验干净)。`_connect` 三臂纯同步、helper 不 raise、失败前无半改;顶替臂**零状态改动**(只产 `Personal`)、OFFLINE 臂先改后投影。`dispatch._drop_connection` 守护后 **dispatch 全程不抛** → GameLoop 单写者不会因 inbox 满崩(architecture.md:48-50 兑现);**撤守护复验**:回归测以 `QueueFull` 失败,证明守护是真护栏。`Personal(StateSnapshot)` 是快照值,不持 `world` 活引用(不变量 7)。
- **② 代码↔文档**:`_connect` 顶替臂偏离 0022 的「在线 no-op」→ 同批改 `connection.md`(:129 三分类、显式对齐 :152)、`core.md`(Command 表 + 事件一览)。`dispatch` 守护对应 architecture.md:48-50,无新签名偏离。
- **③ 文档↔文档一致**:解决 connection.md **:129↔:152 既存矛盾**(本批最关键的文档缺陷);core.md 事件一览与 reduce 实际产出(OFFLINE→广播+快照 / 在线→只快照)逐臂对齐;TODO 勾项 + 计数(共 278)同步;回指 changes/0022 决策 6(不改历史)。
- **④ 数据模型**:未改任何 dataclass / wire DTO;复用既有 `StateSnapshot`/`Personal`。`Shell.inbox_maxsize` 默认 0(无界)不改既有测试语义。
- **⑤ 规范**:新增/改动注释讲「为什么」(reduce 不感知连接、快照可重发、inbox 满在 GameLoop 内不能崩、OFFLINE 臂先恢复后投影的顺序原因);无魔法数(`inbox_maxsize` 是参数、CRITICAL 走 logging);无死代码;中文注释。
- **⑥ 测试**:核心红线**隐私**——局中顶替测用**值级断言**(对手 Qh/Jc 不在序列化产物、只自己+公共牌),非 `hasattr` 恒真式;**并发**——bounded-inbox 回归测**撤守护即失败**(真回归);顶替**端到端**(receiver 测断言新连接真收到 `state_snapshot` 帧);守恒/无 Persist 默认开。278 全绿。
- **⑦ 流程账本**:打算↔实际差异(test_receiver 连带帧序修正)已记;TODO 勾 2 项 + 回填 reduce/tests/JoinRoom 行;提交引用 `0031`、全英文。

**对抗核实存活 / 驳回**:
- *存活并修*:**(a0)【7 维 review 抓到的唯一 must-fix,纯文档】**初稿 `dispatch._drop_connection` 注释 + 本记录决策 3 称「丢的 `Disconnect` 由 `LIVENESS` 的 `Cleanup` 兜」——**不成立**:丢的就是 `Disconnect`,该 nick 永不被标 OFFLINE,而 `_cleanup` 只对 OFFLINE 退座(`reduce.py`),`receiver` finally 的另一处 `Disconnect` 又被 `is_current=False`(已 `unregister`)挡掉 → 无第二次 `Disconnect`,座位实际占用至该 nick 重连或重启。**承诺一张不会触发的安全网会误导运维**,已据实改正注释 + 决策 3 + §实际(行为本身可接受:仅发生在 inbox 满的 CRITICAL 窗口、单 nick、筹码仍守恒;且 shell 不写 world,无法在此层标 OFFLINE)。(a) OFFLINE 臂快照构造顺序——初稿差点把快照提到状态恢复前(会让重连者座位显示 OFFLINE),核实 `_state_snapshot` 读 `users_in_room` 后保持「先恢复后投影」;(b) test_receiver 预置在房在线 alice 的初始 Connect 连带回快照,三处 `sent[0]` 断言会错位,改按帧类型取。
- *驳回*:(c)「是否该包 `GameLoop.handle` dispatch 循环」——驳回:守住 `_drop_connection` 后 dispatch 不再抛,包循环只会吞掉 commit 后真实 bug(决策 3);(d)「receiver 错误路径 `outbound.put_nowait`(:69/86/90)未守」——驳回:跑在 Receiver 协程、外层 `try/except` 兜,最坏丢该连接(慢客户端本应丢),不崩 GameLoop/Timer,出本批 GameLoop 崩溃硬化范围(归后续错误路径健壮性);(e)「reduce 顶替臂该否广播/改状态」——驳回:状态未变,广播是 OFFLINE 恢复臂专属,顶替用户无感。

> 批判性自评:本批最隐蔽处是「reduce 无法观测顶替」——若把改动叙述成「检测顶替」会误导后人(reduce 看到的只是『在房在线 + Connect』)。正解是把正确性锚在「快照只读、隐私逐收件人、幂等可重发」,而非「证明这是顶替」;注释与本记录均按此框定。次隐蔽处是测试桩无界 inbox **掩盖**了 GameLoop 崩溃路径——绿测之所以一直没抓到,正因桩从不让 inbox 满,印证 review.md「绿测覆盖想到的、review 覆盖没想到的」。

## 待办 / 下一步

- **lifespan drain 硬化**(关闭反序 drain 超 `DB_DRAIN_TIMEOUT_MS` 落 CRITICAL)归 **P8 收尾**(architecture.md 已定 drain 属 P8)。
- receiver 错误路径 `outbound.put_nowait` 健壮性(满时丢连前回执投递)可后续单列。
- 日志(GameLoop 边界审计 + 脱敏)、配置收编(`INBOX_MAX`/`OUTBOUND_MAX` 等进 `poker.env`)按 TODO 继续。
