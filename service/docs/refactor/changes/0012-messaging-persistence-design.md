# 0012 · messaging 持久化设计:房聊内存历史 + 私信未读收件箱

> 编号:本篇是用户发起的 P7 messaging **设计讨论**,先于 reduce 动作篇落地;**0011 仍留给 `_player_action`**(见 [0010](0010-p1-reduce-start-hand.md) 待办),故本篇取 0012。

日期:2026-06-18 · 范围:`service/docs/`(只动文档,未动代码)——`messaging.md`(持久化与离线送达 + 契约 + 待定)、`db.md`(DM 两类写实例 + 配置 + 注意点)、`TODO.md`(P7 messaging)、本篇。

## 背景 / 用户提问

用户读完 [messaging.md](../../messaging.md) 后问:好友私信和房间内信息现在怎么做、未读/已读能保留多久。彼时文档现状是 **「默认 ephemeral、不持久化」**:房聊纯实时推送、离线跳过;私信 v1「离线直接丢 + `DMUndelivered`」;**既无已读/未读、也无任何保留**。用户据此拍板:**房聊放内存即可不持久化;私信评估是否需要持久化并补一套设计。**

按 [README §0](../README.md) 先质疑「私信要不要持久化」——结论:**需要**。判据延续本就分两路的那条(房聊要 world 成员名单、私信只要 nick→连接):房聊是「此刻在场」的同步消息,离线即错过、阅后即焚符合直觉;私信点对点、**天然异步**(好友常不在线),v1 的「离线直接丢」对「给好友留言」体验很差。

## 设计讨论(3 个产品取舍 → 用户决定)

向用户确认三处分叉(推荐项 + 用户结论):

1. **私信持久化模型** —— 推荐「离线收件箱(在线直投不入库、离线才存)」。**用户追问点睛**:「对方在线但未读、随后下线,这条未读是不是也要落库?」→ 是。于是**把触发从「离线」改成「未读」**:每条私信**发出即落库(未读)**,读了才标记/清。这比朴素「离线收件箱」更正确,直接覆盖「在线收到没读就下线」。
2. **已读/未读程度** —— 用户选 **「完整已读回执」**:发件人能看到「对方已读 + 时间」。需要 `read-ack` + 回执报文 + `read_at`。
3. **保留多久** —— 用户选 **「已读即删 + 未读保活」**,并强调**保留时间必须可配置、不硬编码**。

## 设计决策(落定)

### 房聊:shell 内存环形缓冲(不持久化)

- `room_chat: dict[room, deque(maxlen=ROOM_CHAT_HISTORY_SIZE)]`,**放 shell 不放 world**——保住 `RoomChat` reduce 只读(否则变写命令、且把非游戏状态塞进 core 域模型)。
- 写:dispatch 见 `Broadcast(ChatMessage)` 即 append。看历史:客户端 (重)进房发 `FetchRoomChat`,shell 直接回 `Personal(RoomChatHistory)`(拉取式,不耦合 StateSnapshot)。清理:随房销毁删缓冲。容量进 config。崩溃即丢(本就 ephemeral)。

### 私信:DB 权威的「未读收件箱 + 完整已读回执」

- **DB 权威(非内存权威)**:私信不参与实时裁定、要跨会话存活,像手牌记录一样发即异步落库、登录读 DB;在线直投只是实时优化。
- **两类写**(接 [db.md](db.md),不新开通道):`direct_message` = **事件写 `DMWrite`**(追加,`dedupe_key=msg_id` 幂等);`dm_read_cursor` = **状态写 `DMReadCursorWrite`**(覆盖,`key=("dm_cursor", reader_uid, peer_uid)` 只留最新游标)。
- **游标表一表两用**:收件人侧算未读(`created_at > 游标`),发件人侧的**已读回执**= 查 `游标 where peer=我`——回执无需另存。
- **时间游标**:用 shell 盖的墙钟 `created_at` 排序/比较,躲开「自增 id 跨重启不单调」;`msg_id` 只作 dedupe + wire 引用。
- **键用 `uid` 不用 `nick`**(nick 可改名);wire 用 nick,边界转换。
- **私信路由 = 写缓冲第二个生产者**:`put_*` 同步无 await,唯一写库者仍 PersistWriter;路由内绝不 `await commit`(否则第二个 DB 写者、破 db.md 不变量 5)。这是「私聊是 outbound 第二生产者」(messaging 契约 3)向写缓冲的延伸。
- **保留**:未读保活到被读;已读再留 `DM_READ_RETENTION_SECONDS`(默认 7 天,可配)后由 **PersistWriter** 周期 `DELETE`(DELETE 也归唯一写者),周期 `DM_CLEANUP_INTERVAL_SECONDS`。
- **崩溃/竞态(接受)**:发后未 flush 即崩丢最近几条(同手牌记录);A 发给离线 B、append 未落库时 B 恰登录读 DB 这条本轮漏但不丢(下个 flush 进 DB、自愈)。要消窗口加「内存未读镜像」——列 future。

### 当前局限(诚实记)

代码**无好友表**,所以「好友私信」当前 = 任意 `nick→nick`(对端存在即可)。好友关系/「仅好友可私信」/黑名单——列 future。

## 改了什么(文档)

- `messaging.md`:私聊「离线」分支改为「落库未读、不再直接丢」;新增 **「持久化与离线送达」** 整节(房聊环形缓冲 + 私信未读收件箱 + 完整已读回执 + 保留/清理 + 竞态);契约 +3 条(6 私信未读收件箱 / 7 房聊内存历史 / 8 键用 uid + 配置化);待定区移除已落定的「持久化 / 新进房看历史」,补「仅好友可私信 / 内存未读镜像 / 全量聊天记录」。
- `db.md`:实例清单 +「私信」;`Persist 接口` +`DMWrite`(事件写)/`DMReadCursorWrite`(状态写);配置 +`DM_READ_RETENTION_SECONDS`/`DM_CLEANUP_INTERVAL_SECONDS`(类 + env);注意点 + 「私信是写缓冲第二生产者 + 清理归 PersistWriter」。
- `TODO.md`:P7 messaging 项细化为「房聊环形缓冲 + 私信未读收件箱」并指向本篇。

## 待办 / 下一步

- 实现归 **P7(大厅/查询/聊天)**,依赖 P3 连接层(`conns`/`FetchRoomChat` 路由)、P4 delayDB(写缓冲/PersistWriter)、P6 wire(`DirectMessage`/`DMMarkRead`/`DMDelivered`/`DMRead`/`RoomChatHistory` 等报文落 .py)。
- 新增配置项实现期进 `gameconfig` + `poker.env` + `.example`(`ROOM_CHAT_HISTORY_SIZE`/`DM_*`/`DM_RATE_LIMIT_*`/`DM_MAX_TEXT_LEN`)。
- 可选硬化:内存未读镜像消 flush 窗口竞态;好友表 + 仅好友可私信 + 黑名单。
