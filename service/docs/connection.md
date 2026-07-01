# 连接管理与 shell 装配(connection / shell)

## 一句话定位

**shell 是 `world` 之外的全部:并发、IO、生命周期。** 本文把散在各篇的 shell 落地点收拢成一处——**连接怎么登记/路由/顶替/清理(ConnectionManager)、事件怎么从 GameLoop 派发到物理落点(dispatch)、进程怎么启动与优雅关闭(lifespan)**。core 视角见 [core.md](core.md);并发模型与不变量见 [architecture.md](architecture.md);加密信道见 [auth.md](auth.md);计时见 [timer.md](timer.md);落库见 [db.md](db.md)。

> 前置:读完 [architecture.md](architecture.md) 的「协程构成」「连接生命周期」「并发不变量」再看本篇。

## shell 组件全景

| 组件 | 数量 | 持有 | 职责 |
|---|---|---|---|
| **GameLoop** | 1 | `world` / `inbox` / `dispatcher` | 唯一状态写者:取命令 → 工作副本 reduce → commit → 交 `Dispatcher` 派发事件 / 回发错误 |
| **Dispatcher** | 1 | `world`(只读) / `conns` / `persist` / `timer` / `inbox` / `history` | 事件 → 物理落点(GameLoop commit 后同步调):`Broadcast`/`Personal` 入 `outbound`、`Persist` 入写缓冲、`TurnChanged`/`ClearAction` 调 Timer、错误按 `origin` 回发(见「dispatch」)|
| **ConnectionManager**(`conns`) | 1 | `nick → Connection`(全局) | nick ↔ 物理连接的登记/查找/顶替;房间成员由 `world` 给(见「广播成员」) |
| **Receiver** | 每连接 1 | 一个 `Connection` | FastAPI 的 ws handler:握手鉴权 → 登记 → 收帧验解 → `Command` 投 `inbox` → 退出时清理 |
| **Sender** | 每连接 1 | 同一 `Connection` | 从该连接 `outbound` 取 `ServerMessage` → 加密成帧 → `ws.send`;单连接严格保序、隔离慢客户端 |
| **PersistWriter** | 1 | 写缓冲 + 自己的 DB session | delayDB 周期落库(见 [db.md](db.md)) |
| **Timer** | 1 | 两张到期表 | 行动超时 / 占座清理,到点投命令(见 [timer.md](timer.md)) |

**唯一状态写者是 GameLoop;ConnectionManager/Timer 的内部表是 shell 私有连接态,不是 `world`。** 谁都不在 reduce 外写 `world`。

## 三个关键结构

```python
@dataclass
class SecureChannel:                 # auth.md 的逐帧加密状态;per-connection,绝不进 world
    enc_key: bytes                   # KDF_sm3(token + \x01)  —— SM4
    mac_key: bytes                   # KDF_sm3(token + \x02)  —— HMAC-SM3
    in_seq: int = 0                  # 入站已见最大序号(严格递增,防重放)
    out_seq: int = 0                 # 出站递增序号

@dataclass
class Connection:                    # 一条物理 ws 的全部 shell 状态(连接绑 nick,不绑房间)
    nick: str                        # 握手时由会话定;一个 nick 全局一条连接
    session_id: str                  # 会话句柄(公开),用于审计/日志关联
    ws: WebSocket
    outbound: asyncio.Queue          # 有界;满 = 慢客户端(见下「队列满」)。装明文 ServerMessage,Sender 才加密
    channel: SecureChannel
    sender_task: asyncio.Task | None = None
    # 注:用户"现在在哪个房间"是 world 状态(world.users[nick].room),不是连接字段。

class ConnectionManager:
    def __init__(self) -> None:
        self._by_nick: dict[str, Connection] = {}   # nick -> Connection(全局,房间无关)

    # —— 登记/注销(Receiver 调用),返回被顶掉的旧连接 ——
    def register(self, conn: Connection) -> Connection | None:
        old = self._by_nick.get(conn.nick)   # 同 nick 已有连接 = 旧连接,被顶替
        self._by_nick[conn.nick] = conn
        return old

    def unregister(self, conn: Connection) -> None:
        if self._by_nick.get(conn.nick) is conn:   # 仅当登记的就是 conn 本人才删(防顶替后误删新连接)
            del self._by_nick[conn.nick]

    def is_current(self, conn: Connection) -> bool:    # 退出时判断「我还是不是当前连接」
        return self._by_nick.get(conn.nick) is conn

    # —— 路由(GameLoop.dispatch 调用)——
    def get(self, nick: str) -> Connection | None:     # Personal / 私聊 / 错误回发,全按 nick
        return self._by_nick.get(nick)
```

