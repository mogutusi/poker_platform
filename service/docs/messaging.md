# 聊天与私信(messaging)

## 定位

房间聊天 + 私聊都是**非游戏消息**:不碰牌局状态、不影响公平性。它们按「需不需要 world/房间上下文」分**两条路**:

| | 作用域 | 需要什么 | 走哪条路 |
|---|---|---|---|
| **房聊 room chat** | 一个房间 | 房间成员名单(**world 拥有**) | **走 reduce**,产出 `Broadcast`,复用现成派发 |
| **私聊 DM** | 两个人(可跨房/在大厅) | 仅 nick→连接(**shell 拥有**) | **shell 路由**,不进 GameLoop |

> **为什么分两路**:延续「reduce/world 只管每房间游戏状态,跨房间/非游戏的在外面」(见 [connection.md](connection.md) 的 A/B 落点)。房聊定向到一个房间、靠 world 的 `users_in_room`,顺理成章走 Broadcast;私聊定向到一个人、天然跨房间(发件人在房 A、收件人在房 B 或大厅),**无法 `checkout` 单个房间**,且只需连接表——所以纯 shell 路由。

> 前置:[connection.md](connection.md)(ConnectionManager 按 nick、dispatch、outbound)、[wire.md](wire.md)(报文字段在 .py,本文只给概念)、[lobby.md](lobby.md)(谁在哪个房/大厅)。

## 房间聊天(走 reduce)

- **命令** `RoomChat(text)`(`origin` = 发件人 nick):目标房 = `world.users[origin].room`(命令不带 room,见 [lobby.md](lobby.md))。
- **reduce**:只校验发送者在房内(`nick in world.rooms[room].users_in_room`);**不改任何游戏状态**;产出 `Broadcast(room, ChatMessage{from_nick, text})`。**文本校验(非空 + 长度)归 shell 文本防护**(与限速同处,见下「限速」)——让 core 保持零配置、纯只读,只认「在不在房」这一游戏判据(决策见 [changes/0021](refactor/changes/0021-p1-room-chat.md))。
- **派发**:dispatch 按 `world.rooms[room].users_in_room` 发给全房(含观战者),OFFLINE 跳过(见 [connection.md](connection.md))。
- **读写性质**:房聊是**只读命令**(不改 world),可走 [storage.md](storage.md) 的 `uRead` 只读路径、免深拷;即便走默认深拷也无害(产出 events、commit 一个内容相同的副本),本规模随意。
- **顺序**:经 GameLoop 串行,所以房聊与牌局事件**有确定全局顺序**,各客户端看到的次序一致(房聊走 reduce 的额外好处)。
- **文本防护 + 限速(已落地,[changes/0033](refactor/changes/0033-room-chat-text-guard.md))**:在 **shell**(Receiver `_guard_room_chat` 收到 `RoomChat` 先做文本校验「非空(strip 后)→ `INVALID_MESSAGE`;`text ≤ ROOM_CHAT_MAX_TEXT_LEN` → 超则 `MESSAGE_TOO_LONG`」+ 过**令牌桶**(每连接 `chat_bucket`,见 [ratelimit](../app/shell/ratelimit.py))→ `RATE_LIMITED`,超了直接丢 + 回 `Err`,**不让刷屏 / 超长文本占 GameLoop**),阈值进 [config.md](config.md)(`ROOM_CHAT_MAX_TEXT_LEN`/`ROOM_CHAT_RATE_BURST`/`ROOM_CHAT_RATE_PER_SEC`,现 dev 常量、P8 env 化)。**校验序**:内容(空/长)先拒(根本不到 GameLoop、不耗令牌),内容合法**再**过桶。`_room_chat` reduce 保持只读、不重复校验(0021 决策)。令牌桶挂连接 ⇒ 重连/顶替起新连接桶重置(v1 接受,见 [changes/0033](refactor/changes/0033-room-chat-text-guard.md) 决策 3)。

## 私聊(shell 路由,不进 GameLoop)

