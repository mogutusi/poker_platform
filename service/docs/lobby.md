# 大厅与房间生命周期(lobby)

## 定位

**大厅不是一个游戏房间,而是"连接上、但还没坐到任何牌桌"的默认状态 + 房间的发现与进入。** 它几乎没有新的 core 逻辑——房间集合在 `world`(游戏状态)、进出房是命令、房间列表是 REST 读。本文基于**连接模型 2**(连接绑用户、房间是连接里的频道,见 [connection.md](connection.md))。

> 前置:[connection.md](connection.md)(连接模型 2、ConnectionManager)、[user.md](user.md)(全局积分载入/驱逐)、[core.md](core.md)(房间/命令)。

## 一个用户的"位置"模型

```
登录 → WS 连上(绑 nick)──默认在【大厅】── JoinRoom(R) ──▶【房间 R】── LeaveRoom ──▶ 回大厅
        (shell 有连接 / world.users 没有它)        (world.users 装入 / 退出时驱逐)
```

- **大厅态**:连接存在(ConnectionManager 有它,presence 可见),但 **`world.users` 里没有这个 nick**——大厅不占 world 游戏状态。
- **房间态**:`JoinRoom` 把 nick 装进 `world.users`(`room=R` + 载入积分),`LeaveRoom`/清理时驱逐。

> **这条直接让你定的「只能不在房间时改昵称」成立**:大厅用户不是任何 `world` 键(座位/`contributed`/键都没用到它),所以改昵称只动 DB + 会话表,不会让 world 的键错乱(见下「改昵称」)。

## 房间从哪来(动态:谁都可创建 / 空则消失,0049)

**无静态预置房。** 用户对一个不存在的房名发 `JoinRoom` 即把它创建出来(见下),最后一人离开则销毁——用户明示的设计。启动时 `world.rooms` 为空(见 [connection.md](connection.md) lifespan);房间随进房而生、随空房而灭,状态**不持久化**(同 [storage.md](storage.md))。

> 房都只是 `world.rooms` 里一个 `Room`,「创建」= `JoinRoom` 到不存在的房、「销毁」= 最后一人离开(core.md §房间生命周期)。建房配置由 shell 从 `gameconfig` 盖进命令(创建者无特权、无房主),建后任何在房成员可 `SetSmallBlind`/`SetBuyIn` 调参。

## 进/出房:`JoinRoom` / `LeaveRoom` 命令

**`JoinRoom(room, uid, loaded, create?)`**(大厅 → 房间;房不存在则**创建**):

- shell:用户在大厅选/新建某房 → Receiver 从 DB 读该 nick 的 `uid` + `points`、并从 `gameconfig` 盖建房默认配置 `create` → 投 `JoinRoom(room, uid, loaded=points, create=…)`。
- reduce 校验:**nick 不在 `world.users`**(单房间约束 `ALREADY_IN_ROOM`——已在别房要先 `LeaveRoom`);**房不存在则用 `create` 建房**(空座 `PENDING_START`);`create=None` 且房不存在 → `NO_SUCH_ROOM`(防御,shell 应总带 `create`)。
- reduce 改副本:装 `UserState(uid, nickname=nick, room=R, points=loaded)` 进 `world.users`;加进 `room.users_in_room` 为 `WATCHING`。
- 产出:`Broadcast(room, UserJoined)` + `Personal(StateSnapshot)`(整桌当前态)。**全链已落地**:core `_join_room`(0022)+ wire `join_room{room}` 报文 + Receiver 按连接 nick 读 DB 富化 `uid`/`loaded`([0030](refactor/changes/0030-p4-per-join-wire-load.md))。dev shell 用户连接进大厅 → 主动 `join_room{"dev"}` 载入(0030 退役了 [0029](refactor/changes/0029-p4-db-backed-dev-shell.md) 的「预置在房 + 启动整载」)。
- **`ROOM_FULL` v1 暂不强制**:≤20 在线、房极少下「满」非真实约束,且「满桌不可观战」是有损 UX 的武断规则;故 v1 不限观战(座位可用性由 `SitDown` 的 `SEAT_TAKEN` 兜),`ErrorCode.ROOM_FULL` 保留待引入容量上限。失败码现为 `NO_SUCH_ROOM` / `ALREADY_IN_ROOM`(见 [changes/0022](refactor/changes/0022-p1-join-room-state-snapshot.md) 决策 5)。

