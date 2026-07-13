# 0071 · 房聊历史挂进 Room(随房生灭)

日期:2026-07-13(设计)· 范围(计划):`app/core/domain.py`(`Room.chat_history`)、`app/core/commands.py`(`RoomCreate.chat_history_size`)、`app/core/reduce.py`(`_join_room` 建房带 maxlen / `_room_chat` 追加)、`app/shell/receiver.py`(FetchRoomChat 改读 committed world;签名去 `history` 换 `world`)、`app/shell/dispatch.py`(去 history 参数与追加分支)、`app/shell/lifespan.py`(接线)、**删除 `app/shell/history.py`**、tests、docs(messaging/connection/core/storage + frontend/BACKEND_GUIDE.md)。

> ~~状态:设计已与用户定案(本篇),实施在 0070 落地之后~~ → **已实施**(见「实际改了什么」)。

## 背景 / 为什么

架构审计 A3:房聊环形缓冲(`shell/history.py`,0036)只有 `append`/`recent`、无清理;房间销毁发生在 `commit()` 里且无任何钩子通知缓冲。后果:① 同名新房的 `fetch_room_chat` 能拉到**上一代房间陌生人的聊天**(动态房下房名自由输入,跨「房间世代」隐私泄露);② 缓冲字典按历史房名无界增长。

两案摆给用户:A(shell 钩子:GameLoop 发现房销毁 → 通知丢缓冲,~10 行,world 保持纯游戏状态)vs B(缓冲挂进 `Room`,生命周期天然随房)。**用户定案选 B**,理由:房内数据本就大多不持久化,生命周期同步是对的;私聊不同(要持久化、在 DB),不受影响。已向用户申明 B 的两条代价并被接受:① 每条命令的工作副本深拷贝会连带拷 ≤50 条 ChatMessage(本规模无感);② 纯展示数据进入权威 world(「什么该进 world」的口子,记档)。

## 设计(实施蓝图)

1. **`Room.chat_history: deque[ChatMessage]`**(`field(default_factory=deque)`;core import wire DTO 合法,见 models.md;`copy.deepcopy` 保留 deque 的 maxlen——实施时用测试钉)。直接构造 `Room(...)` 的既有测试拿到无界默认 deque,不受影响。
2. **环形上限经 `RoomCreate` 传入**(core 不 import config 铁律):`RoomCreate` 加字段 `chat_history_size: int`,shell `_build_join` 从 `gameconfig.ROOM_CHAT_HISTORY_SIZE` 盖;`_join_room` 建房时 `chat_history=deque(maxlen=cmd.create.chat_history_size)`。
3. **`_room_chat` 从「只读」变「追加」**:构造 `ChatMessage` 后 `room.chat_history.append(msg)` 再 `Broadcast`。同一对象进历史与事件——消息构造后无人改它,不违不变量 7(产出 event 后别再改其引用对象)。**0021 的「只读 + 深比较守护」测试需改写**:断言只有 `chat_history` 变、其余深等,且追加的就是广播的那条。
4. **`FetchRoomChat` 改读 committed world**(沿 presence/lobby-REST 的只读豁免:只读、展示用、容忍滞后一拍;单线程 asyncio 下 `tuple(room.chat_history)` 快照不撕裂):`run_receiver` 签名 `history: RoomChatBuffer` → `world: World`;`_serve_room_chat_history` → `world.rooms.get(room)`,有则 `tuple(chat_history)`、无则空。**仍不进 GameLoop**(0036 的初衷保留:历史读不占游戏循环)。
5. **dispatch 去掉 history**:删 `Dispatcher.history` 参数与 `ChatMessage` 追加分支(追加已移进 reduce);lifespan 组装同步改。**删除 `app/shell/history.py`** 与其测试(不留死代码)。
6. **wire 协议零变化**:`fetch_room_chat`/`room_chat_history` 报文形状不动,前端无需改代码;行为差异一句进 BACKEND_GUIDE:「聊天历史随房间销毁而消失(同名新房是全新历史)」。

## 测试(计划)

- 回归主钉:**房间销毁(末人离开)→ 同名重建 → `fetch_room_chat` 为空**(杀跨世代泄露)。
- `deepcopy(Room)` 保留 `chat_history` 的 maxlen 与内容(工作副本正确性)。
- `_room_chat` 追加语义:历史追加 + 广播对象一致 + 房间其余字段深等(改写 0021 只读守护)。
- 环形上限:超 `chat_history_size` 淘汰最旧(原 test_room_chat_history 的 cap 用例迁移)。
- Receiver 读 world 服务历史:房在(含刚聊过)/ 房不存在 → 空;不进 inbox。
- 建房链:`RoomCreate.chat_history_size` 从 shell 盖入 → 新房 deque maxlen 正确。

## 文档(计划)

- messaging.md:§房聊持久化/环形缓冲整节改写(挂 Room、随房生灭、cap 经 RoomCreate、Receiver 读 committed world)。
- connection.md:Dispatcher 全景表去 `history`;Receiver 的只读 world 豁免记录(与 presence 同款)。
- core.md:Room 要点行补 `chat_history`;`JoinRoom`/`RoomCreate` 字段说明。
- storage.md:一句——房态不落库涵盖聊天历史(随房消失是有意语义)。
- frontend/BACKEND_GUIDE.md:历史随房销毁一句(流程纪律:涉前端认知的变动必须同步该文档,0070 起入规)。
- gameconfig.py:`ROOM_CHAT_HISTORY_SIZE` 注释改「经 RoomCreate 盖进新房」。