> **「发」路已落地 [changes/0038](refactor/changes/0038-dm-send-deliver.md)**:`DirectMessage` → shell 路由([`route_direct_message`](../app/shell/messaging.py),Receiver 拦截、不投 `inbox`)→ 防护 → 解析 uid → `put(DMWrite)`(必落 = 未读)→ 在线再投 `DMDelivered`。
> **「读」路·游标写已落地 [changes/0039](refactor/changes/0039-dm-read-cursor.md)**:`DMMarkRead` → [`route_dm_mark_read`](../app/shell/messaging.py) → `put(DMReadCursorWrite)`(状态写,按 `(reader,peer)` 覆盖)→ 对端在线再回 `DMRead` 回执。
> **「读」路·登录补收已落地 [changes/0040](refactor/changes/0040-dm-login-catchup.md)**:(重)连时 [`deliver_dm_catch_up`](../app/shell/messaging.py) 读 DB → 补发未读 `DMDelivered` 列表 + 已读回执 `DMRead` 列表(复用现有报文,无协议增量;客户端按 `msg_id` 去重)。**保留清理**随 **0041/future**。

- **报文** `DirectMessage{to_nick, text}` → **shell 路由 [`route_direct_message`](../app/shell/messaging.py) 直接处理,不投 `inbox`**(Receiver 拦截,同 `FetchRoomChat`)。
- **路由**(`conns.get(to_nick)`,见 [connection.md](connection.md) 的全局 nick 表):**无论在线与否,先 `put(DMWrite)` 必落库**(事件写,= 未读;`dedupe_key = msg_id`,幂等);再按在线态叠加实时投递——
  - **在线** → `enqueue(对方连接, DMDelivered{msg_id, from_nick, text, created_at})`。**实时投递尽力而为**:对方 `outbound` 满(慢客户端)→ 丢这次实时投递 + WARNING,**不丢消息**(已落库,登录补收兜);**不在此 drop 收件人连接**(本协程是发件人 Receiver,drop 收件人是 GameLoop/其自身背压职责)。
  - **离线** → 仅落库,对方登录补收(见下「持久化与离线送达」)。
- **失败回执二分(0038 落定,澄清本文早先口径)**:
  - **对端根本不存在**(`to_nick` 无 DB 行)→ `DMUndelivered{to_nick}`(投递结果,带 `to_nick` 供前端把该条外发标失败;**不落库**)。
  - **空 / 超长 / 发给自己 / 限速**(校验错)→ `ErrorMessage`(`INVALID_MESSAGE` / `MESSAGE_TOO_LONG` / `CANNOT_DM_SELF` / `RATE_LIMITED`,同 [0033](refactor/changes/0033-room-chat-text-guard.md) 房聊防护回执通道)。
- **身份**:发件人 = **连接绑定的 nick**(不信报文自报);收件人 = `to_nick`;**禁止发给自己**(`CANNOT_DM_SELF`)。`msg_id = uuid4().hex`(shell 生成,比 `from_uid:微秒` 稳——免同微秒撞键);`created_at` = shell 墙钟。**v1 发件人成功路径零回包**(本地乐观渲染;送达确认走 0039 `DMRead` 已读回执)。
- **防护序(同 0033)**:空 → 超长 → 自发 → 限速 **先拒**(廉价、不耗令牌、不读 DB),合法**再**过令牌桶(每连接 `dm_bucket`),过桶**才**读 DB 解析 uid(贵)。
- **第二个 outbound 生产者**:私聊路由和 GameLoop 都 `put_nowait` 到 `outbound`——单线程 asyncio 下安全;私聊与游戏消息之间**不保证相对顺序**(对聊天无所谓,可接受)。**仍只经 `outbound` → Sender**,不旁路 `ws.send`(守不变量 4/6)。
- **限速**:在 shell(发件人维度令牌桶 `dm_bucket`,与房聊 `chat_bucket` 各一桶),阈值 `DM_MAX_TEXT_LEN`/`DM_RATE_BURST`/`DM_RATE_PER_SEC` 进 [config.md](config.md)(现 dev 常量、P8 env 化)。

