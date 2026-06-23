# 0021 · P1 余项:房间聊天 RoomChat(走 reduce 的只读广播)

日期:2026-06-23 · 范围:`app/core/reduce.py`(+`_room_chat`)、`app/wire/server.py`(+`ChatMessage`)、`app/wire/client.py`(+`RoomChat` + `to_command`)、重生成 `frontend/src/types/wire.gen.ts`、`tests/core/test_room_chat.py`(新)、`tests/wire/test_protocol.py`(补样本)、文档(`messaging.md`/`wire-protocol-guide.md`/`core.md`/`TODO.md`)。承「P1 余项」。**纯 core 只读命令 + wire 切片。**

## 背景 / 为什么选这一项(批判性思考,README §0)

P1 余项剩三项:等大盲再入局时机(①.7-10)、JoinRoom+StateSnapshot、RoomChat。批判性权衡:

- **等大盲时机(①.7)** 的「BB 路过座位才入局」判定有真实的循环依赖(候选是否入局影响 BB 位、BB 位又决定候选是否入局),rules.md 本身标「精确判定属实现细节」、明示不做赌场式 dead-button——需专门设计,不宜在连续推进里草率定规则。
- **JoinRoom+StateSnapshot** 是前端头号需求(wire-guide §8),但「整桌快照报文」是较大的 wire 设计(嵌套座位/手牌视图 + 重连恢复的「恢复到什么状态」子题),值得独立一轮做透。
- **RoomChat** 自包含、messaging.md 已钉死(房聊走 reduce、只读、产 `Broadcast`)、可一轮高质量交付,并补一个干净的聊天 wire 切片(协议增量,0016)。

故本轮做 RoomChat,等大盲 / JoinRoom 各留独立轮。

## 关键设计决策(批判性 + 与文档对齐)

1. **`_room_chat` 是只读命令**(messaging.md §房间聊天 / 契约 7):reduce **不改任何游戏状态**,只校验发送者在房 → 产 `Broadcast(room, ChatMessage)`。commit 一个内容相同的房间副本无害(read-only,可来 storage.md 的 `uRead` 免拷,本规模随意)。经 GameLoop 串行 ⇒ 房聊与牌局事件有**确定全局顺序**(messaging.md 优点)。

2. **文本校验(非空 + 长度)+ 限速一律归 shell,不进 core**(偏离 messaging.md 原文「reduce 校验 text 非空且 ≤ 上限」,**当场改文档**)。理由:① messaging.md 自身把**限速放 shell**(「不让刷屏占 GameLoop」)——非空/长度同属「文本/滥用防护」,与限速同一处(Receiver)最一致;② 让 core 保持**零配置、纯只读**(不引 `gameconfig`、不为聊天长度新立 core 常量或错误码),core 只认「在不在房」这一个游戏语义判据;③ 长度上限是可调参数,归 shell 配置(config.md/gameconfig)比塞进 core 干净。→ **同步改 messaging.md**:reduce 只校验在房;text 非空 + 长度 + 限速归 shell 文本防护(随 shell 硬化)。core 唯一错误臂 = `NOT_IN_ROOM`(已存在,无新码)。

3. **wire**:client `RoomChat{text}`(身份不进报文,`to_command` 盖 origin);server `ChatMessage{from_nick, text}` 广播全房(含观战者,messaging.md)。字段 `str`,结构上无 `hole_cards`/`deck`(隐私天然满足,messaging.md 脱敏红线)。无新 enum/值对象,codegen 注册表加 2 消息即可。

## 打算改什么(开工前)

- `app/wire/server.py`:+ `ChatMessage{from_nick,text}` + 注册。
- `app/wire/client.py`:+ `RoomChat{text}` + 注册 + 联合 + `to_command` 一臂。
- `app/core/reduce.py`:reduce match 加 `RoomChat` 臂;`_room_chat`(在房校验 → `Broadcast(ChatMessage)`)。
- 重生成 `frontend/src/types/wire.gen.ts`。
- `tests/core/test_room_chat.py`(新):在房广播 / 不在房 NOT_IN_ROOM / 只读(world 不变、无 Persist)/ 广播到目标房。
- `tests/wire/test_protocol.py`:`_broadcast_samples` + `ChatMessage`;parse/to_command + registry + `RoomChat`。
- 文档:`messaging.md`(reduce 校验范围 + 文本防护归 shell)、`wire-protocol-guide.md`(§3/§4 聊天 + §8 移到已交付)、`core.md`(事件一览补房聊行)、`TODO.md`(勾 RoomChat)。

## 实际改了什么

- **`app/wire/server.py`**:+ `ChatMessage{from_nick,text}` + 注册 `SERVER_MESSAGES`。
- **`app/wire/client.py`**:+ `RoomChat{text}` + 注册 `CLIENT_MESSAGES`/联合 + `to_command` 一臂(盖 origin)。
- **`app/core/reduce.py`**:reduce `match` + `RoomChat` 臂;`_room_chat`(在房校验 → `Broadcast(ChatMessage)`,只读、无状态变更、无 Persist);import `ChatMessage`/`RoomChat`。
- **`frontend/src/types/wire.gen.ts`**:重生成(+`ChatMessage` 接口 + `RoomChat` 接口 + 两联合新增成员;字段 `string`,无新 enum/值对象)。
- **`tests/core/test_room_chat.py`(新)**:6 测试——在房广播(只读、无 Persist)/ 观战者可聊 / 不在房 `NOT_IN_ROOM` / 文本原样转发 + 无隐私字段 / **进行中手牌深比较只读守护**(`hand_world` + `deepcopy` 比 Hand/座位/全局积分)/ **不一致成员防御臂**(在 `world.users` 但不在 `users_in_room` → NOT_IN_ROOM)。后两条为自 review 加固(见下)。
- **`tests/wire/test_protocol.py`**:`_broadcast_samples` + `ChatMessage`(隐私序列化);parse/to_command + registry 覆盖 + `RoomChat`。
- **文档**:`messaging.md`(reduce 只校验在房、文本防护归 shell)、`wire-protocol-guide.md`(§3 `room_chat` / §4 `chat_message` / §8 移至已交付 + 私聊改 `direct_message`)、`core.md`(事件一览补房聊行)、`TODO.md`(勾 RoomChat、拆出 Set*Blind、reduce/tests 状态行 + 计数 200)。

