# 0047 · 文档/注释对齐真相(doc↔code truth-up:Dispatcher 抽取 + 去房主注释 + 陈旧占位语)

日期:2026-07-01 · 范围:纯文档/注释,**零行为改动、零测试改动逻辑**。同步一批已落地但文档/注释仍停在旧态的漂移——以 [connection.md](../../connection.md)/[error.md](../../error.md) 的 **Dispatcher 抽取(material)** 为首,兼收 0044 去房主后残留的「0 号位」注释、reduce 全命令落地后残留的「未实现占位」注释,以及三处小漂移(core.md 事件命名、config.md 类型、wire-protocol-guide §9 端点状态)。

## 背景 / 为什么(批判性:先核实「这是不是真漂移」)

开工前用「6 组并行只读审计(docs+code)」通读全部 service/docs + 对应代码,产出漂移候选;**逐条回到代码对抗核实**(默认先反驳),留下真漂移、剔除假阳性(如 messaging.py 直接 `conns.get` 而非 `Presence.is_online` —— 与 messaging.md:54「本文只用在不在线」一致,非漂移,不改)。核实后确认的真漂移分三档:

- **material(架构级误导)**:`Dispatcher` 类抽取(GameLoop commit 后把事件派发/错误回发交给独立 `Dispatcher`,GameLoop 只持 `world/inbox/dispatcher`)在 connection.md/error.md **完全没体现**——两篇仍把 `dispatch`/`send_error` 画成 GameLoop 方法,并给出错误签名 `GameLoop(world, inbox, conns, persist, timer)`(实际 `GameLoop(world, inbox, dispatcher)`,conns/persist/timer/inbox/history 归 `Dispatcher`)。读者据此理解会误判核心 shell 装配。
- **minor(注释与其下方代码矛盾 / 授权模型过时)**:0044 去房主后,`SetSmallBlind`/`SetBuyIn`/`RoomConfigChanged` 及 receiver 守卫/测试 docstring 仍写「0 号位(占座者)配置」——授权实为「任何在房成员」(reduce.py:905 明写「无房主」);reduce 全 16 命令落地后,模块头 + `case _` + `_set_user_status` 仍写「未实现/占位」,其中 `_set_user_status` 注释说「起身离座→WATCHING 暂以 INTERNAL 占位」而其正下方 973-977 行**正是**起身离座的完整实现(0015 落地),注释与代码直接打架。
- **cosmetic/minor 小漂移**:core.md 三处把产出事件写成 `Broadcast(PlayerAction)`(命令名),wire ServerMessage 实为 `PlayerActed`(`type="player_acted"`);config.md 示意 `ACTION_TIMEOUT/LIVENESS_TIMEOUT: int` 实为 `float`(与 timer.py `float` 用法一致);wire-protocol-guide §9 把已落地(0018)的明文 dev 端点写成「即将/正在做」且路径 `/dev?nick=` 实为 `/dev/ws?nick=`。

**为什么值得单独做一篇**:README §0/§5 + review.md 维②③ 反复强调「文档↔代码↔计划三者始终对齐」,doc-sync 是本仓一等公民工作单元(先例 0001/0009/0019)。material 那条会误导所有后续读 connection.md 理解 shell 的人;其余是「注释描述的治理/状态已不存在」——留着 = 下一个人按错误前提写代码。用户本轮也明确要求「sync the docs」。

## 关键设计决策