## 表情(emoji,见 [changes/0034](refactor/changes/0034-emoji-catalog-design.md))

聊天**表情 = 前端渲染约定 + 一份共享目录**,后端几乎零改动。适用**所有聊天面**(房聊现、私聊将来)——它是渲染约定,不是某条消息的字段。

- **格式 `[code]`**:消息文本里用定界括号引用,`code` 是稳定 **ASCII snake_case** 键(如 `[smile]`/`[poker_face]`/`[all_in]`)。定界括号边界无歧义(优于 `#code`)、贴合微信/QQ 习惯;**显示名(label)可中文**。
- **后端纯透传,渲染在前端**:`ChatMessage.text`(及未来 DM)**完全不变**,`[code]` 当普通文本随 `text` 流转;`_room_chat` reduce **维持只读、不校验/不转换**(承 0021/0033)。前端按目录把**已知** code 换成字形(Unicode 表情或自定义贴纸),**未知 `[foo]` 原样显示为文本**——绝不因含方括号而拒收。**无新增 wire 字段/消息**。
- **目录 = 单一事实源,codegen 到 TS(已落地 [0035](refactor/changes/0035-emoji-implementation.md))**:后端封闭目录 [app/wire/emoji.py](../app/wire/emoji.py)(`EmojiCode` 枚举 + `EMOJI_CATALOG{label,glyph}`)→ `gen_wire_ts.py` 无条件吐 `EmojiCode`/`EmojiMeta`/`EMOJI_CATALOG` 进 `wire.gen.ts`,前端只消费、不手写第二份(杜绝 FE/BE 漂移);前端 [utils/emoji.ts](../../frontend/src/utils/emoji.ts) 的 `tokenizeChat`/`chatToPlainText` 据目录渲染,可按 code 覆盖为自定义贴纸图(故同一目录兼容 Unicode 表情与贴纸)。
- **边界(v1)**:`[code]` 计入 `ROOM_CHAT_MAX_TEXT_LEN`(全表情消息仍有界);**字面量** `[smile]` 的转义(如 `\[smile]`)、服务端 code 校验/统计、富文本/@提及 均为后续(见下「待定」)。

## presence(在线判定,本文只用到一点)

私聊判断"对方在不在线" = `conns.get(to_nick) is not None`——就是 ConnectionManager 的 nick 表(presence)。完整 presence(谁在线/在哪房/什么状态,供大厅人数、好友在线提醒)是更大的只读视图,**单列或后续并入**,本文只用"在不在线"这一点。

## 脱敏与隐私

- 聊天正文**不得携带游戏隐私**(底牌 `hole_cards` / 牌堆 `deck`)——这是文本消息,不该出现这些字段(并入 [log.md](log.md) 红线)。
- **决策(可改)· 日志不记正文**:只记元数据(`from_nick`→`to_nick`/`room`、长度、是否投达),不记聊天内容本身(隐私 + 噪音)。要审计违规内容再单开。

## 持久化与离线送达(房聊内存历史 · 私信未读收件箱)

> 落定本篇的持久化决策(取代旧「待定·持久化 / 新进房看历史」)。一句话:**房聊只在内存留最近 N 条(不落库)、私信落库做「未读收件箱 + 完整已读回执」**。判据延续分两路那条——房聊是「此刻在场」的同步消息,离线即错过、阅后即焚足矣;私信点对点、天然异步,好友离线也得收到。

### 房聊:shell 内存环形缓冲(不持久化,已落地 [changes/0036](refactor/changes/0036-room-chat-history.md))