## 实际改了什么(与「打算」对照)

按蓝图 1–6 全部落地,两处实现细化:

- `Room.chat_history` 的 `ChatMessage` 类型标注走 `TYPE_CHECKING` 延迟引用(运行期 `wire.server` 也引 `core.enums`,eager import 会构成环;core import wire DTO 的许可不变,reduce 仍运行期构造 DTO)。
- `_room_chat` 的 0021「只读 + 深比较守护」测试改为:断言唯一改动是 `chat_history` 追加且入历史的就是广播那条,快照补上该条后其余字段深等(守护语义保留、面收窄)。

落点:`domain.py`(字段)/`commands.py`(`RoomCreate.chat_history_size`)/`reduce.py`(建房 deque(maxlen)/`_room_chat` 追加)/`receiver.py`(签名 `history`→`world`;`_serve_room_chat_history` 只读 committed world;`_build_join` 盖 `ROOM_CHAT_HISTORY_SIZE`)/`dispatch.py`(去 history 参数与追加分支)/`lifespan.py`(去 buffer、run_receiver 传 world)/**删除 `shell/history.py` + `tests/shell/test_history.py`**/`_fakes.Shell` 持 world/`gameconfig` 注释。

测试:新 `tests/core/test_room_chat_history.py` 3 测(**主钉:销房→同名重建→历史为空**[0071 前必红] / 环形上限经 RoomCreate / 工作副本 deepcopy 保内容+maxlen)+ 迁移(receiver 三个 fetch 测改走 world、r2 预置改挂 Room / dispatch 删「chat 入缓冲」测[职责已移 reduce] / RoomCreate 构造点 +chat_history_size ×5 / 0021 只读守护收窄);698 全绿(700 − test_history 2 − dispatch 1 + 新 3;净 −2)。

docs:messaging.md(§房聊环形历史改写:挂 Room/随房生灭/cap 经 RoomCreate/契约 7)、connection.md(Dispatcher 全景与伪码去 history + lifespan 步 7 注 + Connection 伪码补 `session` 字段[0070 欠账顺手补])、core.md(Room 要点/RoomChat 行/RoomCreate 字段/房间生命周期 Disconnect 句同步 0070+0071)、storage.md(动态房一句:房内一切含 chat_history 随房消亡是有意语义)、wire-protocol-guide §3(fetch_room_chat 行:历史随房销毁)、**frontend/BACKEND_GUIDE.md §8**(同句,按 0070 新纪律)、gameconfig 注释。

## 自 review

对照 [review.md](../review.md) 逐维:

- **① 分层 / 不变量**:追加在 reduce 经工作副本(world 只由 commit 改);Receiver 读 world 是 presence/lobby-REST 同款**只读豁免**(tuple() 快照、容忍滞后、不做实时裁定),已写进 receiver 注释与 connection.md;core→wire import 走 TYPE_CHECKING 防环;`RoomCreate.chat_history_size` 由 shell 盖,core 不 import config;同一 msg 对象入历史与事件——构造后无人改,不违不变量 7(注释记明)。
- **② 代码↔文档**:七处文档与实现逐条对齐(上列);messaging.md 契约 7 的「reduce 维持只读」已改(那句 0071 后为假)。
- **③ 文档↔文档**:messaging ↔ core ↔ connection ↔ storage ↔ 两份前端文档的「随房生灭」口径一致;0036 历史记录保留原样(历史准确:当时确是 shell 缓冲)。
- **④ 数据模型**:`chat_history` 注明「纯展示、规则不读」+ 直接构造默认无界仅测试用;`deepcopy` 保 maxlen 有测钉(环形上限不静默失效)。
- **⑤ 规范**:删 history.py 不留死代码;无新裸字面量(cap 走 RoomCreate←gameconfig;测试的 cap=3 是用例值)。
- **⑥ 测试**:主钉直击被修缺陷(跨世代泄露);上限/深拷/receiver 读 world(三个迁移的 fetch 测端到端穿 reduce→world→Sender)全覆盖;0021 守护收窄后仍深比较其余字段。
- **⑦ 流程账本**:设计先行且已单独提交(e9e0b39);打算↔实际差异两处上记;BACKEND_GUIDE 同步践行 0070 新纪律。

**对抗自问(crux)**:①「每命令深拷 ≤N 条消息」的代价——用户知情定案,messaging.md 记档;N 上限 1000(gameconfig Field),最坏一房 1000 条短消息深拷仍毫秒级,可接受。②Receiver 读 world 会不会读到「销毁半途」?——单线程 asyncio,commit 是同步替换引用,读者要么见旧要么见新,不撕裂(与 presence 同理)。③事件与历史共享对象:commit 后旧副本不再被改(替换引用),Sender 序列化的与历史存的同为不可变使用的 Pydantic 对象,无写者。0 未处置发现。
