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
- **reduce**:校验发送者在房内(`nick in world.users`)、`text` 非空且 ≤ 上限;**不改任何游戏状态**;产出 `Broadcast(room, ChatMessage{from_nick, text})`。
- **派发**:dispatch 按 `world.rooms[room].users_in_room` 发给全房(含观战者),OFFLINE 跳过(见 [connection.md](connection.md))。
- **读写性质**:房聊是**只读命令**(不改 world),可走 [storage.md](storage.md) 的 `uRead` 只读路径、免深拷;即便走默认深拷也无害(产出 events、commit 一个内容相同的副本),本规模随意。
- **顺序**:经 GameLoop 串行,所以房聊与牌局事件**有确定全局顺序**,各客户端看到的次序一致(房聊走 reduce 的额外好处)。
- **限速**:在 **shell**(Receiver 收到 `RoomChat` 先过令牌桶,超了直接丢 + 回 `Err`),**不让刷屏占 GameLoop**;阈值进 [config.md](config.md)。

## 私聊(shell 路由,不进 GameLoop)

- **报文** `DirectMessage{to_nick, text}` → **Receiver(shell)直接处理,不投 `inbox`**。
- **路由**:`conns.get(to_nick)`(见 [connection.md](connection.md) 的全局 nick 表):
  - **在线** → `enqueue(对方连接, DMDelivered{from_nick, text})`,并可回发件人一个回执。
  - **离线** → **决策(可改)**:v1 直接丢 + 回 `DMUndelivered{to_nick}`;要离线投递/历史再加 delayDB 收件箱(登录后拉,见「待定」)。
- **身份**:发件人 = **连接绑定的 nick**(不信报文自报);收件人 = `to_nick`;禁止发给自己;`to_nick` 不存在则回 `Err`。
- **第二个 outbound 生产者**:私聊路由和 GameLoop 都 `put_nowait` 到 `outbound`——单线程 asyncio 下安全;私聊与游戏消息之间**不保证相对顺序**(对聊天无所谓,可接受)。**仍只经 `outbound` → Sender**,不旁路 `ws.send`(守不变量 4/6)。
- **限速**:同样在 shell(发件人维度令牌桶),进配置。

## presence(在线判定,本文只用到一点)

私聊判断"对方在不在线" = `conns.get(to_nick) is not None`——就是 ConnectionManager 的 nick 表(presence)。完整 presence(谁在线/在哪房/什么状态,供大厅人数、好友在线提醒)是更大的只读视图,**单列或后续并入**,本文只用"在不在线"这一点。

## 脱敏与隐私

- 聊天正文**不得携带游戏隐私**(底牌 `hole_cards` / 牌堆 `deck`)——这是文本消息,不该出现这些字段(并入 [log.md](log.md) 红线)。
- **决策(可改)· 日志不记正文**:只记元数据(`from_nick`→`to_nick`/`room`、长度、是否投达),不记聊天内容本身(隐私 + 噪音)。要审计违规内容再单开。

## 与架构契约(必须守住)

1. **房聊走 reduce**(产出 `Broadcast`、不改游戏状态);**私聊走 shell 路由**(不进 reduce/world)。判据:要不要房间成员名单(world)。
2. **身份一律取连接绑定的 nick**,绝不信报文自报的发送者(见 [auth.md](auth.md))。
3. **私聊是 `outbound` 的第二生产者**:`put_nowait` 安全,与游戏消息顺序不保证(可接受);仍只经 Sender,不旁路 `ws.send`。
4. **限速在 shell**(进 reduce / 进路由之前),防刷屏拖累 GameLoop。
5. **正文不含游戏隐私**;默认不把聊天正文写日志。

## 待定 / future

- **持久化**:房聊历史 / 私聊离线收件箱——默认 ephemeral(不存);要存走 [db.md](db.md) 的**事件写**(追加),登录/进房时拉最近 N 条。
- **屏蔽 / 黑名单**:拒收某人私聊;按发件人维度在 shell 路由处过滤。
- **新进房看历史**:`StateSnapshot` 默认**不含**聊天(ephemeral);要带最近几条需一个 world 内或 shell 的环形缓冲(决策,future)。
- **系统公告 / 大厅群发**:`LobbyBroadcast`(发给所有大厅连接),接 [lobby.md](lobby.md) 的待定。
- **富文本 / @提及 / 回执 / 表情**:协议加字段即可(见 [wire.md](wire.md) 加性演进),本文不展开。
- **完整 presence 模块**:谁在线/在哪房/状态的只读 API,供大厅、好友、私聊共用。