**`LeaveRoom()`**(房间 → 大厅):

- reduce:若在座/在局,先按规则结算退筹(产出最后一笔 `Persist(PointsWrite)`);从 `room.users_in_room` 移除;**最后** `del world.users[nick]`;若房间已空再 `del world.rooms[room]`(动态房)。
- 产出:`Personal(nick=离开者, UserLeft)`(回执给本人——他已不在成员名单/房可能已销毁,收不到 Broadcast)+ `Broadcast(room, UserLeft)`(给留下的人;房已销毁则 dispatch 自动跳过,见 [connection.md](connection.md))。
- 用户回到大厅(连接还在,只是不在任何 world 房)。
- 对局进行中离开:复用断线/清理那套(在手牌里则自动弃牌、手结束再真正释座);core 规则已落地(见 [rules.md](rules.md) ④ / [changes/0014](refactor/changes/0014-p1-inhand-lifecycle.md):`room.leaving` 标记 + `_finalize_hand` 末 `_evict`)。

**换房** = `LeaveRoom` 再 `JoinRoom`(单房间约束要求先离开当前房)。

## 房间参数配置:`SetSmallBlind` / `SetBuyIn`(任何在房成员)

预置房的注码/默认买入可由 **任何在房成员**(含观战者)在运行时调整(`SetSmallBlind(amount)` / `SetBuyIn(amount)`,大盲 = 2×小盲派生)。这是**改既有房的参数**(单房间 scoped,走 `checkout`/`commit`),与「动态建房」`CreateRoom`(注册表级,见「待定」)是两回事。

- **无房主**(0044 定:用户明示不要房管理、每个人都能改):房配回归 peer 模型,与开局(任何 ready 在座者发起)/ 免盲投票一致。无 `owner`/`host` 字段、无 0 号位特权。原 0043 的「0 号位占座者授权 + `NOT_ROOM_OWNER`」已撤(见 [changes/0044](refactor/changes/0044-room-config-any-member.md))。
- **授权 = 在房即可**:发起人在 `room.users_in_room`(含观战者)→ 放行;不在 → `NOT_IN_ROOM`(这是命令路由的必然,非「权限」)。
- **时机 = 仅两手之间**(`room.hand is None` / `PENDING_START`):这是 **correctness 门、非授权**——局中改盲会污染已锁入本手的下注(小盲喂下盲 + 各处大盲派生),故 `HAND_IN_PROGRESS` 拒(两命令对称)。
- **上下限**:`gameconfig.MIN/MAX_SMALL_BLIND` / `MIN/MAX_BUY_IN`,由 **shell 进 reduce 前防护**(core 不 import config),越界 `INVALID_SMALL_BLIND`/`INVALID_BUY_IN`;reduce 只兜结构(在房 / 非局中 / 正额)。
- **产出**:`Broadcast(RoomConfigChanged{small_blind,big_blind,buy_in})` 全房(含观战者)对齐;**不落库**(房状态不持久,[storage.md](storage.md)),重启回 `gameconfig` 缺省。`StateSnapshot` 也带 `buy_in`,重连可见当前值。详见 [changes/0043](refactor/changes/0043-room-config-commands.md) + [0044](refactor/changes/0044-room-config-any-member.md)。

## 红利:游戏命令不再带 `room`

模型 2 下用户同时只在一个房间,所以 `PlayerAction` / `SitDown` / `BuyIn` / 房聊 等命令**不带 `room`**——reduce 用 `world.users[nick].room` 定位目标房,工作副本 `checkout` 也据此解析(只有 `JoinRoom` 例外:目标房在命令里)。**身份和房间都不进报文**(见 [wire.md](wire.md)),报文只剩动作参数。

## 房间列表(REST 读)—— 已落地(0048)

```
GET /lobby/rooms → [RoomMeta]        # app/rest/lobby.py:list_rooms / make_lobby_router
```

