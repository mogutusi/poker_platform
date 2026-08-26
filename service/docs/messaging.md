# 聊天与私信(messaging)

## 定位

一句话:房间聊天走 reduce,私聊走 shell 路由,分界线是「需不需要房间成员名单」。

两者都是非游戏消息:不碰牌局裁定、不影响公平性。

| | 作用域 | 需要什么 | 走哪条路 |
|---|---|---|---|
| 房聊 room chat | 一个房间 | 房间成员名单(world 拥有) | 走 reduce,产出 `Broadcast` |
| 私聊 DM | 两个人(可跨房/在大厅:收发双方不必在同一个房间,甚至可以都不在房间里) | 仅 nick→连接(shell 拥有) | shell 路由,不进 GameLoop |

判据延续「reduce/world 只管每房间游戏状态,跨房间/非游戏的在外面」,见 [connection.md](connection.md) 的 A/B 落点。私聊必须在 shell:它天然跨房间,无法 `checkout`(取出某个房间的状态副本来算)单个房间,且只需连接表、不需要 world。

> 前置:[connection.md](connection.md)(ConnectionManager 按 nick、dispatch、outbound)、[wire.md](wire.md)(报文字段在 .py,本文只给概念)、[lobby.md](lobby.md)(谁在哪个房/大厅)。

## 房间聊天(走 reduce)

一句话:文本校验和限速在 shell 先过一遍,过关的才进 reduce 广播给全房。

**命令**

- `RoomChat(text)`,`origin` = 发件人 nick;目标房 = `world.users[origin].room`,命令本身不带 room,见 [lobby.md](lobby.md)。

**reduce**

- 只校验发送者在 `room.users_in_room`;文本校验(非空 + 长度)归 shell,core 只认「在不在房」这一游戏判据,见 [changes/0021](refactor/changes/0021-p1-room-chat.md)。
- 不改任何游戏裁定状态,唯一写入是追加 `Room.chat_history`(0071,见下「持久化」)。
- 产出 `Broadcast(room, ChatMessage{from_nick, text})`。

**派发**

- dispatch 按 `users_in_room` 发给全房,含观战者;OFFLINE 跳过,见 [connection.md](connection.md)。

**顺序**

- 经 GameLoop 串行,房聊与牌局事件有确定全局顺序,各客户端次序一致。

**文本防护 + 限速**(shell,Receiver `_guard_room_chat`,[changes/0033](refactor/changes/0033-room-chat-text-guard.md))

校验按顺序三步,任一步失败即回 `Err`:

1. 非空(strip 后),否则 `INVALID_MESSAGE`。
2. `text ≤ ROOM_CHAT_MAX_TEXT_LEN`,否则 `MESSAGE_TOO_LONG`。
3. 过每连接令牌桶 `chat_bucket`(见 [ratelimit](../app/shell/ratelimit.py)),超则 `RATE_LIMITED`。令牌桶 = 攒一小把额度,发一条扣一个、按时间回补。

补充几点:

- 顺序有意:内容错先拒,不耗令牌;超限的消息直接丢 + 回 `Err`,不占 GameLoop;reduce 不重复校验(0021)。
- 阈值 `ROOM_CHAT_MAX_TEXT_LEN`/`ROOM_CHAT_RATE_BURST`/`ROOM_CHAT_RATE_PER_SEC` 进 [config.md](config.md)。
- 桶挂在连接上,重连/顶替会起新连接、桶随之重置;v1 接受这个漏洞(0033 决策 3)。

## 私聊(shell 路由,不进 GameLoop)

一句话:私信发出即落库(=未读),在线的话再叠一次实时投递;失败回执分「对端不存在」和「你自己发错了」两类。

已落地的四批改动:「发」[changes/0038](refactor/changes/0038-dm-send-deliver.md)、「读」游标 [changes/0039](refactor/changes/0039-dm-read-cursor.md)、登录补收 [changes/0040](refactor/changes/0040-dm-login-catchup.md)、保留清理 [0041](refactor/changes/0041-dm-retention-cleanup.md)。

**报文与入口**

- `DirectMessage{to_nick, text}` → shell 路由 [`route_direct_message`](../app/shell/messaging.py) 直接处理;不投 `inbox`,由 Receiver 拦截,同 `FetchRoomChat`。

**路由**(`conns.get(to_nick)`,见 [connection.md](connection.md))