- **连接绑 nick、不绑房间**:握手只认会话身份(见「连接生命周期」),"在哪个房间"是 `world.users[nick].room`,由 `JoinRoom`/`LeaveRoom` 改(见 [lobby.md](lobby.md))。所以 ConnectionManager 全局按 nick 键——**私聊、presence 都成了 O(1) 的 nick 查找**,无需房间索引。
- **加解密封装在 `SecureChannel`,挂连接、不进 `world`**:Receiver 收帧时按入站规矩验+解(见 [auth.md](auth.md) 的「先验 seq → 验 MAC → 才解密」),Sender 发帧时加密;`outbound` 里一律是**明文 `ServerMessage`**,core/dispatch 全程不知有加密(守分层)。
- **`Connection` 是 shell 状态,不是 `UserState`**:`enc_key`/`session_id`/`ws` 这些非确定外部状态绝不进 core(同 [timer.md](timer.md) 的「时间戳只活在 shell」)。

> **dev 落地 delta(明文脚手架,P5 前)**:上面的 `SecureChannel` + `Connection.channel` 是**国密加密的目标形状**(P5 落地)。**当前 dev `Connection` 无 `channel` 字段**(明文:Sender 直接 `ws.send_text(model_dump_json)`、Receiver 收明文 JSON `parse`),改挂 **`chat_bucket` / `dm_bucket`(`TokenBucket`,每连接限速)**——房聊限速随 [0033](refactor/changes/0033-room-chat-text-guard.md)、私聊限速随 [0038](refactor/changes/0038-dm-send-deliver.md) 加。P5 落地时补 `channel` + 替换 Sender/Receiver 帧编解。

## 广播成员 = world 房间成员,按 nick 解析连接

`Broadcast(room)` 发给谁,以 **`world.rooms[room].users_in_room`(逻辑成员)** 为准,再按 nick 到 ConnectionManager 取连接:

```python
for nick in world.rooms[r].users_in_room:
    if (c := conns.get(nick)) is not None:   # OFFLINE / 无连接者自动跳过
        enqueue(c, msg)
```

- **OFFLINE 玩家**在 `users_in_room` 里(座位保留)、但 `conns.get(nick)` 为空——跳过,它重连时由 `StateSnapshot` 补齐。
- **观战者**(`JoinRoom` 后 `WATCHING`)也在 `users_in_room` 里,照收公开广播。
- dispatch 在 GameLoop 内、commit 之后读 `world`(自己刚写的、同协程),安全;隐私由 core 在事件层把关(他人底牌不进 `Broadcast`,见 [core.md](core.md) 不变量 3)。

> 大厅用户**不在任何 `users_in_room`**,所以收不到房间广播——符合预期(他们还没进房)。要给大厅推送是另一条路 `LobbyBroadcast`(见 [lobby.md](lobby.md) 待定)。

## dispatch:事件 → 物理落点

事件派发抽成独立 **`Dispatcher`**(持 `world`(只读)/ `conns` / `persist` / `timer` / `inbox` / `history`);GameLoop 成功 commit 后对每个 event 调 `dispatcher.dispatch(ev)`——**同步**派发(只 `put_nowait` / 调本地快设施,不 `await`,守不变量 3),错误回发走 `dispatcher.send_error(cmd, err)`。GameLoop 本身只持 `world`/`inbox`/`dispatcher`:

```python
class Dispatcher:                                        # 持 world(只读)/conns/persist/timer/inbox/history
    def dispatch(self, ev: Event) -> None:
        match ev:
            case Broadcast(room=r, msg=m):
                room = self.world.rooms.get(r)           # reduce 可能刚销毁该房(最后一人离开)
                if room is None:
                    return                               # 房已销毁 → 无人可广播,跳过(见下「销毁房」)
                for nick in room.users_in_room:          # 逻辑成员 → 按 nick 取连接
                    if (c := self.conns.get(nick)) is not None:
                        self._enqueue(c, m)
                if isinstance(m, ChatMessage):           # 房聊广播 → 入环形缓冲,供进/重进房 FetchRoomChat 拉(见 messaging.md)
                    self.history.append(r, m)
            case Personal(nick=n, msg=m):                # 底牌 / StateSnapshot / 离开者回执,按 nick 私发
                if (c := self.conns.get(n)) is not None:
                    self._enqueue(c, m)
            case Persist(payload=p):
                if isinstance(p, HandRecordWrite) and p.end_time is None:
                    p = replace(p, end_time=self._now())  # 手牌记录 end_time 由 shell 派发时盖墙钟(core 不读钟,见 db.md)
                self.persist.put(p)                      # 写缓冲单入口,内部 _state_key 分流,见 db.md
            case TurnChanged(room=r, acting_nick=n, epoch=e):   # 字段序同 events.py 的 TurnChanged 数据类
                self.timer.on_turn_changed(r, n, e)      # B 组:同步调 Timer;倒计时长由 Timer 读 gameconfig.ACTION_TIMEOUT,不随事件带
            case ClearAction(room=r):
                self.timer.clear_action(r)

    def _enqueue(self, conn: Connection, msg) -> None:
        try:
            conn.outbound.put_nowait(msg)
        except asyncio.QueueFull:                        # ≤20 人正常不会满;满 = 该连接 Sender 卡死
            log.warning("slow client dropped nick=%s", conn.nick)
            self._drop_connection(conn)                  # unregister + 投 Disconnect(inbox 满则丢 + CRITICAL);重连靠 StateSnapshot 补回
```

`Personal` 只带 `nick`(不带 room),因为连接按 nick 全局唯一。错误回发同理:`send_error(cmd, err)` 用 `conns.get(cmd.origin)` 找发起连接(见 [error.md](error.md));`origin=None` 的系统命令无连接可回发,只落 `log.warning`。**私聊**也走 `conns.get(对方 nick)`——同一张表,这就是模型 2 把私聊变 O(1) 的地方(见 [messaging.md](messaging.md))。

> **销毁房 / 离开者的确认**:`Broadcast` 的收件人是 dispatch 时**当前 `world` 房成员**,而 `LeaveRoom`/`Cleanup` 在同一条 reduce 里已把离开者移出 `users_in_room`(房空还会销毁该房)。所以「离开者本人要不要收到回执」**不能靠 `Broadcast`**——它已经不在成员名单里(房还可能没了)。要给离开者确认,reduce 产 `Personal(nick=离开者, UserLeft)`;留在房里的人才靠 `Broadcast(room, UserLeft)`。房已销毁时 `Broadcast` 自动跳过(上面的 `rooms.get` 容错),不报错。

## 连接生命周期(一条 Receiver 的一生)

```
握手鉴权(绑 nick) → 登记(可能顶替) → 起 Sender → 投 Connect → 收帧循环(含 JoinRoom/LeaveRoom) → 退出清理
```