- **`RoomMeta`**(Pydantic,`app/rest/lobby.py`)= 静态配置(`id`/`small_blind`/`big_blind`/`buy_in`/`max_seats`)+ 实时(`seated`/`watching`/`status`)。**v1 无独立 `name` 字段**——`id` = `world.rooms` 的键,即人读名(动态建房引入命名后再拆);`big_blind` = 2×小盲派生(同 `RoomConfigChanged`)。
- `seated` = 占用座位数(`len([s for s in room.seats if s])`,**含 OFFLINE 保座**);`watching` = `users_in_room` 中 `WATCHING` 状态数(在座与在线正交)。
- 实时头数/状态来自 `world.rooms[r]` 的统计(**committed 只读、展示用、可滞后一拍**;不做实时判定)。**这是唯一读 `world` 的 REST 端点**——房态内存权威、从不落库(见 [storage.md](storage.md) / [rest.md](rest.md) §共同原则);投影 `list_rooms` 纯同步无 `await`,对唯一写者 GameLoop 原子读、不撕裂(同 [presence.md](presence.md) 只读范式)。
- **v1 客户端轮询**(≤20 人,几秒一次足够);实时推送(`LobbyBroadcast`)是 future,见「待定」。
- `RoomMeta` 是 **REST DTO ≠ `Room`**:完整游戏状态(`deck`/`hand`/各人筹码)绝不上 lobby(见 [wire.md](wire.md));它**不进 ws `ServerMessage` 联合**(非 ws 消息),故不进 `wire.gen.ts`——前端 REST 类型走 openapi(P7 无 node 待解,见 [changes/0048](refactor/changes/0048-rest-lobby-rooms.md))。
- **dev 明文无鉴权**(与 dev ws 端点一致);lobby-rooms 只暴露房配 + 头数(无隐私)。P5 上 JWT 时按 [rest.md](rest.md)「REST 走 JWT」补。

## 改昵称(你的决策:只能不在房间时)

- **规则**:仅当 nick **不在 `world.users`**(即在大厅)才允许改昵称;在房间内一律拒(`CANT_CHANGE_NICK_IN_ROOM`)。
- **为什么**:`nickname` 是 `world` 的键(座位、`contributed`、ConnectionManager 都按它索引),在用时改会让键错乱。大厅用户不是 world 键,改它只是 DB + 会话表更新。
- **落点**:走 **REST**(`PATCH /user/nickname`)或大厅操作——因为大厅用户不在 `world`,直接改 DB + 更新会话表里的 `nickname` 即可,**不经 reduce**。下次 `JoinRoom` 自然用新 nick 当键。

## 与架构契约(必须守住)

1. **大厅无 `world` 状态**:房间集合在 `world.rooms`,大厅本身只是 ConnectionManager(presence) + REST。
2. **`world.users` = 当前在游戏房的人**;大厅用户只活在 ConnectionManager。
3. **进出房走 `JoinRoom`/`LeaveRoom` 命令**,守单房间约束;游戏命令不带 `room`,由用户当前房推定。
4. **房间列表是 REST 读**(展示、可滞后),不做实时判定;**房间状态不持久化 + 无静态预置**——房随进房动态创建、随空房销毁(0049),重启从空 `world.rooms` 起步。
5. **改昵称仅限大厅**(不在 `world.users` 时),避免 world 键错乱。

## 待定 / future

- **动态建房已落地(0049)**:「谁都可创建 / 空则消失」= `JoinRoom` 到不存在的房即建、最后一人离开即销毁(见 core.md §房间生命周期)。**余项**:建房时自定盲注/买入/座位(往 `join_room` 报文加 `create` 字段让创建者设参;现用 `gameconfig` 默认 + 建后 `SetSmallBlind`/`SetBuyIn` 调)、房名冲突/命名规则、反滥用的建房数量上限——本规模(内网 ≤20)暂不设。
- **实时房间列表推送**:新增 `LobbyBroadcast(msg)` 事件 → dispatch 发给所有**大厅连接**(nick 不在 `world.users` 的连接),建房/销房时增量推;v1 用轮询 `GET /lobby/rooms`(0048)。
- **离桌中途在局的精确处理**:接 [timer.md](timer.md) 的 `Cleanup` 与弃牌规则。
- **messaging(私聊 + 房聊)**:见 [messaging.md](messaging.md)。完整 **presence** 只读视图(谁在线/在哪房/状态)仍待单列。