- 第一步:无论对方在线与否,先 `put(DMWrite)` 必落库。这是一条事件写(每条各存一行),`dedupe_key = msg_id` 保幂等;落库即计未读。
- 第二步:对方在线才 `enqueue(对方连接, DMDelivered{msg_id, from_nick, text, created_at})`;离线只落库,等登录补收(见下)。
- 实时投递尽力而为:对方 `outbound` 满就丢这次投递 + WARNING,消息已落库、不会丢。此处不 drop 收件人连接,drop 是 GameLoop / 收件人自身背压的职责。

**失败回执二分**(0038)

| 情况 | 回什么 |
|---|---|
| 对端不存在(`to_nick` 无 DB 行) | `DMUndelivered{to_nick}`,它是投递结果,供前端标失败,不落库 |
| 空 / 超长 / 发给自己 / 限速 | `ErrorMessage`,码与四种情况一一对应:空 → `INVALID_MESSAGE`,超长 → `MESSAGE_TOO_LONG`,发给自己 → `CANNOT_DM_SELF`,限速 → `RATE_LIMITED` |

这条回执与 [0033](refactor/changes/0033-room-chat-text-guard.md) 走同一通道。

**身份**

- 发件人 = 连接绑定的 nick,不信报文自报;禁止发给自己。
- `msg_id = uuid4().hex`,由 shell 生成,免得同一微秒撞键;`created_at` = shell 墙钟。
- v1 发件人成功路径零回包:本地乐观渲染,送达确认走 `DMRead` 已读回执。

**防护序**(同 0033)

- 顺序是空 → 超长 → 自发 → 限速 → 读 DB 解析 uid:前四步廉价,不耗令牌、不读 DB,所以先拒;过了每连接 `dm_bucket` 才做最贵的读 DB 解析 uid。
- 阈值 `DM_MAX_TEXT_LEN`/`DM_RATE_BURST`/`DM_RATE_PER_SEC` 进 [config.md](config.md)(0042 env 化)。

**第二个 outbound 生产者**

- 私聊路由和 GameLoop 都 `put_nowait` 到 `outbound`,单线程 asyncio 下安全;代价是私聊与游戏消息之间不保证相对顺序,对聊天可接受。
- 仍然只经 `outbound` → Sender,不旁路 `ws.send`(守不变量 4/6)。

## 表情(emoji,见 [changes/0034](refactor/changes/0034-emoji-catalog-design.md))

一句话:表情就是文本里的 `[code]` 标记,后端当普通文本透传,前端查目录渲染成字形——前端渲染约定 + 一份共享目录,后端几乎零改动,适用所有聊天面(房聊现、私聊将来)。

**格式 `[code]`**

- 消息文本里用定界括号引用;`code` 是稳定 ASCII snake_case 键,如 `[smile]`/`[poker_face]`/`[all_in]`,显示名(label)可中文。

**后端纯透传**

- `ChatMessage.text`(及未来 DM)字段不变,`[code]` 当普通文本流转,reduce 不校验、不转换,无新增 wire 字段 / 消息。
- 前端把已知 code 换成字形(Unicode 表情或自定义贴纸);未知 `[foo]` 原样显示为文本,不因含方括号就拒收。

**目录单一事实源,codegen 到 TS**(已落地 [0035](refactor/changes/0035-emoji-implementation.md))

- 后端封闭目录在 [app/wire/emoji.py](../app/wire/emoji.py):`EmojiCode` 枚举 + `EMOJI_CATALOG{label,glyph}`;`gen_wire_ts.py` 吐 `EmojiCode`/`EmojiMeta`/`EMOJI_CATALOG` 进 `wire.gen.ts`,前端只消费,不手写第二份。
- 前端 [utils/emoji.ts](../../frontend/src/utils/emoji.ts) 的 `tokenizeChat`/`chatToPlainText` 据目录渲染,可按 code 覆盖为贴纸图。

**边界(v1)**

- `[code]` 计入 `ROOM_CHAT_MAX_TEXT_LEN`;字面量转义(如 `\[smile]`)、服务端 code 校验 / 统计、富文本 / @提及均为后续(见「待定」)。

## presence(在线判定,本文只用到一点)

一句话:本文只需要「在不在线」这一个布尔值,即 `conns.get(to_nick) is not None`,用 ConnectionManager 的 nick 表。

