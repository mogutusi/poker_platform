# 连接管理与 shell 装配(connection / shell)

## 一句话定位

shell 是 `world` 之外的全部:并发、IO、生命周期。

本文讲三件事:连接的登记/路由/顶替/清理(ConnectionManager)、事件从 GameLoop 到物理落点的派发(dispatch)、进程启动与优雅关闭(lifespan)。

相关文档:core 视角见 [core.md](core.md),并发模型与不变量见 [architecture.md](architecture.md),加密信道见 [auth.md](auth.md),计时见 [timer.md](timer.md),落库见 [db.md](db.md)。

> 前置:先读 [architecture.md](architecture.md) 的「协程构成」「连接生命周期」「并发不变量」。

## shell 组件全景

| 组件 | 数量 | 持有 | 职责 |
|---|---|---|---|
| **GameLoop** | 1 | `world` / `inbox` / `dispatcher` | 唯一状态写者:取命令 → 工作副本 reduce → commit → 交 `Dispatcher` 派发事件、回发错误。工作副本 = reduce 期间改的临时状态,commit 时才换上去 |
| **Dispatcher** | 1 | `world`(只读)/ `conns` / `persist` / `timer` / `inbox` | commit 后同步派发,四类去向:`Broadcast`/`Personal` 入 `outbound`;`Persist` 入写缓冲;`TurnChanged`/`ClearAction` 调 Timer;错误按 `origin` 回发。见「dispatch」 |
| **ConnectionManager**(`conns`) | 1 | `nick → Connection`(全局) | 只管 nick 与物理连接的登记/查找/顶替;房间成员由 `world` 给出,见「广播成员」 |
| **Receiver** | 每连接 1 | 一个 `Connection` | FastAPI 的 ws handler:握手鉴权 → 登记 → 收帧验解 → `Command` 投 `inbox` → 退出清理 |
| **Sender** | 每连接 1 | 同一 `Connection` | 从 `outbound` 取 `ServerMessage`,加密成帧后 `ws.send`;保证单连接严格保序,并把慢客户端隔离在自己这一条连接上 |
| **PersistWriter** | 1 | 写缓冲 + 自己的 DB session | delayDB(写不立刻落库,先进写缓冲)周期批量落库,见 [db.md](db.md) |
| **Timer** | 1 | 两张到期表 | 行动超时、占座清理,到点投命令,见 [timer.md](timer.md) |

## 三个关键结构

```python
@dataclass
class SecureChannel:                 # auth.md 的逐帧加密状态;挂 Session(逐会话,不逐连接),不进 world
    enc_key: bytes                   # KDF_sm3(session_token + \x01) —— SM4
    mac_key: bytes                   # KDF_sm3(session_token + \x02) —— HMAC-SM3
    in_seq: int = 0                  # 入站已见最大序号:严格递增,按会话计,跨重连挡重放(0058/0061)
    out_seq: int = 0                 # 出站递增序号,按会话计

@dataclass
class Connection:                    # 一条物理 ws 的全部 shell 状态;连接绑 nick,不绑房间
    nick: str                        # 握手时由会话定;一个 nick 全局一条连接
    session_id: str                  # 会话句柄(公开 selector):审计/日志关联,加密路查会话
    ws: WebSocket
    outbound: asyncio.Queue          # 有界,满 = 慢客户端(见「队列满」);装明文 ServerMessage,由 Sender 加密
    channel: SecureChannel | None    # 引用会话的信道;None = 明文 dev 帧(?nick=),非 None = 加密帧(?sid=,0061)
    session: Session | None          # 所属会话引用;收/发帧前比对 expires_at 做 exp 兜底(0070);dev 明文为 None
    sender_task: asyncio.Task | None = None
    receiver_task: asyncio.Task | None = None   # 本连接的 ws handler 自身;慢客户端被丢弃时据此终结它(0083)
    # 注:用户在哪个房间是 world 状态(world.users[nick].room),不是连接字段。
    # 注:seq/密钥挂 Session,channel 只是引用;顶替/重连复用同一会话信道,seq 连续。

class ConnectionManager:
    def __init__(self) -> None:
        self._by_nick: dict[str, Connection] = {}   # nick -> Connection(全局,与房间无关)

    # —— 登记/注销(Receiver 调用),返回被顶掉的旧连接 ——
    def register(self, conn: Connection) -> Connection | None:
        old = self._by_nick.get(conn.nick)   # 同 nick 已有连接 = 旧连接,被顶替
        self._by_nick[conn.nick] = conn
        return old

    def unregister(self, conn: Connection) -> None:
        if self._by_nick.get(conn.nick) is conn:   # 仅当登记的就是 conn 本人才删,防顶替后误删新连接
            del self._by_nick[conn.nick]

    def is_current(self, conn: Connection) -> bool:    # 退出时判断「我还是不是当前连接」
        return self._by_nick.get(conn.nick) is conn

    # —— 路由(GameLoop.dispatch 调用)——
    def get(self, nick: str) -> Connection | None:     # Personal / 私聊 / 错误回发,全按 nick
        return self._by_nick.get(nick)
```

