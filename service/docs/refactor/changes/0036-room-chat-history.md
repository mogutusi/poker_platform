# 0036 · 房聊历史:shell 内存环形缓冲 + FetchRoomChat 拉取

日期:2026-06-24 · 范围:`app/shell/history.py`(新:`RoomChatBuffer`)、`app/shell/dispatch.py`(广播 `ChatMessage` 入缓冲)、`app/wire/server.py`(+`RoomChatHistory`)、`app/wire/client.py`(+`FetchRoomChat` + to_command 特例)、`app/shell/receiver.py`(`FetchRoomChat` shell 直服务)、`app/shell/lifespan.py`/`tests/shell/_fakes.py`(装配 buffer)、`app/gameconfig.py`(`ROOM_CHAT_HISTORY_SIZE`)、重生成 `wire.gen.ts`、测、文档。落地 [messaging.md](../../messaging.md) §持久化「房聊内存环形缓冲 + 新进房看历史」。

## 背景 / 为什么

[messaging.md](../../messaging.md):48-55 定:房聊**只在内存留最近 N 条**(每房环形缓冲,不落库、不进 world——保 `RoomChat` reduce 只读),(重)进房客户端发 `FetchRoomChat` 拉历史。当前未落地:新进房/刷新/重连看不到此前房聊。

## 关键设计决策(批判性,与 messaging.md 对齐 + 一处修订)

1. **缓冲在 shell,不在 world**(messaging.md 决策):`RoomChatBuffer` 持 `dict[room, deque(maxlen=ROOM_CHAT_HISTORY_SIZE)]`,只在内存。`_room_chat` reduce 维持只读(只产 `Broadcast(ChatMessage)`,不改状态);把 `chat_log` 塞 `world.rooms` 会让 RoomChat 变写命令 + 非游戏态进 core 域模型——否决。
2. **写入在 dispatch**:派发 `Broadcast(room, msg)` 且 `msg` 是 `ChatMessage` 时 `buffer.append(room, msg)`(一处 isinstance;RoomChat reduce 只产这一种 chat 广播)。次序由 GameLoop 串行保证;房已销毁(`rooms.get` 为 None)早退、不入。
3. **【修订 messaging.md】`FetchRoomChat` 走 shell 直服务,但 `room` 进报文**。messaging.md:53 原说「shell 直接处理、不进 GameLoop(同私聊)」——但私聊只需 `conns.get(nick)`(shell 表),而房聊历史需**目标房**,房在 `world.users[nick].room`(world 态)。**shell 协程不得读 world(不变量 2:world 仅 GameLoop 经 commit 改/读,他协程读会与 commit 竞争)**。故 `FetchRoomChat{room}` **带房名**(同 `JoinRoom` 带 room),shell 据报文房名直接读 `RoomChatBuffer` 回该连接 `outbound`——**不读 world、不进 GameLoop**,messaging.md「shell 直服务」的意图保住。回的是**直接 enqueue `RoomChatHistory`**(非 `Personal` 事件;同私聊 `DMDelivered`/Receiver 错误回执的直发路径——修订 messaging.md:53「Personal」措辞)。
4. **缓冲 shell 跨协程共享安全**:dispatch(GameLoop 协程)`append`、Receiver(自协程)`recent` 读——单线程 asyncio 下两者都是**无 await 的同步操作**,互不中途交错(同 [timer.md](../../timer.md):dispatch 写 / Timer 读 `_action` 表的既有安全模式)。`recent` 返回 tuple 快照(不可变,安全发送)。
5. **v1 简化(文档化)**:① **不校验成员资格**——跨房拉历史(在 A 拉 B 历史)可接受:房聊是**公开非敏感**态(privacy 红线只护 hole_cards/deck)、≤20 内网、拉者本可进 B 看;严格成员校验需走 reduce 解 world(本批不做,留备选)。② **不清理缓冲**——v1 房静态预置([lobby.md](../../lobby.md)),不销毁 → 无需清理;动态建房(future)再由销毁处删。③ **FetchRoomChat 不限速**——拉取幂等 + 廉价(拷 deque),洪泛由响应灌满 outbound 触发背压丢连兜(0031)。
6. **`FetchRoomChat` 不进 reduce**:它不映射 core Command(shell 路由);`to_command` 加 `case FetchRoomChat(): raise`(同 `JoinRoom` 特例),供穷尽 + 协议直测。

## 打算改什么(开工前)