完整 presence(谁在线 / 在哪房 / 什么状态)是更大的只读视图,单列或后续并入。

## 脱敏与隐私

一句话:聊天正文不能带牌局隐私,而且默认不写日志。

- 聊天正文不得携带游戏隐私(底牌 `hole_cards` / 牌堆 `deck`),并入 [log.md](log.md) 红线。
- 决策(可改)· 日志不记正文:只记元数据,即 `from_nick`→`to_nick`/`room`、长度、是否投达。要审计违规内容再单开。

## 持久化与离线送达

一句话:房聊只在内存留最近 N 条(不落库),私信落库做「未读收件箱 + 完整已读回执」。

判据同「分两路」:房聊是「此刻在场」的同步消息,离线即错过;私信点对点、天然异步,离线也得收到。

### 房聊:房内内存环形历史(不持久化;0036 落地,[0071](refactor/changes/0071-room-chat-history-in-room.md) 迁入 `Room.chat_history` 随房生灭)

**存哪**

- `Room.chat_history`,类型 `deque(maxlen=…)`,定义在 [domain.py](../app/core/domain.py);生命周期与房同步,房销毁则历史消亡,同名新房是全新历史。
- 0071 这么改是为修两个问题:跨「房间世代」的历史泄露、按房名无界增长;用户定案是「进 Room」而非用 shell 钩子。
- 上限经 `RoomCreate.chat_history_size` 传入,由 shell 从 `ROOM_CHAT_HISTORY_SIZE` 盖入(core 不 import config)。
- 追加发生在 reduce `_room_chat`,经工作副本写入,world 本体只由 commit 改;次序由 GameLoop 串行保证。
- 不落库。纯展示数据,规则不读;「每命令深拷 ≤N 条消息」的代价已记档接受。

**看历史(新进房 / 重连)**

- 客户端发 `FetchRoomChat{room}`(ws,带房名),Receiver 直接处理,不进 GameLoop。
- 读 committed world 的 `world.rooms.get(room).chat_history`,沿用 presence 同款只读豁免:只读、展示用、容忍滞后;取 `tuple` 快照,单线程 asyncio 下不会读到撕裂的中间态。
- enqueue `RoomChatHistory{room, messages}` 到该连接 `outbound`,走直发路径,不是 `Personal` 事件。
- 决策(可改):拉取式比「`StateSnapshot` 自动附带」更解耦。

**v1 不校验成员资格**

跨房拉历史可接受:房聊是公开非敏感态,privacy 红线只护 `hole_cards`/`deck`;规模 ≤20 人、内网;拉取者本来就能进该房去看。严格校验需走 reduce,本批不做,见 [changes/0036](refactor/changes/0036-room-chat-history.md) 决策 5。

**容量 / 崩溃**

- `ROOM_CHAT_HISTORY_SIZE` 进 [config.md](config.md),默认 50,0042 env 化。
- 进程崩 → 历史全丢;房聊本就临时,接受,见 [storage.md](storage.md) 崩溃语义。

### 私信:落库的「未读收件箱 + 完整已读回执」

**落库触发是「未读」,不是「离线」**

- 每条私信发出即落库,一条落库 = 一条未读;收件人读了回 `DMMarkRead` → 推进游标(「我读到哪儿了」的时间水位)+ 回执发件人,已读满保留期才清。
- 于是「在线收到但没读就下线」的消息也不丢——这是与「离线收件箱」的关键差别。

**决策(可改)· DM 以 DB 为权威**

- 不套用游戏状态的「内存权威」:私信不参与实时裁定,还要跨会话 / 离线存活,所以像手牌记录一样 DB 权威;在线直投只是叠加其上的实时投递优化。

投递与落库(全在 shell 路由,不进 GameLoop):

| 时机 | shell 动作 |
|---|---|
| **发**([0038](refactor/changes/0038-dm-send-deliver.md))`DirectMessage{to_nick, text}` | `from/to nick→uid`(`load_uids_by_nicks`)→ 生成 `msg_id=uuid4` → `put(DMWrite)` → 若在线再 `enqueue(DMDelivered)` |
| **读**([0039](refactor/changes/0039-dm-read-cursor.md))`DMMarkRead{peer_nick, read_through}` | `reader/peer nick→uid` → `put(DMReadCursorWrite)` 推进「我读 ta 的进度」→ 发件人在线则 `enqueue(DMRead)` 回执 |
| **登录补收**([0040](refactor/changes/0040-dm-login-catchup.md)) | (重)连时 [`deliver_dm_catch_up`](../app/shell/messaging.py) 读 DB,拼出 `DMDelivered` 列表 + `DMRead` 回执列表,enqueue 本连接 outbound |