**连接绑 nick、不绑房间**

握手只认会话身份;「在哪个房」是 `world.users[nick].room`,由 `JoinRoom`/`LeaveRoom` 修改(见 [lobby.md](lobby.md))。ConnectionManager 全局按 nick 建键,私聊、presence 都是 O(1) 查找,不需要房间索引。

**加解密封装在 `SecureChannel`,挂 Session、不进 `world`**

- Receiver 收帧按「验 MAC → 解密 → 验 seq」处理(见 [auth.md](auth.md),0058/0061),Sender 发帧时加密;`outbound` 里一律是明文 `ServerMessage`,core/dispatch 全程不知有加密。
- `enc_key`/`session_id`/`ws` 这类非确定外部状态不进 core,与 [timer.md](timer.md) 的「时间戳只活在 shell」同理。

**ws 加密接线现状**

已落地([changes/0061](refactor/changes/0061-p5-ws-secure-channel-wiring.md)),按 `Connection.channel` 分流:

| `channel` | 端点 | 收发 |
|---|---|---|
| None | `?nick=`(明文 dev 帧) | 收发明文 JSON |
| 非 None | `?sid=`(加密帧) | Sender `seal` 出二进制;Receiver `receive_bytes` → `channel.open` → `parse` |

- 信道逐会话、跨重连复用、seq 连续,细节见 [auth.md](auth.md);每连接限速桶 `chat_bucket` / `dm_bucket`(`TokenBucket`)随房聊 [0033](refactor/changes/0033-room-chat-text-guard.md)、私聊 [0038](refactor/changes/0038-dm-send-deliver.md) 加入。
- 其他相关落地:REST 信封 [0062](refactor/changes/0062-p5-rest-envelope-user-me.md);登录重放守卫 [0063](refactor/changes/0063-p5-login-replay-guard.md);K_user 双钥轮换 [0066](refactor/changes/0066-p5-kuser-rotation.md),该轮换不动会话密钥。
- P5 已全部落地。仅剩一项:前端切加密后退役明文 `?nick=` 端点。

## 广播成员 = world 房间成员,按 nick 解析连接

`Broadcast(room)` 发给谁,以 `world.rooms[room].users_in_room` 为准。这是逻辑成员表;拿到 nick 后再到 ConnectionManager 取连接,无连接者跳过(循环见下节 dispatch 伪码)。

- OFFLINE 玩家在 `users_in_room` 里(座位保留)但无连接,跳过,重连时由 `StateSnapshot` 补齐;观战者(`JoinRoom` 后 `WATCHING`)也在表里,照收公开广播。
- dispatch 在 GameLoop 内、commit 之后读 `world`,同协程,安全;隐私由 core 在事件层把关,他人底牌不进 `Broadcast`(见 [core.md](core.md) 不变量 3)。

> 大厅用户不在任何 `users_in_room`,收不到房间广播;给大厅推送是另一条路 `LobbyBroadcast`(见 [lobby.md](lobby.md) 待定)。

## dispatch:事件 → 物理落点

事件派发是独立的 `Dispatcher`:GameLoop 成功 commit 后对每个 event 调 `dispatcher.dispatch(ev)`,错误回发走 `dispatcher.send_error(cmd, err)`;派发同步——只 `put_nowait` 或调本地快设施,不 `await`,守不变量 3。