- **存哪**:shell [`RoomChatBuffer`](../app/shell/history.py) 持 `dict[room, deque(maxlen=ROOM_CHAT_HISTORY_SIZE)]`——每房一个定长环形缓冲,**只在内存,不进 world、不落库**。
- **决策(可改)· 放 shell 不放 world**:这样 `RoomChat` 的 reduce 维持**只读**(只产 `Broadcast(ChatMessage)`、不改状态,见上「房间聊天」/[storage.md](storage.md))。备选「`chat_log` 放 `world.rooms[room]`」会让 RoomChat 变写命令、把非游戏状态塞进 core 域模型——否决。
- **写入**:dispatch 派发 `Broadcast(room, msg)` 时,`msg` 是 `ChatMessage` 就 `buffer.append(room, msg)`(一处 `isinstance`;RoomChat reduce 只产这一种 chat 广播)。次序由 GameLoop 串行保证;房已销毁(`rooms.get` 为 None)早退不入。
- **看历史(新进房 / 重连)**:客户端 (重)进房后发 `FetchRoomChat{room}`(ws),**Receiver/shell 直接处理、不进 GameLoop**,**直接 enqueue `RoomChatHistory{room, messages}`** 到该连接 `outbound`(非 `Personal` 事件——同私聊 `DMDelivered` / Receiver 错误回执的直发路径)。决策(可改):拉取式比「dispatch 发 `StateSnapshot` 时自动附带」更解耦。
  - **`room` 进报文(修订 0036)**:私聊只需 `conns.get(nick)`(shell 表),但房聊历史需目标房,而房在 `world.users[nick].room`(world 态)——**shell 协程不得读 world(不变量 2)**。故 `FetchRoomChat` 带房名(同 `JoinRoom`),shell 据报文房名直读缓冲,**不读 world、不进 GameLoop**。
  - **v1 不校验成员资格**:跨房拉历史可接受——房聊是**公开非敏感**态(privacy 红线只护 hole_cards/deck)、≤20 内网、拉者本可进该房看。严格成员校验需走 reduce 解 world(本批不做,见 [changes/0036](refactor/changes/0036-room-chat-history.md) 决策 5)。
- **跨协程共享安全**:dispatch(GameLoop 协程)写 / Receiver(自协程)读——单线程 asyncio 下两端皆无 `await` 同步访问、不中途交错(同 [timer.md](timer.md) dispatch 写 / Timer 读 `_action` 表)。`recent` 返回 tuple 快照。
- **清理**:**v1 房静态预置([lobby.md](lobby.md))→ 不销毁 → 无需清理**;缓冲键于固定房集。动态建房(future)时由销毁处删(shell 不读 world,故由 reduce/GameLoop 侧信号触发,非惰性查 `world.rooms`)。
- **容量 / 崩溃**:`ROOM_CHAT_HISTORY_SIZE` 进 [config.md](config.md)(默认 50;现 dev 常量、P8 env 化)。进程崩 → 缓冲全丢;房聊本就 ephemeral,接受([storage.md](storage.md) 崩溃语义)。

### 私信:落库的「未读收件箱 + 完整已读回执」

**核心:落库触发是「未读」,不是「离线」。** 每条私信**发出即落库**(=未读),收件人真正读了回 `read-ack` → 标记已读 + 回执发件人;已读满保留期才清。于是**「在线收到但没读就下线」的消息也不丢**——这正是与「离线收件箱」的关键差别。

**决策(可改)· DM 以 DB 为权威**(不套用游戏状态的「内存权威」):私信不参与实时游戏裁定、天生要跨会话/离线存活,所以像手牌记录一样**DB 权威**——发即异步落库([db.md](db.md) 事件写)、登录/查询读 DB;在线直投只是叠加其上的**实时投递优化**,不改「DB 权威」。

投递与落库(全在 **shell 路由**,不进 GameLoop):