1. **握手鉴权**(详见 [auth.md](auth.md)):`ws connect ?sid=<session_id>`(**不带 room_id**)→ 按 `session_id` 查会话表得 `nick`/`token` → 派生密钥建 `SecureChannel`。**第一帧 MAC 验过 = 证明持有 token**。鉴权失败:ws 关闭码拒掉,**绝不建 `Connection`**。
2. **登记**:建 `Connection(nick=…)` → `old = conns.register(conn)`。`old` 非空 = **顶替**(见下):关 `old.ws`、cancel `old.sender_task`,**不投 `Disconnect`**。
3. **起 Sender**:`conn.sender_task = create_task(sender_loop(conn))`。
4. **接入(进的是大厅,不是房间)**:投 `Connect(nick)`。reduce 按 `world` 真相分三类处理(`_connect`,0022 起、0031 补顶替臂)——
   - **纯大厅**(`nick` 不在 `world.users`)→ core 无事可做(进房 + 载入积分走 `JoinRoom`)。
   - **在房 + `OFFLINE`**(正在某房、之前断线)→ **重连**:恢复在线 + `Broadcast(UserStatusChanged)` + 私发 `Personal(StateSnapshot)` 对齐其所在房。**恢复到的状态按 world 推断,不存断线前状态**(`_disconnect` 已用 `OFFLINE` 覆盖):在进行中手牌(是其 `Player`)→ `PLAYING`、有座但不在手 → `SITTING_IN`(需重新 ready)、无座 → `WATCHING`(皆合法 `OFFLINE→*` 转移)。
   - **在房 + 在线**(状态非 `OFFLINE`)→ **顶替再连**:新 ws 接管旧连接,旧连接被静默关闭、**未投 `Disconnect`**(见下「顶替语义」),故 `world` 仍记其在线。此时只私发 `Personal(StateSnapshot)` 让**新连接**对齐桌面;状态未变 → **不改不广播**(对房内他人无信息变化,用户无感,见「会话过期与密钥轮换」)。这与下文 §会话轮换「顶替 → 私发 `StateSnapshot`」一致。

   > reduce **不感知「连接」**,无法区分「顶替再连」与「同一连接重复 `Connect`」;但 Receiver 每条连接只投一次 `Connect`(见下「收帧循环」前的 `Connect` 投递),对**已在房在线** nick 的第二次 `Connect` 必来自新 ws(= 顶替)。重发快照是只读、隐私逐收件人(见 [core.md](core.md) `StateSnapshot`)、幂等安全的——正确性不靠「证明这是顶替」,而靠「快照本身无害可重发」。**积分始终不在 `Connect` 载入**,等 `JoinRoom` 才载入(见 [lobby.md](lobby.md) / [user.md](user.md))。
5. **收帧循环**:`while: 收帧 → 验+解 → ClientMessage → Command(盖 origin=nick)→ inbox.put`。`LeaveRoom`/游戏动作/房聊在这条循环里(房间由 `world.users[nick].room` 推定,命令不带 room);**`JoinRoom` 例外**——报文只带 `room`,Receiver 按连接 nick **读 DB 富化 `uid`/`loaded`**(异步)再构 `JoinRoom(room, uid, loaded)`(身份/积分不信报文,见 [changes/0030](refactor/changes/0030-p4-per-join-wire-load.md))。每收一帧 `timer.heartbeat(nick)` 续命(见 [timer.md](timer.md))。协议/解析错误直接构造 `ErrorMessage` 投本连接 `outbound`。
6. **退出清理**(ws 断 / 异常):
   - `conns.unregister(conn)`(只删自己,顶替场景自动跳过)。
   - **仅当 `is_current` 为真才投 `Disconnect(nick)`**:被顶替的旧连接 `is_current=False`,**静默退出**(否则会把刚重连上的人误标 OFFLINE)。reduce 收 `Disconnect`:若 nick 在某房则标 `OFFLINE` 保座,否则(大厅)只是没了 presence、无 world 变化。
   - **不主动 `drop_liveness`**:断开后保活仍在走,到期触发 `Cleanup` 给占座窗口(见 [timer.md](timer.md))。

> **断开 ≠ 离场**:在房间里断开只标 `OFFLINE`、保留座位;真正退筹释座等 `LIVENESS_TIMEOUT` 的 `Cleanup`,或用户主动 `LeaveRoom`(见 [lobby.md](lobby.md))。