```python
class Dispatcher:                                        # 持 world(只读)/conns/persist/timer/inbox
    def dispatch(self, ev: Event) -> None:
        match ev:
            case Broadcast(room=r, msg=m):
                room = self.world.rooms.get(r)           # reduce 可能刚销毁该房(最后一人离开)
                if room is None:
                    return                               # 房已销毁,无人可广播,跳过
                for nick in room.users_in_room:          # 逻辑成员 → 按 nick 取连接
                    if (c := self.conns.get(nick)) is not None:
                        self._enqueue(c, m)
            case Personal(nick=n, msg=m):                # 底牌 / StateSnapshot / 离开者回执,按 nick 私发
                if (c := self.conns.get(n)) is not None:
                    self._enqueue(c, m)
            case Persist(payload=p):
                if isinstance(p, HandRecordWrite) and p.end_time is None:
                    p = replace(p, end_time=self._now())  # end_time 由 shell 派发时盖墙钟,core 不读钟(见 db.md)
                self.persist.put(p)                      # 写缓冲单入口,内部 _state_key 分流,见 db.md
            case TurnChanged(room=r, acting_nick=n, epoch=e):   # 字段序同 events.py
                self.timer.on_turn_changed(r, n, e)      # B 组:同步调 Timer;时长由 Timer 读 gameconfig.ACTION_TIMEOUT,不随事件带
            case ClearAction(room=r):
                self.timer.clear_action(r)

    def _enqueue(self, conn: Connection, msg) -> None:
        try:
            conn.outbound.put_nowait(msg)
        except asyncio.QueueFull:                        # ≤20 人正常不会满;满 = 该连接 Sender 卡死
            log.warning("slow client dropped nick=%s", conn.nick)
            self._drop_connection(conn)                  # 摘键 + 终结其 Sender/Receiver + 投 Disconnect(inbox 满则丢 + CRITICAL);重连靠 StateSnapshot 补回
```

路由全按 nick,因为连接按 nick 全局唯一:

- `Personal` 只带 `nick`,不带 room;私聊也走 `conns.get(对方 nick)`,用的是同一张表(见 [messaging.md](messaging.md))。
- 错误回发 `send_error(cmd, err)` 用 `conns.get(cmd.origin)` 找发起连接(见 [error.md](error.md));`origin=None` 的系统命令无连接可回发,只落 `log.warning`。

> 给离开者本人的确认必须由 reduce 产 `Personal(nick=离开者, UserLeft)`。原因:`LeaveRoom`/`Cleanup` 在同一条 reduce 里已把离开者移出 `users_in_room`,房空还会销毁该房,所以 `Broadcast(room, UserLeft)` 只到得了留下的人。房已销毁时 `Broadcast` 靠 `rooms.get` 容错跳过。

## 连接生命周期(一条 Receiver 的一生)

```
握手鉴权(绑 nick) → 登记(可能顶替) → 起 Sender → 投 Connect → 收帧循环(含 JoinRoom/LeaveRoom) → 退出清理
```

### 1. 握手鉴权

详见 [auth.md](auth.md),加密路落地见 [0061](refactor/changes/0061-p5-ws-secure-channel-wiring.md)。

客户端 `ws connect ?sid=<session_id>`,不带 room_id;`SessionStore.lookup` 得 `nickname`/`token`,查不到或过期 → ws 关闭码 4401 拒掉,不建 `Connection`。通过后 get-or-derive 会话 `SecureChannel`,建 `Connection(channel=…)`;第一帧 MAC 验过即证明持有 token,伪造/重放首帧 → `FrameError` → 关连接。dev 明文路 `?nick=`(无信道)并存。

`sid` 是公开句柄,嗅探者拿它只能连上顶替、搞 DoS 式干扰:无 token 就造不出合法帧、也读不了密文,故在威胁模型外。「首帧验证前不登记」是后续硬化项(见 0061)。

### 2. 登记

建 `Connection(nick=…)` → `old = conns.register(conn)`。`old` 非空即顶替(见「顶替语义」):关 `old.ws`、cancel `old.sender_task`,不投 `Disconnect`。

