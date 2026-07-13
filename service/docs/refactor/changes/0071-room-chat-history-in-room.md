# 0071 · 房聊历史挂进 Room(随房生灭)——设计定案,待实施

日期:2026-07-13(设计)· 范围(计划):`app/core/domain.py`(`Room.chat_history`)、`app/core/commands.py`(`RoomCreate.chat_history_size`)、`app/core/reduce.py`(`_join_room` 建房带 maxlen / `_room_chat` 追加)、`app/shell/receiver.py`(FetchRoomChat 改读 committed world;签名去 `history` 换 `world`)、`app/shell/dispatch.py`(去 history 参数与追加分支)、`app/shell/lifespan.py`(接线)、**删除 `app/shell/history.py`**、tests、docs(messaging/connection/core/storage + frontend/BACKEND_GUIDE.md)。

> **状态:设计已与用户定案(本篇),实施在 0070(连接与会话生命周期)落地之后。** 若上下文压缩,凭本篇即可完整实施。

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

(实施后回填)

## 自 review

(push 前回填)
