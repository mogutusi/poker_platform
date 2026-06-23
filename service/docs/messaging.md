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
- **文本防护 + 限速**:在 **shell**(Receiver 收到 `RoomChat` 先做文本校验「非空 + `text ≤ ROOM_CHAT_MAX_TEXT_LEN`」+ 过令牌桶,超了直接丢 + 回 `Err`),**不让刷屏 / 超长文本占 GameLoop**;阈值进 [config.md](config.md)。非空/长度与限速同属「文本/滥用防护」,集中在进 reduce 之前一处,reduce 不重复校验(随 shell 硬化落地;当前 reduce 已只读、shell 防护待补)。

## 私聊(shell 路由,不进 GameLoop)

- **报文** `DirectMessage{to_nick, text}` → **Receiver(shell)直接处理,不投 `inbox`**。
- **路由**:`conns.get(to_nick)`(见 [connection.md](connection.md) 的全局 nick 表):
  - **在线** → `enqueue(对方连接, DMDelivered{from_nick, text})`,并可回发件人一个回执。
  - **离线** → **不再直接丢**:仍 `put_append` 落库进未读收件箱,对方登录补收(见下「持久化与离线送达」)。`DMUndelivered{to_nick}` 只在 `to_nick` 根本不存在这种硬错误时回。
- **身份**:发件人 = **连接绑定的 nick**(不信报文自报);收件人 = `to_nick`;禁止发给自己;`to_nick` 不存在则回 `Err`。
- **第二个 outbound 生产者**:私聊路由和 GameLoop 都 `put_nowait` 到 `outbound`——单线程 asyncio 下安全;私聊与游戏消息之间**不保证相对顺序**(对聊天无所谓,可接受)。**仍只经 `outbound` → Sender**,不旁路 `ws.send`(守不变量 4/6)。
- **限速**:同样在 shell(发件人维度令牌桶),进配置。

## presence(在线判定,本文只用到一点)

私聊判断"对方在不在线" = `conns.get(to_nick) is not None`——就是 ConnectionManager 的 nick 表(presence)。完整 presence(谁在线/在哪房/什么状态,供大厅人数、好友在线提醒)是更大的只读视图,**单列或后续并入**,本文只用"在不在线"这一点。

## 脱敏与隐私

- 聊天正文**不得携带游戏隐私**(底牌 `hole_cards` / 牌堆 `deck`)——这是文本消息,不该出现这些字段(并入 [log.md](log.md) 红线)。
- **决策(可改)· 日志不记正文**:只记元数据(`from_nick`→`to_nick`/`room`、长度、是否投达),不记聊天内容本身(隐私 + 噪音)。要审计违规内容再单开。

## 持久化与离线送达(房聊内存历史 · 私信未读收件箱)

> 落定本篇的持久化决策(取代旧「待定·持久化 / 新进房看历史」)。一句话:**房聊只在内存留最近 N 条(不落库)、私信落库做「未读收件箱 + 完整已读回执」**。判据延续分两路那条——房聊是「此刻在场」的同步消息,离线即错过、阅后即焚足矣;私信点对点、天然异步,好友离线也得收到。

### 房聊:shell 内存环形缓冲(不持久化)