1. **connection.md 认领 Dispatcher 结构的权威位置**:§dispatch 伪码从「GameLoop 方法」改写为独立 `Dispatcher` 类(持 world/conns/persist/timer/inbox/history),补上真实代码里但伪码漏掉的两件事——`Persist(HandRecordWrite)` 派发时盖 `end_time` 墙钟、`ChatMessage` 广播入 `RoomChatBuffer`;§组件全景表 GameLoop 持有列改 `world/inbox/dispatcher` 并加 `Dispatcher` 行;§lifespan 启动步 7 签名改 `GameLoop(world, inbox, dispatcher)`。error.md 不重写伪码(其「错误=返回值/按 origin 回发」的教学正确),只加一行说明 `send_error`/`dispatch` 属 `Dispatcher`(GameLoop 委托),并把 system-cmd 失败的 `log.error` 对齐实现的 `log.warning`。
2. **只改注释/文档,不碰行为**:`case _` 的 `Err(INTERNAL, "reduce 暂未实现命令 X")` 消息**保留**——对一个「未来新增但漏写臂」的命令它仍准确;陈旧的是其**上方注释**把 case _ 说成「迁移期临时占位」。`_set_user_status` 969 行的 INTERNAL 守卫也**保留**(防御非自助目标),只修 952-953 与代码矛盾的引导注释。core 纯度/不变量不涉。
3. **「0 号位」→「任何在房成员(无房主)」全站扫**:审计只点了 wire 两处,对抗核实又揪出 receiver.py:129 与 test_room_config_guard.py:3 两处同源陈旧,一并改(commands.py:45/50 同改)。test_room_config.py 里的「0 号位」是**座位下标**语义(测试布置 A 占座0)、且明说「无房主/去 seat-0 依赖」,**正确不改**。
4. **scope 克制**:connection.md「shell 非 reduce 消息路径(DM 路由 / 房聊环缓冲 / 登录补收)缺文档」是**新增章节**级别的工作(messaging.md 已载实质),本篇只把 §待定 里「messaging 未设计 / wire 清单未写」这两个**已过时的 bullet** 改成「已落地,见 messaging.md / .py」,不新写章节——留待办。

## 打算改什么(开工前)

- `docs/connection.md`:§组件全景表(GameLoop 持有列 + 加 Dispatcher 行)、§dispatch 伪码(GameLoop→Dispatcher 类 + 补 end_time/history)、§三个关键结构(Connection 加 dev↔P5 delta 注:今日无 `channel`、改挂 `chat_bucket`/`dm_bucket`)、§lifespan 步 7 签名、§待定(messaging 已落地 + wire 清单已写)。
- `docs/error.md`:§怎么处理 加 Dispatcher 归属说明 + `log.error`→`log.warning` 对齐。
- `docs/core.md`:110/160/161 `Broadcast(PlayerAction)`→`Broadcast(PlayerActed)`;134 产出顺序补驱逐事件括注。
- `docs/config.md`:21-22 `int`→`float`(ACTION/LIVENESS_TIMEOUT)。
- `docs/wire-protocol-guide.md`:§9 端点状态「即将」→「已落地(0018)」+ 路径 `/dev/ws?nick=`。
- 注释:`app/wire/server.py:136`、`app/wire/client.py:43,48`、`app/core/commands.py:45,50`、`app/shell/receiver.py:129`、`tests/shell/test_room_config_guard.py:3`(去「0 号位」);`app/core/reduce.py:1-4,105-108,951-953`(去「未实现占位」框架)。
- `docs/refactor/TODO.md`:持续项「文档漂移→改文档」勾一次账;必要时补注本篇。

## 实际改了什么