- `app/gameconfig.py`:`ROOM_CHAT_HISTORY_SIZE = 50`。
- `app/shell/history.py`(新):`RoomChatBuffer.append(room, msg)` / `recent(room) -> tuple[ChatMessage,...]`。
- `app/wire/server.py`:`RoomChatHistory{type, room, messages: tuple[ChatMessage,...]}` + 注册。
- `app/wire/client.py`:`FetchRoomChat{type, room}` + 注册 + 联合 + `to_command` raise 特例。
- `app/shell/dispatch.py`:Dispatcher 持 `history`;`Broadcast` 且 `ChatMessage` → `append`。
- `app/shell/receiver.py`:`_frame_to_command` 拦 `FetchRoomChat` → `_serve_room_chat_history`(直 enqueue,不进 inbox);`history` 穿 `run_receiver`/`_frame_to_command`。
- `app/shell/lifespan.py` + `tests/shell/_fakes.py`:建 `RoomChatBuffer` 传 Dispatcher + run_receiver。
- 重生成 `wire.gen.ts`。
- 测:buffer 单元(环形/每房/快照)、dispatch 房聊广播入缓冲、receiver 拉历史回 `RoomChatHistory`、wire 协议样本。
- 文档:`messaging.md`(修订决策 3/5 + 标落地)、`config.md`/`gameconfig`、`wire-protocol-guide`、`TODO`。

## 实际改了什么

- **`app/shell/history.py`(新)**:`RoomChatBuffer`——`append(room, msg)`(首条惰性建 `deque(maxlen=ROOM_CHAT_HISTORY_SIZE)`)/ `recent(room) -> tuple[ChatMessage,...]`(tuple 快照)。
- **`app/shell/dispatch.py`**:`Dispatcher` 加 `history` 形参(positional,inbox 后);`Broadcast` 臂成员循环后,`isinstance(m, ChatMessage)` 则 `history.append(r, m)`。import `ChatMessage`/`RoomChatBuffer`。
- **`app/wire/server.py`**:`RoomChatHistory{type, room, messages: tuple[ChatMessage,...]}` + 注册 SERVER_MESSAGES。
- **`app/wire/client.py`**:`FetchRoomChat{type, room}` + 注册 CLIENT_MESSAGES + 联合;`to_command` 加 `case FetchRoomChat(): raise`(shell 路由,不进 reduce)。
- **`app/shell/receiver.py`**:`run_receiver` + `_frame_to_command` 加 `history` 形参;`_frame_to_command` 拦 `FetchRoomChat` → `_serve_room_chat_history`(`conn.outbound.put_nowait(RoomChatHistory(room, history.recent(room)))`,return None 不进 inbox)。import `RoomChatHistory`/`RoomChatBuffer`。
- **`app/shell/lifespan.py`**:`DevShell` 建 `self.history = RoomChatBuffer()`;Dispatcher + ws 端点 `run_receiver` 透传。
- **`app/gameconfig.py`**:`ROOM_CHAT_HISTORY_SIZE = 50`(dev 常量 + 注释)。
- **`frontend/src/types/wire.gen.ts`**:重生成(+`RoomChatHistory`/`FetchRoomChat`)。
- **测**:`tests/shell/test_history.py`(新 4:空/每房有序/环形淘汰/快照不可变);`test_dispatch.py`(+房聊广播入缓冲、非房聊不入);`test_receiver.py`(+端到端 fetch 回历史、未知房回空);`test_protocol.py`(+`FetchRoomChat` 样本 + `to_command` raise 臂、`RoomChatHistory` 入隐私无牌样本);装配:`_fakes.Shell`/`test_dev_db_e2e`(Dispatcher/run_receiver 透传 history)。
- **文档**:`messaging.md`(§持久化 房聊段标落地 + 修订决策 3「room 进报文 + 直 enqueue 非 Personal」+ 决策 5「v1 不校验成员 / 静态房无需清理」+ 跨协程共享安全)、`wire-protocol-guide`(`fetch_room_chat`/`room_chat_history`)、`TODO`。

318 全绿;codegen `--check` 干净;core 无越层 import。

## 自 review

方法:对照 [review.md](../../review.md) 跑对抗式 4 维 review **子代理工作流**(并发+不变量2 / 设计正确性 / 分层-reduce 只读 / 测试+文档;各维审查 → 每候选反驳)。结论 **go,0 must-fix**:14 候选全驳到 nit,三大高风险面经实查均 sound。逐维:

- **① 分层 / 不变量**:**核心红线·不变量 2**——`_serve_room_chat_history` 只读**报文房名**键的 shell 缓冲、**绝不读 world**(房无法 shell 侧解析,正是 `FetchRoomChat` 带 room 的缘由);**跨协程缓冲安全**实查确认:`append`(dispatch,GameLoop 内)/ `recent`(Receiver 协程)皆**无 await 同步 deque 操作**,单线程下不中途交错,`recent` 返 tuple 快照——与 Timer 共享表(dispatch 写 / Timer 读 `_action`)同构,非空谈。`grep core` 确认 `_room_chat` **只读未改**(只读 `users_in_room` + 产 `Broadcast(ChatMessage)`,缓冲写在 shell)。`history.py` shell-only(import wire `ChatMessage` + gameconfig,无环)。
- **② 代码↔文档**:`FetchRoomChat` 带 room + 直 enqueue(非 Personal 事件)+ 不进 GameLoop —— messaging.md §持久化 同步修订(决策 3/5 + 跨协程安全),`to_command` raise 特例注释。
- **③ 文档↔文档**:messaging.md / wire-guide(`fetch_room_chat`/`room_chat_history`)/ TODO / changes 交叉链一致;wire `RoomChatHistory.messages` 嵌 `ChatMessage` codegen 产 `messages: ChatMessage[]`(`--check` 干净)。
- **④ 数据模型**:`RoomChatBuffer` 是 shell 私有态(非 world / 非域模型);`RoomChatHistory` 只裹 `ChatMessage`(text,结构上无牌——隐私红线不受影响,已入 test_protocol 无牌样本)。
- **⑤ 规范**:`history.py`/字段带中文注释 + 「为什么」(跨协程安全、惰性建桶、快照);`ROOM_CHAT_HISTORY_SIZE` 配置化(无裸字面量);`_serve_room_chat_history` 的 `outbound.put_nowait` 不守 QueueFull —— 与既有 receiver 错误回执直发路径一致(满 = 慢客户端,外层 try 兜丢连,0031 已定),非本批新引入。
- **⑥ 测试**:buffer 单元(环形淘汰最旧 / 每房隔离 / 旧→新有序 / 不可变快照 / 空房);dispatch(房聊入缓冲、非房聊不入);端到端(fetch 回**有序**历史 + 未知房回空 + **拉非成员房**返回历史——钉死 v1 不校验成员的有意设计);protocol(`FetchRoomChat` 样本 + raise 臂 + `RoomChatHistory` 无牌样本)。319 全绿。
- **⑦ 账本**:打算↔实际一致 + 采纳 2 条 nit(端到端有序 + 非成员房拉取测);TODO 划项;提交引用 0036、全英文。

**对抗核实存活 / 采纳 / 驳回**:functional 候选**全部驳到 nit**(0 blocker/major)。*采纳的 nit(2,均已修)*:① 端到端 fetch 测改连聊两句、断言旧→新有序(原只单条);② 加「拉非自己房」测,钉死 membership-agnostic 行为(原 unknown-room 测只覆空、分不清「不校验成员」与「无历史」)。*驳回*:`recent` 的 `tuple(deque)` 「迭代中被改」——驳回(无 await、不交错;`dict.get` 不同房 append 亦不破当前房迭代);`outbound.put_nowait` 不守 QueueFull「比 dispatch 差」——驳回(与既有 receiver 错误直发一致、design 5.3 接受,非本批引入);`to_command` raise「不可达死代码」——驳回(belt-and-suspenders + 协议直测覆盖,同 JoinRoom 既有约定)。

> 批判性自评:本批关键判断是**修订 messaging.md「FetchRoomChat 同私聊走 shell」**——私聊只需连接表,而房聊历史需目标房(world 态),shell 不得读 world(不变量 2)。解法是「room 进报文(同 JoinRoom)+ shell 直读缓冲」,既守不变量 2 又保「不进 GameLoop」意图,代价是不校验成员(公开非敏感房聊可接受,已文档化 + 测固化)。review 实跑确认缓冲跨协程安全锚在「无 await 同步操作 + tuple 快照」、非「靠运气」。

## 待办 / 下一步

- 私聊 DM 未读收件箱(messaging.md §私信)。
- 动态建房后的缓冲清理 + 严格成员校验(如需)。
- `ROOM_CHAT_HISTORY_SIZE`(P8)随 `gameconfig` env 化。