- **存哪**:shell 持有 `room_chat: dict[room, deque(maxlen=ROOM_CHAT_HISTORY_SIZE)]`——每房一个定长环形缓冲,**只在内存,不进 world、不落库**。
- **决策(可改)· 放 shell 不放 world**:这样 `RoomChat` 的 reduce 维持**只读**(只产 `Broadcast(ChatMessage)`、不改状态,见上「房间聊天」/[storage.md](storage.md))。备选「`chat_log` 放 `world.rooms[room]`」会让 RoomChat 变写命令、把非游戏状态塞进 core 域模型——否决。
- **写入**:dispatch 派发 `Broadcast(room, msg)` 时,`msg` 是 `ChatMessage` 就 `room_chat[room].append(msg)`(一处 `isinstance`;RoomChat reduce 只产这一种 chat 广播)。次序由 GameLoop 串行保证。
- **看历史(新进房 / 重连)**:客户端 (重)进房后发 `FetchRoomChat`(ws),**Receiver/shell 直接处理、不进 GameLoop**(同私聊走 shell 路由的理由),回 `Personal(RoomChatHistory{最近 N 条})`。决策(可改):拉取式比「dispatch 发 `StateSnapshot` 时自动附带」更解耦——shell 不必判断「这条快照是不是新进房触发」。
- **清理**:房间销毁(最后一人离开)时随连接表 `del room_chat[room]`(或惰性:`FetchRoomChat` 见房已不在 `world.rooms` 即回空 + 删)。
- **容量 / 崩溃**:`ROOM_CHAT_HISTORY_SIZE` 进 [config.md](config.md)(默认 50)。进程崩 → 缓冲全丢;房聊本就 ephemeral,接受([storage.md](storage.md) 崩溃语义)。

### 私信:落库的「未读收件箱 + 完整已读回执」

**核心:落库触发是「未读」,不是「离线」。** 每条私信**发出即落库**(=未读),收件人真正读了回 `read-ack` → 标记已读 + 回执发件人;已读满保留期才清。于是**「在线收到但没读就下线」的消息也不丢**——这正是与「离线收件箱」的关键差别。

**决策(可改)· DM 以 DB 为权威**(不套用游戏状态的「内存权威」):私信不参与实时游戏裁定、天生要跨会话/离线存活,所以像手牌记录一样**DB 权威**——发即异步落库([db.md](db.md) 事件写)、登录/查询读 DB;在线直投只是叠加其上的**实时投递优化**,不改「DB 权威」。

投递与落库(全在 **shell 路由**,不进 GameLoop):

| 时机 | shell 动作 |
|---|---|
| **发** `DirectMessage{to_nick, text}` | `to_nick→to_uid`(读路径;不存在→`Err`)→ 生成 `msg_id` → **`put_append(DMWrite)`**(必落,未读)→ 若 `conns.get(to_nick)` 在线再 `enqueue(DMDelivered)` 实时投 |
| **读** `DMMarkRead{peer_nick, read_through}` | **`put_state(DMReadCursorWrite)`** 推进「我读 ta 的进度」(覆盖只留最新)→ 发件人在线则 `enqueue(DMRead)` 回执 |
| **登录补收** | shell 读 DB:对每个对端取 `created_at > 游标` 的未读 → `DMDelivered` 列表 + 未读数;再读 `游标 where peer=自己` 得「对方已读到哪」补回执 |

- **写缓冲的第二个生产者(新不变量)**:私信路由 `put_append/put_state` 进写缓冲——`put_*` 同步无 `await`([db.md](db.md)),asyncio 单线程下与 GameLoop.dispatch 的 put **不交错**;唯一**写库者**仍是 PersistWriter。这是「私聊是 outbound 第二生产者」(契约 3)向写缓冲的自然延伸。**绝不在路由里 `await commit`**(否则出现第二个 DB 写者、破 [db.md](db.md) 不变量 5)。
- **键用不可变 `uid` 不用 `nick`**:落库与游标都按 `User.id`(同 [db.md](db.md) `PointsWrite.uid`——nick 可改名,见 [presence.md](presence.md));wire 上用 nick(显示),收发边界做 nick↔uid 转换。
- **游标表一表两用**:未读 = `created_at > dm_read_cursor[reader=我, peer=对方].read_through_ts` 的行(未读数跨对端求和);**发件人的已读回执** = 查 `dm_read_cursor where peer=我`——「对方把我发的读到了几时」,回执无需另存,游标即真源。
- **时间游标而非自增 id**:用 shell 盖的墙钟 `created_at` 排序/比较(同 [db.md](db.md)「墙钟由 shell 盖」),躲开「自增 id 跨重启不单调」的坑;`msg_id` 只作 `DMWrite` 的 `dedupe_key`(幂等 INSERT)+ wire 引用。

保留多久(「已读即删 + 未读保活」,且**时间可配**):