**偏离计划**:范围与「打算」一致。`scripts/gen_wire_ts.py` 注册表驱动、无须改;`test_protocol` registry 覆盖测试如期因新增 client 报文先红、补样本后绿。唯一与 messaging.md 原文的偏离(文本非空/长度从 reduce 移到 shell)是有意的分层决策(决策 2),已同步改 messaging.md。

## 自 review

方法:按 [review.md](../../review.md) 跑**聚焦对抗式 review 工作流**(本批是小的只读改动,范围聚焦——按 review.md「范围 = 本次 diff + 契约消费方」,派 4 个相关维度审查者:只读/分层、wire/隐私、**文档同步**、测试/账本 → 每条候选再派独立反驳者)。结果:**3 候选 / 2 存活 / 1 驳回**;均落在「测试/账本」维,**分层 / wire / 文档同步三维 0 发现**(文档同步专审确认 messaging.md/guide/core.md/TODO 一致、无残留「聊天=未交付」、长度移 shell 与各文档无矛盾)。两条存活(均确认代码正确、只是测试守护偏弱)已当场加固:

- **[minor] 只读守护偏弱**:原 `test_room_chat_broadcasts` 的 before/after 快照在「无进行中手牌」上比、且 `hand` 是引用快照(恒 `None==None`,且即便非空也抓不住原地改),配不上注释「游戏状态一字未动」的承诺。**修**:加 `test_room_chat_readonly_during_active_hand`——用 `hand_world` 造进行中手牌,`copy.deepcopy` 深快照 Room(含 Hand 全字段/各 Seat)+ `world.users`,在局玩家发房聊后**深比较**断言一字未动 + 无 Persist。守护强度现配得上承诺。
- **[nit] NOT_IN_ROOM 仅覆盖 `room is None` 一支**:原测试用大厅 nick(`Z`)触发的是第一分支;第三分支(`nick not in room.users_in_room` 的不一致态)未覆盖。**修**:加 `test_room_chat_inconsistent_membership_not_in_room`(用户在 `world.users` 指向 r1、但从 `r1.users_in_room` 删除 → 仍 NOT_IN_ROOM)。
- **驳回(1)**:`readonly_layering` 维一条候选被反驳者驳倒(核实 `_room_chat` 确为纯只读单行返回,无层泄漏)。

**逐维结论**:

- **① 分层 / 只读 / 核心红线**:`_room_chat` 纯同步、单行只读 return(不碰 hand/seats/users/不产 Persist,深比较测试钉死);`grep app/core` 无 forbidden import;失败 `return [], Err(NOT_IN_ROOM)`、不 raise;`Broadcast` 携 `ChatMessage` 值快照(`str`,不持域活引用);派发按 `users_in_room` 含观战者。
- **② 代码↔文档 / ③ 文档一致**:唯一偏离(文本非空/长度从 reduce 移 shell)已同步 `messaging.md`(§reduce + §文本防护 + 契约 7 自洽);`wire-protocol-guide` §3/§4/§8、`core.md` 事件一览、`TODO`、`error.md` 均一致;计数 202 同步。文档同步专审 0 发现。
- **④ 数据模型**:`ChatMessage`/`RoomChat` 字段 `str`;无新枚举/值对象;身份不进报文(`RoomChat` 仅 `text`,`to_command` 盖 origin)。
- **⑤ 规范**:字段带中文注释;`_room_chat` 注释讲「为什么只读 / 文本防护归 shell」;无魔法数 / 死代码。
- **⑥ 测试**:6 测试覆盖在房广播 / 观战者 / NOT_IN_ROOM 两分支 / 文本原样 / 进行中手牌深比较只读 / 无 Persist;wire 测 `ChatMessage` 序列化无隐私 + `RoomChat` parse/to_command 双向。202 全绿。
- **⑦ 账本**:打算↔实际一致(唯一偏离即文本校验归 shell,已记决策 2 + 同步文档);加固两测记本段;提交引用 `0021`、全英文。

> 批判性自评:本批是干净的只读增量,风险低;对抗 review 未触及正确性,只把「只读守护」从「无手牌 + 引用快照」加强到「进行中手牌 + 深快照」——这正是 review.md「绿测覆盖想到的、review 覆盖没想到的」在小改动上的体现(原 4 测全绿,但守护强度配不上注释承诺)。

## 待办 / 下一步

- 等大盲再入局时机(①.7-10,需专门设计 BB-路过判定)。
- JoinRoom + Connect + StateSnapshot(整桌快照报文 + 重连恢复)。
- shell 硬化:房聊文本非空/长度校验 + 令牌桶限速(messaging.md);房聊内存环形缓冲 + `FetchRoomChat`(P7)。