## 顶替语义(同 nick 新连接顶掉旧的)

**决策:一个 nick 全局只有一条有效连接;新 ws 接管、旧 socket 关闭。** 理由:重连常发生在"旧 socket 假死、尚未被 LIVENESS 判掉"时,顶替让客户端永远只有一条有效连接,`nick→outbound` 路由稳定。

正确性要点(都在 `register`/`unregister`/`is_current` 里):

- **登记即顶替**:`register` 用新连接覆盖 `nick` 项、返回旧连接;调用方关旧 ws + cancel 旧 Sender,**不投 Disconnect**。
- **注销带身份判定**:`unregister` 只在"登记的就是我"时才删,旧连接退出不会误删已上位的新连接。
- **Disconnect 带身份判定**:旧连接 `is_current=False` 不投 Disconnect——避免"新连接刚上线、旧连接的 Disconnect 随后把它标 OFFLINE"的乱序。

## 会话过期与密钥轮换(连接层落点)

**决策:密钥定期轮换,且对用户无感——靠"定期无感重连"换钥,不在单条连接里换。**(语义全貌见 [auth.md](auth.md) 的「会话过期与密钥轮换」;这里只讲连接层怎么落)

- **无感轮换**:客户端在 `SESSION_TTL` 到期前用缓存的 `K_user` 静默重登 → 新 `session_token`/新密钥 → 新连接走**顶替**(见上)接管该 `nick` → reduce 私发 `StateSnapshot` 对齐其当前房(若在房)。顶替的身份判定保证旧连接静默退出、不投 `Disconnect`,所以用户无感。
- **exp 兜底**:服务器在 `session_token.exp` 到点拒该会话;正常客户端已提前轮换不会撞到。
- **真正掉线才需重登**:客户端进程还在、`K_user` 在内存 → 轮换/重连全静默;进程彻底关闭(`K_user` 丢失)或登出 → 下次必须用户手输 `K_user` 重登。
- ws 通道凭证 = `K_user` + `session_token`,不另设 refresh token;REST 另走现有 JWT(见 [auth.md](auth.md) 的「token 层级」)。

## lifespan:启动与优雅关闭

由 FastAPI lifespan 编排,**启动正序、关闭反序**:

**启动**
1. 加载配置(`gameconfig`/`settings`),缺字段即启动失败(见 [config.md](config.md))。
2. 配置日志(见 [log.md](log.md))。
3. 开 DB 连接池。
4. 建**空** `World`(`world.rooms` 为空;**动态房——谁都可创建 / 空则消失**,房随 `JoinRoom` 到不存在的房而建、随空房而销毁,见 [lobby.md](lobby.md) / [changes/0049](refactor/changes/0049-dynamic-rooms.md))。
5. 建写缓冲 + 起 `PersistWriter`。
6. 建 `Timer` + 起 `timer.run()`。
7. 建 `ConnectionManager`、`Dispatcher(world, conns, persist, timer, inbox, history)`、`GameLoop(world, inbox, dispatcher)` + 起 `gameloop.run()`。
8. 挂载 ws 端点(Receiver),**此刻起接受连接**。

**关闭(必须 drain,见 [db.md](db.md))**
1. **停 Receiver**:不再接新连接 / 新命令。
2. **排空 `inbox` + 停 GameLoop**:在途命令处理完,不再产生新 `Persist`。
3. **PersistWriter 终结 flush**:`swap`+落库直到 `is_empty()`;有限重试,超 `DB_DRAIN_TIMEOUT_MS` 放弃并落 **CRITICAL**。
4. 关 DB 连接池、cancel 各 Sender。

> 非优雅崩溃丢进行中手牌 + 未 flush 的积分变更——积分非货币,本规模接受(见 [storage.md](storage.md))。