**docs(6 篇):**
- `connection.md`:① §组件全景表——GameLoop 持有列 `world/inbox/conns/persist/timer`→`world/inbox/dispatcher`,**新增 `Dispatcher` 行**(持 `world`(只读)/conns/persist/timer/inbox/history);② §三个关键结构——Connection 代码块后加 **dev↔P5 delta 引注**(今日 dev `Connection` 无 `channel`、明文直发、改挂 `chat_bucket`/`dm_bucket`,P5 补 `channel`);③ §dispatch——伪码从「GameLoop 方法」改写为 **`Dispatcher` 类**,并补真实代码里但旧伪码漏掉的两件事(`Persist(HandRecordWrite)` 派发盖 `end_time` 墙钟、`ChatMessage` 广播入 `history`)+ `_drop_connection` 的 inbox 满 CRITICAL 守护 + `send_error` origin=None 落 `log.warning`;④ §lifespan 步 7 签名——`GameLoop(world, inbox, conns, persist, timer)`→`Dispatcher(world, conns, persist, timer, inbox, history)` + `GameLoop(world, inbox, dispatcher)`(**逐字对齐 lifespan.py:88-89**);⑤ §待定——messaging bullet 改「已落地(0021/0033/0036/0038-0041)」、wire 清单「(未写)」→「已写(client.py/server.py+codegen)」。
- `error.md`:§怎么处理——伪码里 `self.send_error`/`self.dispatch`→`self.dispatcher.*`,加「send_error 在 Dispatcher 上(持 conns);GameLoop 委托」注 + `log.error`→`log.warning` + `ErrorMessage(code=,detail=)`→`ErrorMessage.from_err(err)`(对齐 dispatch.py:74-81)。
- `core.md`:110/160/161 `Broadcast(PlayerAction)`→`Broadcast(PlayerActed)`(3 处,`PlayerAction` 是入站命令、`PlayerActed` 是出站 wire);134 产出顺序补「(若有 room.leaving 离桌者:逐人 Persist(PointsWrite)+UserLeft 驱逐)」括注。
- `config.md`:21-22 `ACTION_TIMEOUT/LIVENESS_TIMEOUT: int`→`float`(对齐 gameconfig.py:33-34 + timer.py `timeout_s/fire_at: float`)。
- `wire-protocol-guide.md`:§9 标题「(Phase D · 即将)」→「(Phase D · 已落地)」+ 正文「端点正在做(下一步)」→「已落地(0018)」+ 路径 `/dev?nick=`→`/dev/ws?nick=` + 补 uvicorn 起服务命令 + 握手后进大厅/join_room 说明。
- `TODO.md`:L45 wire-guide dev 握手段「随 D 阶段补齐」→「已补齐(§9,0018/0047),路径 `/dev/ws?nick=`」;持续项「文档漂移改文档」补记本篇为最近一次全量 truth-up。

**代码注释/消息(7 文件,零行为改动):**
- 去陈旧「0 号位(占座者)」授权注释 → 「任何在房成员(无房主)」:`app/wire/server.py:136`(RoomConfigChanged)、`app/wire/client.py:43,48`、`app/core/commands.py:45,50`、`app/shell/receiver.py:129`、`tests/shell/test_room_config_guard.py:3`(模块 docstring)。**对抗核实多揪出 receiver.py + 测试 docstring 两处审计未点的同源陈旧**。
- `app/core/reduce.py`:模块头(1-4)+ `case _`(105-108)去「未实现/占位/逐个落地」框架 → 「全命令有臂;case _ 防御性兜底(未知命令→Err INTERNAL,不 raise),现不可达」;`_set_user_status` 注释(951-953)去「起身→WATCHING 暂以 INTERNAL 占位」(其正下方 973-977 正是起身实现)→ 准确描述「就座内切换 + 起身退筹(0015);入座走 SitDown、买入走 BuyIn」;并把 969 行 `Err(INTERNAL,"…暂未实现(入座走 SitDown)")` 消息改为「非自助可请求目标(PLAYING/OFFLINE 系统驱动;入座走 SitDown、买入走 BuyIn)」——**否则与更新后的注释自相矛盾**(自 review 抓到,见下)。

**未改**:任何控制流 / 签名 / 类型 / 测试断言;`case _` / 969 行的 `Err` **code 仍 INTERNAL**(只改 message 文本);历史变更记录(0023/0043/0044)里的「0 号位」是**当时事实的账本**,不改。

432 全绿(前后一致,纯注释/文档 + 消息文本);`gen_wire_ts.py --check` OK(codegen 不吐注释,wire.gen.ts 无漂移)。

## 自 review

方法:开工前「6 组并行只读审计(docs+code)」出候选 → **逐条回代码对抗核实**(默认先反驳)→ 收工再跑 5 组 grep 查「改全没 / 有没有引入新漂移或死链」+ 逐字比对 material 改动 vs `dispatch.py`/`gameloop.py`/`lifespan.py`。逐维:

- **① 分层 / 不变量**:纯注释/文档 + 一处 Err message 文本,**零行为**;core 未加 import、未改控制流(432 绿佐证)。material 改动只描述既有 `Dispatcher` 抽取,未改代码。
- **② 代码↔文档同步**:material 逐字核对——`Dispatcher.__init__(world,conns,persist,timer,inbox,history)`(dispatch.py:29-46)、`GameLoop(world,inbox,dispatcher)`(gameloop.py:29)、`lifespan.py:88-89` 构造顺序、dispatch 各臂(end_time 盖戳 66-67 / ChatMessage→history 58-59 / send_error log.warning 77 / from_err 81)——**全部一致**。config.md float、core.md PlayerActed、guide 路径均已回代码验证。
- **③ 文档↔文档一致**:grep 复验——live docs 无残留 `Broadcast(PlayerAction)` / `GameLoop(world, inbox, conns` / `/dev?nick=`(仅 0047 自身与历史账本命中)。**采纳并记 1 个 scope 决策**:db.md(29/38/149/187)/timer.md(47)/architecture.md 仍用「GameLoop.dispatch」作**概念简称**——核实后确认它们**不含 connection.md 那种错误构造签名**,只是「GameLoop 驱动的、同步的、commit 后派发步」的教学简称,不与新 `Dispatcher` 命名矛盾(reader 读 connection.md 即知 = Dispatcher.dispatch)。全量改名会搅动 db.md 精心写的双缓冲 prose、低价值,**故意留作后续一致性小 pass**(见待办),不扩本篇 scope。新增链接(0033/0038/messaging.md)均已验证可解析。
- **④ 数据模型**:不涉(无类型/字段改动)。
- **⑤ 规范合规**:所有改后注释仍「注释在讲为什么」;去掉的都是**过时的“临时/占位/房主”叙事**,换成与代码一致的当前语义。无新增裸字面量。
- **⑥ 测试充分**:无新逻辑 ⇒ 无新测试;仅改 1 处测试模块 docstring(去「0 号位」)+ 1 处 reduce Err message 文本,**均无测试断言耦合**(改前 grep 证实仅 reduce.py:970 一处引用该串;test_room_config* 断言的是 code 非 detail)。432 前后不变。
- **⑦ 流程账本**:打算↔实际一致(多出 2 项:reduce.py:970 message 一致性修 + receiver/test docstring 两处同源陈旧,均自 review 补记);TODO 勾项 + 持续项回填;提交将引用 0047、全英文。

**对抗核实存活 / 采纳 / 驳回**:审计候选中**驳回 1 假阳性**(messaging.py `conns.get` vs `Presence.is_online`——与 messaging.md:54 一致,非漂移,不改);**采纳全部真漂移**(material 1 + minor/cosmetic 7 类);**自 review 新抓 2**(reduce.py:970 message 会与新注释自相矛盾 → 修;审计漏点的 receiver.py:129 + test docstring → 补改)。0 真行为 bug;兑现「绿测 ≠ 可提交」——本篇全部价值在测试根本不覆盖的维②③⑦。

## 待办 / 下一步

- **低价值一致性小 pass(可选)**:db.md/timer.md/architecture.md 的「GameLoop.dispatch」概念简称,触及那几篇时顺手注明「= Dispatcher.dispatch(GameLoop 驱动)」;本篇已论证不矛盾,故未扩 scope。
- connection.md 仍缺「shell 非 reduce 消息路径(DM 路由 / 房聊环形缓冲写读 / 登录补收)」的正式章节——实质在 messaging.md,补 connection.md 侧图另起一篇。
- 下一单元 0048:P7 `GET /lobby/rooms` 只读 REST 切片(推进 P7,首个 HTTP/REST 面)。**前置已明**:lifespan.py:90 `Presence(world, conns)` 已确立「持稳定 world 引用、读 commit 后态(原地改 .users/.rooms)、单线程 asyncio 下不 await 的读原子、可滞后」的只读消费范式,`GET /lobby/rooms` 读 `world.rooms` 头数同此;需在 0048 记录里调和 rest.md「REST 只读 DB」↔ lobby.md「房列表读 world.rooms」的契约张力(定:lobby-rooms 是唯一读 world 的 REST,与读 DB 的 leaderboard/hands/profile 分列)。