- **未读**:保留**直到被读**。**已读**:再留 `DM_READ_RETENTION_SECONDS`(默认 7 天;**进 [config.md](config.md),不硬编码**)后清。
- **清理归唯一写者**:PersistWriter 周期里附带一趟保留清理(`DELETE` 已读且 `created_at < now - 保留期`),周期 `DM_CLEANUP_INTERVAL_SECONDS`。DELETE 也是 DB 写、归唯一写者,**不另起协程写库**(守 [db.md](db.md) 唯一写者)。
- **崩溃 / 竞态(接受)**:① 发后未 flush 即崩 → 丢最近几条(同手牌记录);② 极小窗:A 发给离线 B、append 还在缓冲未落库时 B 恰登录读 DB → 本轮漏**但不丢**(在缓冲里,下个 flush 进 DB,下次拉 / A 在线时可见),本规模自愈。要消窗可加「内存未读镜像」(见待定)。
- **限速 / 长度**:私信发送维度令牌桶在 shell(进路由前),`DM_RATE_LIMIT_*`;`text ≤ DM_MAX_TEXT_LEN`;均进配置。
- **隐私**:正文现在**落库**(持久化本意),但 [log.md](log.md) 脱敏红线不变——**不写日志**、正文不得带 `hole_cards`/`deck`(见上「脱敏与隐私」)。

## 与架构契约(必须守住)

1. **房聊走 reduce**(产出 `Broadcast`、不改游戏状态);**私聊走 shell 路由**(不进 reduce/world)。判据:要不要房间成员名单(world)。
2. **身份一律取连接绑定的 nick**,绝不信报文自报的发送者(见 [auth.md](auth.md))。
3. **私聊是 `outbound` 的第二生产者**:`put_nowait` 安全,与游戏消息顺序不保证(可接受);仍只经 Sender,不旁路 `ws.send`。
4. **限速在 shell**(进 reduce / 进路由之前),防刷屏拖累 GameLoop。
5. **正文不含游戏隐私**;默认不把聊天正文写日志(私信落库不等于可写日志)。
6. **私信落库走「未读收件箱」**:发即 `put_append(DMWrite)`(未读)、读 `put_state(DMReadCursorWrite)` 推进游标、已读满保留期由 PersistWriter 清;私信路由是**写缓冲的第二个生产者**(`put_*` 同步无 await),唯一写库者仍 PersistWriter,路由内**绝不 `await commit`**。
7. **房聊只在内存留最近 N 条**(shell 环形缓冲,不落库),`RoomChat` reduce 维持**只读**;新进房/重连靠 `FetchRoomChat` 拉。
8. **持久化键用 `uid` 不用 `nick`;保留期 / 容量 / 限速一律配置化**(见 [config.md](config.md))。

## 待定 / future

- **「持久化 / 新进房看历史」已落定** → 见上「持久化与离线送达」(房聊内存环形缓冲、私信未读收件箱 + 完整已读回执)。
- **仅好友可私信 + 屏蔽 / 黑名单**:当前**无好友表**,私信 = 任意 `nick→nick`(对端存在即可)。好友关系校验、拒收某人(按发件人维度在 shell 路由处过滤)——future。
- **内存未读镜像**:把私信未读也做成「shell 内存权威 + delayDB」,消掉上文那个 flush 窗口竞态、登录补收免读 DB——本规模非必需,future。
- **全量聊天记录**:若日后要双方翻历史,把私信从「已读即删」改成「不删 + 拉历史接口」(分页游标,同 [rest.md](rest.md) 查手牌)——future。
- **系统公告 / 大厅群发**:`LobbyBroadcast`(发给所有大厅连接),接 [lobby.md](lobby.md) 的待定。
- **富文本 / @提及 / 表情**:协议加字段即可(见 [wire.md](wire.md) 加性演进),本文不展开。
- **完整 presence 模块**:谁在线/在哪房/状态的只读 API,见 [presence.md](presence.md),供大厅、好友、私聊共用。