| 时机 | shell 动作 |
|---|---|
| **发**(已落地 [0038](refactor/changes/0038-dm-send-deliver.md))`DirectMessage{to_nick, text}` | `from/to nick→uid`(读路径 `load_uids_by_nicks`;对端不存在→`DMUndelivered`)→ 生成 `msg_id=uuid4` → **`put(DMWrite)`**(事件写,必落、未读)→ 若 `conns.get(to_nick)` 在线再 `enqueue(DMDelivered)` 实时投(尽力而为) |
| **读**(已落地 [0039](refactor/changes/0039-dm-read-cursor.md))`DMMarkRead{peer_nick, read_through}` | `reader/peer nick→uid`(`load_uids_by_nicks`;peer 不存在→`error(INVALID_MESSAGE)`、=自己→`CANNOT_DM_SELF`)→ **`put(DMReadCursorWrite)`**(状态写,`_state_key` 按 `(reader,peer)` 覆盖只留最新;OrmPersister UPSERT,行非必存)推进「我读 ta 的进度」→ 发件人在线则 `enqueue(DMRead)` 回执(尽力而为) |
| **登录补收**(已落地 [0040](refactor/changes/0040-dm-login-catchup.md)) | (重)连时 [`deliver_dm_catch_up`](../app/shell/messaging.py) 读 DB:`load_unread_dms`(`created_at > 游标` 或无游标,旧→新)→ `DMDelivered` 列表;`load_read_receipts`(`游标 where peer=自己`)→ `DMRead` 回执列表。enqueue 本连接 outbound,**不进 GameLoop / 不读 world**;best-effort(DB 失败 / outbound 满则跳过,下次重连补,游标未推进故不丢)。未读数 = 列表长度(无单独「数」报文)|

- **写缓冲的第二个生产者(新不变量)**:私信路由 `put(DMWrite)`/`put(DMReadCursorWrite)` 进写缓冲——`put` 同步无 `await`([db.md](db.md)),asyncio 单线程下与 GameLoop.dispatch 的 put **不交错**;唯一**写库者**仍是 PersistWriter。这是「私聊是 outbound 第二生产者」(契约 3)向写缓冲的自然延伸。**绝不在路由里 `await commit`**(否则出现第二个 DB 写者、破 [db.md](db.md) 不变量 5)。
- **键用不可变 `uid` 不用 `nick`**:落库与游标都按 `User.id`(同 [db.md](db.md) `PointsWrite.uid`——nick 可改名,见 [presence.md](presence.md));wire 上用 nick(显示),收发边界做 nick↔uid 转换。
- **游标表一表两用**:未读 = `created_at > dm_read_cursor[reader=我, peer=对方].read_through_ts` 的行(未读数跨对端求和);**发件人的已读回执** = 查 `dm_read_cursor where peer=我`——「对方把我发的读到了几时」,回执无需另存,游标即真源。
- **时间游标而非自增 id**:用 shell 盖的墙钟 `created_at` 排序/比较(同 [db.md](db.md)「墙钟由 shell 盖」),躲开「自增 id 跨重启不单调」的坑;`msg_id` 只作 `DMWrite` 的 `dedupe_key`(幂等 INSERT)+ wire 引用。

保留多久(「已读即删 + 未读保活」,且**时间可配**;**已落地 [0041](refactor/changes/0041-dm-retention-cleanup.md)**):

- **未读**:保留**直到被读**。**已读**:再留 `DM_READ_RETENTION_SECONDS`(默认 7 天;**进 [config.md](config.md),不硬编码**)后清。
- **清理归唯一写者(0041)**:`PersistWriter.maybe_cleanup` 周期(`DM_CLEANUP_INTERVAL_SECONDS`)附带一趟保留清理 → `OrmPersister.cleanup_dms` `DELETE` 已读(收件人游标 `read_through_ts >= created_at`)且 `created_at < now - 保留期` 的私信。DELETE 也是 DB 写、归唯一写者,**不另起协程写库**(守 [db.md](db.md) 唯一写者);未读永不删、已读未过期留;best-effort(失败 ERROR + 跳过,幂等下周期重删)。
- **崩溃 / 竞态(接受)**:① 发后未 flush 即崩 → 丢最近几条(同手牌记录);② 极小窗:A 发给离线 B、append 还在缓冲未落库时 B 恰登录读 DB → 本轮漏**但不丢**(在缓冲里,下个 flush 进 DB,下次拉 / A 在线时可见),本规模自愈。要消窗可加「内存未读镜像」(见待定)。
- **限速 / 长度**:私信发送维度令牌桶在 shell(进路由前),`DM_RATE_LIMIT_*`;`text ≤ DM_MAX_TEXT_LEN`;均进配置。
- **隐私**:正文现在**落库**(持久化本意),但 [log.md](log.md) 脱敏红线不变——**不写日志**、正文不得带 `hole_cards`/`deck`(见上「脱敏与隐私」)。