表格三行的细节:

- 发:对端不存在 → `DMUndelivered`。`put(DMWrite)` 是事件写,必落、算未读;`enqueue(DMDelivered)` 尽力而为。
- 读:peer 不存在 → `INVALID_MESSAGE`;peer 就是自己 → `CANNOT_DM_SELF`。`put(DMReadCursorWrite)` 是状态写,`_state_key` 按 `(reader,peer)` 覆盖、只留最新,OrmPersister 用 UPSERT 落库;`enqueue(DMRead)` 回执尽力而为。
- 登录补收:`load_unread_dms` 取 `created_at > 游标` 的行(没有游标就全取),旧→新排序;`load_read_receipts` 取游标表里 peer=自己的行。全程不进 GameLoop、不读 world。best-effort:DB 失败或 outbound 满就跳过,下次重连再补,游标没推进所以不丢。客户端按 `msg_id` 去重;未读数 = 列表长度,没有单独的「数」报文。

**写缓冲的第二个生产者(新不变量)**

- 私信路由把 `put(DMWrite)`/`put(DMReadCursorWrite)` 放进写缓冲;`put` 同步、无 `await`(见 [db.md](db.md)),单线程 asyncio 下不会与 GameLoop.dispatch 的 put 交错。
- 唯一写库者仍是 PersistWriter:绝不在路由里 `await commit`,否则出现第二个 DB 写者,破 [db.md](db.md) 不变量 5。

**键用不可变 `uid` 不用 `nick`**

- 落库与游标都按 `User.id`,同 [db.md](db.md) 的 `PointsWrite.uid`;原因是 nick 可改名,见 [presence.md](presence.md)。
- wire 上仍用 nick(显示用),在收发边界做 nick↔uid 转换。

**游标表一表两用**

- 未读 = `created_at > dm_read_cursor[reader=我, peer=对方].read_through_ts` 的行,未读总数是跨对端求和。
- 发件人的已读回执 = 查 `dm_read_cursor where peer=我`,无需另存一份。

**时间游标而非自增 id**

- 用 shell 盖的墙钟 `created_at` 排序 / 比较,同 [db.md](db.md)「墙钟由 shell 盖」,避开自增 id 跨重启不单调的坑。
- `msg_id` 只作 `DMWrite` 的 `dedupe_key` + wire 引用。

**游标只前进,且不超过服务器此刻**([0098](refactor/changes/0098-read-cursors-only-move-forward.md))

`read_through` 是客户端回传的,两头都要守——它一表三用,写歪一处三处全歪。

| 方向 | 守在哪 | 不守会怎样 |
|---|---|---|
| 不许回拨 | `OrmPersister._upsert_dm_cursor`:旧值已在手、唯一写者、同一事务 ⇒ race-free | 已读私信重新变未读被重推;对面看到已读退回未读;本可删的行赖着不走 |
| 不许指向未来 | `route_dm_mark_read`:钳到 shell 此刻 | 「什么都读过了」——此后到达的私信永不进登录补收,过保留期还会被 `cleanup_dms` 当「已读且过期」真删掉 |

上界钳在路由层是因为**只有 shell 有墙钟**;下界钳在写层是因为路由读到的旧值可能已被写缓冲超越(delayDB 异步追平)。
比较一律经 `db/dm_records.as_utc`:游标列是 `DateTime(timezone=True)`,pg 带 tz 回来而 **sqlite 读回丢 tz**,naive 与 aware 直接比会 `TypeError` —— 落在唯一写者里就是整批状态写被毒死、回灌重试永不成功。

**保留期**([0041](refactor/changes/0041-dm-retention-cleanup.md))

- 未读保留直到被读;已读后再留 `DM_READ_RETENTION_SECONDS`,默认 7 天,进 [config.md](config.md)。
- 清理归唯一写者:`PersistWriter.maybe_cleanup` 按 `DM_CLEANUP_INTERVAL_SECONDS` 周期调 `OrmPersister.cleanup_dms`,不另起协程写库。
- `DELETE` 条件两条同时成立:收件人游标 `read_through_ts >= created_at`(即已读),且 `created_at < now - 保留期`。
- best-effort:失败就 ERROR + 跳过,幂等,下周期重删。