**关旧 ws 是一个很宽的 await 窗口,窗后必须复查「我还是当前连接吗」**(0083)。窗内自己可能已被第三条连接顶掉——顶替恰恰常发生在旧 socket 假死时,而关一条假死的 socket 要等 close 超时,窗口宽达十几秒。不复查就会:拆掉别人刚装的占座清理表,再投一条 `Connect` 把已 `OFFLINE` 的用户复活成在线;而 `Cleanup` 只回收 `OFFLINE` 座位,于是座位与桌上筹码永久泄漏。复查不通过就地返回:不起 Sender、不拆表、不投 `Connect`,静默退出(同顶替语义)。

### 3. 起 Sender

`conn.sender_task = create_task(sender_loop(conn))`。

### 4. 接入(进的是大厅,不是房间)

投 `Connect(nick)`。reduce 按 `world` 真相分三类(`_connect`,0022 起、0031 补顶替臂):

- **纯大厅**(`nick` 不在 `world.users`):core 无事可做。进房和载入积分走 `JoinRoom`。
- **在房 + `OFFLINE`** = 重连:恢复在线 + `Broadcast(UserStatusChanged)` + 私发 `Personal(StateSnapshot)` 对齐所在房。
- **在房 + 在线** = 顶替再连:旧连接被静默关闭、未投 `Disconnect`,所以 `world` 仍记其在线。

重连恢复到哪个状态,按 world 推断(不存断线前状态),三种情况都是合法的 `OFFLINE→*` 转移:

| world 情形 | 恢复到 |
|---|---|
| 在进行中手牌 | `PLAYING` |
| 有座不在手 | `SITTING_IN`(需重新 ready) |
| 无座 | `WATCHING` |

顶替再连的处理:只私发 `Personal(StateSnapshot)`,状态未变不广播——对房内他人无信息变化,用户无感(见「会话过期与密钥轮换」)。

> reduce 不感知「连接」,分不清「顶替再连」与「同一连接重复 `Connect`」;但 **Receiver 每条连接只投一次 `Connect`**(本步的投递),故对**已在房在线** nick 的第二次 `Connect` 必来自新 ws(= 顶替)。正确性也不靠「证明这是顶替」,而靠快照无害可重发:只读、隐私逐收件人(见 [core.md](core.md) `StateSnapshot`)、幂等安全。

积分不在 `Connect` 载入,等 `JoinRoom`(见 [lobby.md](lobby.md) / [user.md](user.md))。

### 5. 收帧循环

单帧流程:`while: 收帧 → [会话 exp 检查(0070)] → 验+解 → ClientMessage → Command(盖 origin=nick)→ inbox.put`。

- 命令不带 room,房间由 `world.users[nick].room` 推定;协议/解析错误直接构 `ErrorMessage` 投本连接 `outbound`。
- 加密连接收/发帧前各比对一次会话 `expires_at`,过期关连接(4401);dev 明文无会话,不查。
- 不做每帧续命。占座窗口改为断线装表(0070,见 [timer.md](timer.md))。

`JoinRoom` 是例外,因为报文只带 `room`:

1. Receiver 先过载入屏障 = `inbox.join()` + `PersistWriter.barrier()`,让刚离房的退分先落库、DB 追平。失败回 `INTERNAL`(0073,见 [storage.md](storage.md)「载入屏障」)。
2. 再按连接 nick 读 DB(异步)富化 `uid`/`loaded`,构 `JoinRoom(room, uid, loaded)`。

身份和积分都不信报文([changes/0030](refactor/changes/0030-p4-per-join-wire-load.md))。

### 6. 退出清理(ws 断 / 异常)

1. `conns.unregister(conn)`:只删自己,顶替场景自动跳过。
2. 仅当 `is_current` 为真才 `arm_cleanup(nick)` + 投 `Disconnect(nick)`。被顶替的旧连接 `is_current=False`,静默退出;否则会把刚重连的人误标 OFFLINE。0070 定下的规矩:凡投 `Disconnect` 处必装占座窗口表,`dispatch._drop_connection` 同样适用。
3. 拆表由**新连接**做:它在自己的「1. 握手鉴权」之后、投 `Connect` 之前 `cancel_cleanup(nick)`(不是在本节的 `unregister` 之后)。竞态漏拆由 reduce 的 OFFLINE staleness 兜底(staleness = reduce 进门先查命令是否还新鲜,不新鲜就忽略)。