## 与架构契约(必须守住)

1. **房聊走 reduce**(产出 `Broadcast`、不改游戏状态);**私聊走 shell 路由**(不进 reduce/world)。判据:要不要房间成员名单(world)。
2. **身份一律取连接绑定的 nick**,绝不信报文自报的发送者(见 [auth.md](auth.md))。
3. **私聊是 `outbound` 的第二生产者**:`put_nowait` 安全,与游戏消息顺序不保证(可接受);仍只经 Sender,不旁路 `ws.send`。
4. **限速在 shell**(进 reduce / 进路由之前),防刷屏拖累 GameLoop。
5. **正文不含游戏隐私**;默认不把聊天正文写日志(私信落库不等于可写日志)。
6. **私信落库走「未读收件箱」**:发即 `put(DMWrite)`(事件写,未读)、读 `put(DMReadCursorWrite)`(状态写)推进游标、已读满保留期由 PersistWriter 清;私信路由是**写缓冲的第二个生产者**(`put` 同步无 await),唯一写库者仍 PersistWriter,路由内**绝不 `await commit`**。
7. **房聊只在内存留最近 N 条**(shell 环形缓冲,不落库),`RoomChat` reduce 维持**只读**;新进房/重连靠 `FetchRoomChat` 拉。
8. **持久化键用 `uid` 不用 `nick`;保留期 / 容量 / 限速一律配置化**(见 [config.md](config.md))。
9. **表情是前端渲染约定 + codegen 共享目录**(`[code]`),后端透传:`ChatMessage`/`_room_chat`/wire 字段**不变**,目录单一事实源(见上「表情」/ [changes/0034](refactor/changes/0034-emoji-catalog-design.md))。

## 待定 / future

- **「持久化 / 新进房看历史」已落定** → 见上「持久化与离线送达」(房聊内存环形缓冲、私信未读收件箱 + 完整已读回执)。
- **仅好友可私信 + 屏蔽 / 黑名单**:当前**无好友表**,私信 = 任意 `nick→nick`(对端存在即可)。好友关系校验、拒收某人(按发件人维度在 shell 路由处过滤)——future。
- **内存未读镜像**:把私信未读也做成「shell 内存权威 + delayDB」,消掉上文那个 flush 窗口竞态、登录补收免读 DB——本规模非必需,future。
- **全量聊天记录**:若日后要双方翻历史,把私信从「已读即删」改成「不删 + 拉历史接口」(分页游标,同 [rest.md](rest.md) 查手牌)——future。
- **系统公告 / 大厅群发**:`LobbyBroadcast`(发给所有大厅连接),接 [lobby.md](lobby.md) 的待定。
- **表情(emoji)**:**已落地** → 见上「表情」节(设计 [0034](refactor/changes/0034-emoji-catalog-design.md) + 实现 [0035](refactor/changes/0035-emoji-implementation.md):`[code]` 前端渲染 + codegen 共享目录,后端透传、**不加协议字段**)。转义字面量 `[code]` / 服务端 code 校验 / 富文本 @提及 是后续 nicety。
- **富文本 / @提及**:协议加字段即可(见 [wire.md](wire.md) 加性演进),本文不展开。
- **完整 presence 模块**:谁在线/在哪房/状态的只读 API,见 [presence.md](presence.md),供大厅、好友、私聊共用。