**崩溃 / 竞态(接受)**

1. 发后未 flush 即崩 → 丢最近几条,同手牌记录。
2. 极小窗:A 发给离线 B,append 还在缓冲未落库时 B 恰好登录读 DB → 本轮漏但不丢;下个 flush 进 DB,下次拉取或 A 在线时可见。
3. **同一 flush 窗内的游标回拨仍会生效**(0098 记档,不修):状态写在 `WriteBuffer` 里按键**后写覆盖**,所以同窗内先标读 T2、再标读 T1,进库的是 T1——单调守卫在唯一写者处,它只看得到「库里的旧值」,看不到被覆盖掉的 T2。
   **后果是保守的**:游标偏小只会让 T1..T2 那几条在下次补收时重发一遍,**绝不会**误删(`cleanup_dms` 要求 `游标 >= created_at`,游标低 = 更不敢删)。窗口至多一个 flush 周期,且正常客户端本就单调上报。
   不修的理由:要堵它得让路由去读写缓冲里的待落值,等于把「只前进」这条规则复制到第二处;或者给通用的 `WriteBuffer` 加按类型的合并逻辑,破坏它「状态写一律后写覆盖」的单一语义。两者都比这个保守窗口更糟。

要消掉第 2 条窗口可以加「内存未读镜像」,见待定。

**隐私**

- 正文落库(持久化的本意),但 [log.md](log.md) 脱敏红线不变:不写日志,正文不得带 `hole_cards`/`deck`。

## 与架构契约(必须守住)

1. 房聊走 reduce(产出 `Broadcast`);私聊走 shell 路由(不进 reduce/world)。判据:要不要房间成员名单(world)。
2. 身份一律取连接绑定的 nick,绝不信报文自报的发送者(见 [auth.md](auth.md))。
3. 私聊是 `outbound` 的第二生产者:`put_nowait` 安全,与游戏消息顺序不保证;仍只经 Sender,不旁路 `ws.send`。
4. 限速在 shell(进 reduce / 进路由之前),防刷屏拖累 GameLoop。
5. 正文不含游戏隐私;默认不把聊天正文写日志(私信落库不等于可写日志)。
6. 私信落库走「未读收件箱」:发即 `put(DMWrite)`、读 `put(DMReadCursorWrite)` 推进游标、已读满保留期由 PersistWriter 清;路由内绝不 `await commit`,唯一写库者仍 PersistWriter。
7. 房聊只在内存留最近 N 条(`Room.chat_history` 环形,随房生灭,不落库;0071),`RoomChat` reduce 除追加历史外不改任何游戏状态;新进房/重连靠 `FetchRoomChat` 拉。
8. 持久化键用 `uid` 不用 `nick`;保留期 / 容量 / 限速一律配置化(见 [config.md](config.md))。
9. 表情是前端渲染约定 + codegen 共享目录(`[code]`),后端透传,wire 字段不变(见 [changes/0034](refactor/changes/0034-emoji-catalog-design.md))。

## 待定 / future

- **仅好友可私信 + 屏蔽 / 黑名单**:当前无好友表,私信 = 任意 `nick→nick`,对端存在即可。好友校验、按发件人过滤都放在 shell 路由 —— future。
- **内存未读镜像**:把私信未读也做成「shell 内存权威 + delayDB」(delayDB = 内存说了算、DB 异步跟上的写法),可消掉上文的 flush 窗口竞态,登录补收也免读 DB。本规模非必需。
- **全量聊天记录**:若日后要双方翻历史,把私信从「已读即删」改成「不删 + 拉历史接口」,分页游标同 [rest.md](rest.md) 查手牌。
- **系统公告 / 大厅群发**:`LobbyBroadcast`,发给所有大厅连接,接 [lobby.md](lobby.md) 的待定。
- **表情后续**:转义字面量 `[code]`、服务端 code 校验、富文本 / @提及。协议加字段即可,见 [wire.md](wire.md) 加性演进。
- **完整 presence 模块**:见 [presence.md](presence.md),供大厅、好友、私聊共用。