> **这段清理必须全程无 await。** 它和 `dispatch._drop_connection` 是同一个 nick 上仅有的两处「摘键 + 投 `Disconnect`」;两边都对事件循环原子,所以无论谁先跑,另一边看到的都是「我已经不是当前连接」,`Disconnect` 恰好一份。往这里加任何 await(哪怕只是「礼貌地」关一下 ws)都会重新打开窗口、变成双份(0083)。

**慢客户端被 dispatch 丢弃**用同一套退出语义,但由 dispatch 主动发起:摘键 → cancel 该连接的 Sender 与 Receiver → 装表 + 投 `Disconnect`。只摘键不够——触发条件正是「读慢写健」的非对称慢客户端:它的下行堵着(所以 `outbound` 才会满)、上行却畅通,Receiver 会继续把它的帧变成命令投进 `inbox`,成为一条「已经不存在的连接」仍在驱动状态机的幽灵命令源;而且它重连时 `register` 返回 `old=None`(键早被摘掉),不触发顶替,于是同一个 nick 同时挂着两个 Receiver(0083)。cancel 是同步的,不违反「dispatch 全程不 await」。

reduce 收到 `Disconnect` 分三类:

- **在座者**标 `OFFLINE`、保座——断开 ≠ 离场,这条只限在座者;退筹释座要等断线起 `LIVENESS_TIMEOUT` 满的 `Cleanup`,或用户主动 `LeaveRoom`(见 [lobby.md](lobby.md))。
- **观战者**即时离场(0070):无座无筹码,重进零成本,末人离房则销房;重连后在大厅,需重新 `join_room`。
- **大厅用户**无 world 变化。

## 顶替语义(同 nick 新连接顶掉旧的)

一个 nick 全局只有一条有效连接;新 ws 接管,旧 socket 关闭。理由:重连常发生在旧 socket 假死、尚未被 LIVENESS 判掉时,顶替保证 `nick→outbound` 路由稳定。

正确性全在 `register`/`unregister`/`is_current` 的身份判定里(见「三个关键结构」的代码注释):旧连接被顶或退出时不投 Disconnect、只删自己,所以不会误删新连接、也不会误标 OFFLINE。

## 会话过期与密钥轮换(连接层落点)

密钥定期轮换靠无感重连换钥,不在单条连接里换。流程:

1. 客户端在 `SESSION_TTL` 到期前用缓存的 `K_user` 静默重登,得到新 `session_token` 和新密钥。
2. 新连接顶替接管该 nick。顶替的身份判定保证旧连接静默退出、不投 `Disconnect`,所以用户无感。
3. reduce 私发 `StateSnapshot` 对齐当前房(若在房)。

服务器侧兜底:`session_token.exp` 到点拒会话。两种情况才需手输 `K_user` 重登:进程关闭(`K_user` 丢失)、用户登出。

凭证只有两样:`K_user` + `session_token`。不设 refresh token、无 JWT(0057,身份从解密得出)。语义全貌见 [auth.md](auth.md) 的「会话过期与密钥轮换」。

## lifespan:启动与优雅关闭

由 FastAPI lifespan 编排,启动正序、关闭反序。

**启动**

1. 加载配置(`gameconfig`/`settings`),缺字段即启动失败(见 [config.md](config.md))。
2. 配置日志(见 [log.md](log.md)),再开 DB 连接池。
3. 建空 `World`,此时 `world.rooms` 为空。房间是动态的:随 `JoinRoom` 到不存在的房而建、空则销毁(见 [lobby.md](lobby.md) / [changes/0049](refactor/changes/0049-dynamic-rooms.md))。
4. 建写缓冲 + 起 `PersistWriter`;建 `Timer` + 起 `timer.run()`。
5. 建 `ConnectionManager`、`Dispatcher(world, conns, persist, timer, inbox)`、`GameLoop(world, inbox, dispatcher)` + 起 `gameloop.run()`。
6. 挂载 ws 端点(Receiver),此刻起接受连接。

关于房聊:历史挂 `Room.chat_history`,0071 起无独立 buffer 组件。Receiver 的 `FetchRoomChat` 只读 committed world 直接服务。