> **dev shell 落地(明文脚手架,见 [changes/0018](refactor/changes/0018-d-dev-shell.md) / [0029](refactor/changes/0029-p4-db-backed-dev-shell.md) / [0030](refactor/changes/0030-p4-per-join-wire-load.md))**:`shell/lifespan.py` 的 `DevShell.setup()` 启动序为 **async engine(`sqlite+aiosqlite` 缺省)→ `create_all` 建表(dev 引导,无 Alembic;生产用迁移)→ 幂等种子 dev 用户进 DB(原型注册 P5 未建的替身)→ 建**空** `world`(dev 房空预置)→ `OrmPersister` 落库(替 `NullPersister`)→ 起 GameLoop/Timer/PersistWriter → 挂 `/dev/ws`**;关闭 `DevShell.stop()` 走**反序关闭**(0046):① cancel Timer+GameLoop → ② 同步排空 inbox(`gameloop.handle` 处理在途命令,其 Persist 入缓冲)→ ③ cancel PersistWriter 循环 + `await drain()`(有界,超 `DB_DRAIN_TIMEOUT_MS` → CRITICAL)→ ④ cancel 各 Sender + `await engine.dispose()`。**注**:dev 的 ①-④ 不与上「关闭」1-4 一一对应——spec 步 1「停 Receiver」在 dev **无显式动作**(uvicorn 关闭时已先撕 ws receiver,再调 lifespan shutdown),Timer-cancel 是 dev 专属并入 ①。**per-join 载入已落地(0030)**:dev 用户连接进大厅 → 主动 `join_room{"dev"}` → Receiver 按 nick 读 DB 富化 `uid`/`loaded` → `JoinRoom(room, uid, loaded)` 装入(退役了 0029 的「预置在房 + 启动整载」)。

## 与架构契约(必须守住)

1. **ConnectionManager/Timer 的内部表是 shell 私有连接态,不是 `world`**;只有 GameLoop 经 reduce commit 改 `world`。
2. **对外发送只经 `Connection.outbound` → Sender**:禁止 `create_task(ws.send())` 或在 dispatch 里直接 `ws.send`(守不变量 4/6)。
3. **加解密只在 ws 边界**(Receiver 验解 / Sender 加密),`outbound` 装明文,core 不知有加密。
4. **顶替/注销/Disconnect 都带连接身份判定**(`is`/`is_current`),杜绝"旧连接误删新连接 / 误标 OFFLINE"。
5. **连接绑 nick(全局唯一),不绑房间**;房间由 `JoinRoom`/`LeaveRoom` 改 `world.users[nick].room`。广播成员 = `world` 房间的 `users_in_room`,按 nick 取连接,无连接者跳过。
6. **队列有界,满即判慢客户端**:丢连接 + `Disconnect`,绝不阻塞 GameLoop(见 [architecture.md](architecture.md))。
7. **关闭必须 drain**(见 [db.md](db.md))。

## 待定 / 未设计

- **大厅 / 房间管理(lobby)**:见 [lobby.md](lobby.md)(已设计:连接模型 2、`JoinRoom`/`LeaveRoom`、房间列表 REST、静态预置房;动态建房仍待定)。
- **私聊 / 房聊(messaging)**:**已落地**(房聊 reduce + shell 文本防护/限速/环形缓冲 0021/0033/0036;私聊 shell 路由 发/读游标/登录补收/保留清理 0038-0041)——设计见 [messaging.md](messaging.md);本文只约定路由(`conns.get(nick)`),shell 侧的 DM 路由 / 房聊环形缓冲写读 / 登录补收尚缺本文正式章节(待补)。
- **wire 协议清单**:`ClientMessage`/`ServerMessage` 全集 + `StateSnapshot` 字段**已写**(`app/wire/client.py`/`server.py` + codegen `wire.gen.ts`),治理见 [wire.md](wire.md)。本文只约定路由,不定报文字段。
- **背压上限取值**:`inbox` / `outbound` 队列大小、慢客户端判定阈值进 [config.md](config.md),具体值实测定。
