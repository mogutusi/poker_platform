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

## 房间从哪来(v1:配置静态预置)

lifespan 启动时按配置 `ROOMS` 预置 `world.rooms`(每个含 `name` / `small_blind` / `buy_in` / `max_seats`)。房间状态**不持久化**(同 [storage.md](storage.md)),重启按配置重建。

> 无论房间是"启动预置"还是日后"用户动态建",它都只是 `world.rooms` 里一个 `Room`,后续机制完全相同。动态建房见「待定」。

## 进/出房:`JoinRoom` / `LeaveRoom` 命令

**`JoinRoom(room, uid, loaded)`**(大厅 → 房间):

- shell:用户在大厅选某房 → Receiver 从 DB 读该 nick 的 `uid` + `points` → 投 `JoinRoom(room, uid, loaded=points)`。
- reduce 校验:房间存在(`NO_SUCH_ROOM`);**nick 不在 `world.users`**(单房间约束 `ALREADY_IN_ROOM`——已在别房要先 `LeaveRoom`)。
- reduce 改副本:装 `UserState(uid, nickname=nick, room=R, points=loaded)` 进 `world.users`;加进 `room.users_in_room` 为 `WATCHING`。
- 产出:`Broadcast(room, UserJoined)` + `Personal(StateSnapshot)`(整桌当前态)。core 已落地(0022 `_join_room`);**client `join_room{room}` 报文 + Receiver 读 DB 富化 `uid`/`loaded` 留 [0030]**;dev shell 自 [0029](refactor/changes/0029-p4-db-backed-dev-shell.md) 已 **DB-backed**(种子 + 启动期整体载入),但仍用「预置用户在房 WATCHING」**绕开 per-join `JoinRoom`**——真 per-join 载入(本流程)随 0030 的 wire 报文 + Receiver DB 读落地。
- **`ROOM_FULL` v1 暂不强制**:≤20 在线、房极少下「满」非真实约束,且「满桌不可观战」是有损 UX 的武断规则;故 v1 不限观战(座位可用性由 `SitDown` 的 `SEAT_TAKEN` 兜),`ErrorCode.ROOM_FULL` 保留待引入容量上限。失败码现为 `NO_SUCH_ROOM` / `ALREADY_IN_ROOM`(见 [changes/0022](refactor/changes/0022-p1-join-room-state-snapshot.md) 决策 5)。

**`LeaveRoom()`**(房间 → 大厅):

- reduce:若在座/在局,先按规则结算退筹(产出最后一笔 `Persist(PointsWrite)`);从 `room.users_in_room` 移除;**最后** `del world.users[nick]`;若房间已空再 `del world.rooms[room]`(动态房)。
- 产出:`Personal(nick=离开者, UserLeft)`(回执给本人——他已不在成员名单/房可能已销毁,收不到 Broadcast)+ `Broadcast(room, UserLeft)`(给留下的人;房已销毁则 dispatch 自动跳过,见 [connection.md](connection.md))。
- 用户回到大厅(连接还在,只是不在任何 world 房)。
- 对局进行中离开:复用断线/清理那套(在手牌里则自动弃牌、手结束再真正释座);core 规则已落地(见 [rules.md](rules.md) ④ / [changes/0014](refactor/changes/0014-p1-inhand-lifecycle.md):`room.leaving` 标记 + `_finalize_hand` 末 `_evict`)。

**换房** = `LeaveRoom` 再 `JoinRoom`(单房间约束要求先离开当前房)。

## 红利:游戏命令不再带 `room`

模型 2 下用户同时只在一个房间,所以 `PlayerAction` / `SitDown` / `BuyIn` / 房聊 等命令**不带 `room`**——reduce 用 `world.users[nick].room` 定位目标房,工作副本 `checkout` 也据此解析(只有 `JoinRoom` 例外:目标房在命令里)。**身份和房间都不进报文**(见 [wire.md](wire.md)),报文只剩动作参数。

## 房间列表(REST 读)

```
GET /lobby/rooms → [RoomMeta]
```

- **`RoomMeta`** = 静态配置(`id`/`name`/`small_blind`/`buy_in`/`max_seats`) + 实时(在座数/观战数/状态)。
- 实时头数来自 `world.rooms[r].users_in_room` 的统计(**committed 只读、展示用、可滞后**;不做实时判定)。
- **v1 客户端轮询**(≤20 人,几秒一次足够);实时推送(`LobbyBroadcast`)是 future,见「待定」。
- `RoomMeta` 是 **wire DTO ≠ `Room`**:完整游戏状态(`deck`/`hand`/各人筹码)绝不上 lobby(见 [wire.md](wire.md))。

## 改昵称(你的决策:只能不在房间时)

- **规则**:仅当 nick **不在 `world.users`**(即在大厅)才允许改昵称;在房间内一律拒(`CANT_CHANGE_NICK_IN_ROOM`)。
- **为什么**:`nickname` 是 `world` 的键(座位、`contributed`、ConnectionManager 都按它索引),在用时改会让键错乱。大厅用户不是 world 键,改它只是 DB + 会话表更新。
- **落点**:走 **REST**(`PATCH /user/nickname`)或大厅操作——因为大厅用户不在 `world`,直接改 DB + 更新会话表里的 `nickname` 即可,**不经 reduce**。下次 `JoinRoom` 自然用新 nick 当键。

## 与架构契约(必须守住)

1. **大厅无 `world` 状态**:房间集合在 `world.rooms`,大厅本身只是 ConnectionManager(presence) + REST。
2. **`world.users` = 当前在游戏房的人**;大厅用户只活在 ConnectionManager。
3. **进出房走 `JoinRoom`/`LeaveRoom` 命令**,守单房间约束;游戏命令不带 `room`,由用户当前房推定。
4. **房间列表是 REST 读**(展示、可滞后),不做实时判定;**房间状态不持久化**,重启按配置重建。
5. **改昵称仅限大厅**(不在 `world.users` 时),避免 world 键错乱。

## 待定 / future

- **动态建房**:`CreateRoom(config)` 命令(用户建房、设盲注/买入)、空房回收、命名冲突。它改的是 `world.rooms` **顶层**(不是某个房),属"注册表级命令",`checkout` 要从"单房间"扩展到"房间注册表级"——v1 先用静态配置绕开。
- **实时房间列表推送**:新增 `LobbyBroadcast(msg)` 事件 → dispatch 发给所有**大厅连接**(nick 不在 `world.users` 的连接)。v1 用轮询。
- **离桌中途在局的精确处理**:接 [timer.md](timer.md) 的 `Cleanup` 与弃牌规则。
- **messaging(私聊 + 房聊)**:见 [messaging.md](messaging.md)。完整 **presence** 只读视图(谁在线/在哪房/状态)仍待单列。