**关闭(必须 drain,见 [db.md](db.md))**

1. 停 Receiver:不再接新连接、新命令。
2. 排空 `inbox` + 停 GameLoop:在途命令处理完,不再产生新 `Persist`。
3. PersistWriter 终结 flush:`swap` + 落库直到 `is_empty()`。有限重试,超 `DB_DRAIN_TIMEOUT_MS` 放弃并落 CRITICAL。
4. 关 DB 连接池、cancel 各 Sender。

> 非优雅崩溃丢进行中手牌和未 flush 的积分变更。积分非货币,本规模接受(见 [storage.md](storage.md))。

### dev shell(明文脚手架)

`shell/lifespan.py` 的 `DevShell.setup()` 启动序:async engine(缺省 `sqlite+aiosqlite`)→ `create_all` 建表(dev 引导,无 Alembic;生产用迁移)→ 幂等种子 dev 用户进 DB → 建空 `world` → `OrmPersister` 落库 → 起 GameLoop/Timer/PersistWriter → 挂 `/dev/ws`。见 [changes/0018](refactor/changes/0018-d-dev-shell.md) / [0029](refactor/changes/0029-p4-db-backed-dev-shell.md)。

关闭 `DevShell.stop()` 反序四步(细节见 [0046](refactor/changes/0046-lifespan-drain.md)):cancel Timer + GameLoop → 同步排空 inbox → cancel PersistWriter + `await drain()`(有界,超 `DB_DRAIN_TIMEOUT_MS` → CRITICAL)→ cancel 各 Sender + `engine.dispose()`。

「停 Receiver」在 dev 无显式动作,因为 uvicorn 先撕 ws 再调 lifespan shutdown。

per-join 载入已落地([0030](refactor/changes/0030-p4-per-join-wire-load.md)):dev 用户连接进大厅 → 主动 `join_room{"dev"}` → Receiver 读 DB 富化 → `JoinRoom(room, uid, loaded)`。它退役了 0029 的「预置在房 + 启动整载」。

## 与架构契约(必须守住)

1. ConnectionManager/Timer 的内部表是 shell 私有连接态,不是 `world`;只有 GameLoop 经 reduce commit 改 `world`。
2. 对外发送只经 `Connection.outbound` → Sender:禁止 `create_task(ws.send())` 或在 dispatch 里直接 `ws.send`(守不变量 4/6)。
3. 加解密只在 ws 边界(Receiver 验解、Sender 加密),`outbound` 装明文,core 不知有加密。
4. 顶替/注销/Disconnect 都带连接身份判定(`is`/`is_current`),杜绝旧连接误删新连接、误标 OFFLINE。
5. 连接绑 nick(全局唯一),不绑房间;广播成员 = `world` 房间的 `users_in_room`,按 nick 取连接,无连接者跳过。
6. 队列有界,满即判慢客户端:丢连接(摘键 + 终结其 Sender/Receiver 协程)+ `Disconnect`,绝不阻塞 GameLoop(见 [architecture.md](architecture.md))。
7. 关闭必须 drain(见 [db.md](db.md))。

## 待定 / 未设计

- **大厅 / 房间管理(lobby)**:见 [lobby.md](lobby.md)。已设计:连接模型 2(连接只绑 nick、不绑房间)、`JoinRoom`/`LeaveRoom`、房间列表 REST、静态预置房;动态建房仍待定。
- **私聊 / 房聊(messaging)**:已落地(房聊 0021/0033/0036;私聊发/读游标/登录补收/保留清理 0038-0041),设计见 [messaging.md](messaging.md)。本文只约定路由(`conns.get(nick)`);shell 侧的 DM 路由 / 房聊环形缓冲写读 / 登录补收尚缺本文正式章节(待补)。
- **wire 协议清单**:`ClientMessage`/`ServerMessage` 全集 + `StateSnapshot` 字段已写(`app/wire/client.py`/`server.py` + codegen `wire.gen.ts`),治理见 [wire.md](wire.md)。本文只约定路由,不定报文字段。
- **背压上限取值**:`inbox` / `outbound` 队列大小、慢客户端判定阈值进 [config.md](config.md),具体值实测定。
