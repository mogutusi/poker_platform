# 大厅与房间生命周期(lobby)

## 定位

大厅 = 「连接上了、但还没进任何牌桌」的默认状态,再加上房间的发现与进入。

这里几乎没有新的 core 逻辑:房间集合放在 `world`,进出房是命令,房间列表是 REST 读。本文基于连接模型 2:连接绑定用户,房间只是连接里的一个频道。

> 前置:[connection.md](connection.md)(连接模型 2、ConnectionManager)、[user.md](user.md)(全局积分载入/驱逐)、[core.md](core.md)(房间/命令)。

## 一个用户的"位置"模型

一个用户要么在大厅,要么在某一个房间。

```
登录 → WS 连上(绑 nick)──默认在【大厅】── JoinRoom(R) ──▶【房间 R】── LeaveRoom ──▶ 回大厅
        (shell 有连接 / world.users 没有它)        (world.users 装入 / 退出时驱逐)
```

- **大厅态**:连接存在,ConnectionManager 里有它、presence 也能看见,但 `world.users` 里没有这个 nick,大厅不占 world 的游戏状态。
- **房间态**:`JoinRoom` 把 nick 装进 `world.users`,同时置 `room=R` 并载入积分;`LeaveRoom` 或清理时驱逐。

「只能不在房间时改昵称」这条规则就靠这一点成立:大厅用户不是任何 `world` 的键。详见下文「改昵称」。

## 房间从哪来(动态:谁都可创建 / 空则消失,0049)

没有静态预置房间。

- 对一个不存在的房名发 `JoinRoom`,房间就地创建;最后一人离开,房间销毁。启动时 `world.rooms` 为空,见 [connection.md](connection.md) lifespan。
- 房间状态不持久化,同 [storage.md](storage.md);建房配置由 shell 从 `gameconfig` 盖进命令。
- 创建者没有特权,也没有房主;建好之后任何在房成员都能调参。见下节及 core.md §房间生命周期。

## 进/出房:`JoinRoom` / `LeaveRoom` 命令

**`JoinRoom(room, uid, loaded, create?)`**:大厅 → 房间;房间不存在则创建。

- shell 侧:用户在大厅选中或新建某房 → Receiver 从 DB 读该 nick 的 `uid` 与 `points`,并从 `gameconfig` 盖出建房默认配置 `create` → 投 `JoinRoom(room, uid, loaded=points, create=…)`。
- reduce 侧,按顺序:
  1. 校验 nick 不在 `world.users`,否则 `ALREADY_IN_ROOM`。
  2. 房间不存在则用 `create` 建房,座位为空、状态 `PENDING_START`。
  3. 若 `create=None` 且房不存在 → `NO_SUCH_ROOM`。这是防御分支,shell 应该总是带上 `create`。
  4. 装 `UserState(uid, nickname=nick, room=R, points=loaded)` 进 `world.users`。
  5. 加进 `room.users_in_room`,状态为 `WATCHING`。
  6. 产出 `Broadcast(room, UserJoined)` 与 `Personal(StateSnapshot)`,后者是整桌当前态。
- 已落地的部分:core 的 `_join_room`(0022);wire 的 `join_room{room}`;Receiver 读 DB 富化 `uid`/`loaded`,见 [0030](refactor/changes/0030-p4-per-join-wire-load.md)。[0029](refactor/changes/0029-p4-db-backed-dev-shell.md) 的「预置在房 + 启动整载」已退役。
- `ROOM_FULL` 在 v1 不强制:在线 ≤20 人,「满」不是真实约束,也不限制观战;座位冲突由 `SitDown` 的 `SEAT_TAKEN` 兜住。`ErrorCode.ROOM_FULL` 保留,等引入容量上限时再用;因此当前失败码只有 `NO_SUCH_ROOM` 与 `ALREADY_IN_ROOM`,见 [changes/0022](refactor/changes/0022-p1-join-room-state-snapshot.md) 决策 5。

**`LeaveRoom()`**:房间 → 大厅。

- reduce 侧,按顺序:
  1. 若在座或在局,先按规则结算退筹,产出最后一笔 `Persist(PointsWrite)`。
  2. 从 `room.users_in_room` 移除。
  3. `del world.users[nick]`。
  4. 房间已空则 `del world.rooms[room]`。
  5. 用户回大厅,连接仍然在。
- 产出两条消息:`Personal(nick=离开者, UserLeft)`,因为他已不在成员名单里、收不到 Broadcast 所以要私发;以及 `Broadcast(room, UserLeft)` 给留下的人,若房间已销毁则 dispatch 自动跳过,见 [connection.md](connection.md)。
- 对局进行中离开:复用断线/清理那一套。在手牌里则自动弃牌,手结束后再真正释座。见 [rules.md](rules.md) ④ 与 [changes/0014](refactor/changes/0014-p1-inhand-lifecycle.md),实现是 `room.leaving` 标记加 `_finalize_hand` 末尾的 `_evict`。

换房 = 先 `LeaveRoom` 再 `JoinRoom`,因为有单房间约束。

## 房间参数配置:`SetSmallBlind` / `SetBuyIn`(任何在房成员)

任何在房成员(包括观战者)都能在运行时调小盲和默认买入。

- 命令是 `SetSmallBlind(amount)` 与 `SetBuyIn(amount)`;大盲由 2×小盲派生。
- 这是改一个既有房间的参数,和动态建房是两回事。它 scoped 在单房间,走 `checkout`/`commit`。
- 详见 [changes/0043](refactor/changes/0043-room-config-commands.md) 与 [0044](refactor/changes/0044-room-config-any-member.md)。

具体约束:

- **无房主**(0044 定)。没有 `owner`/`host` 字段,也没有 0 号位特权,0043 里的「0 号位占座者授权 + `NOT_ROOM_OWNER`」已撤销。这与开局、免盲投票一样,都是 peer 模型(人人平等,无特权角色)。授权条件只有「在房」,不在房则 `NOT_IN_ROOM`——这是命令路由的必然结果,不是权限设计。
- **时机**:仅限两手之间,即 `room.hand is None` 或 `PENDING_START`,否则 `HAND_IN_PROGRESS`,两个命令的规则对称。理由是局中改盲会污染已经锁入本手的下注,所以这是 correctness 门,不是授权门。
- **上下限**:取 `gameconfig.MIN/MAX_SMALL_BLIND` 与 `MIN/MAX_BUY_IN`,由 shell 在进 reduce 之前防护,因为 core 不 import config;越界回 `INVALID_SMALL_BLIND` 或 `INVALID_BUY_IN`。reduce 只兜结构性校验:在房、非局中、正额。
- **产出**:`Broadcast(RoomConfigChanged{small_blind,big_blind,buy_in})`,让全房对齐。不落库,因为房间状态不持久化,重启后回到 `gameconfig` 缺省值;`StateSnapshot` 也带 `buy_in`,所以重连时能看到当前值。

## 红利:游戏命令不再带 `room`

一个用户同时只在一个房间,所以房间号可以推出来,不必进报文。

- `PlayerAction`、`SitDown`、`BuyIn`、房聊等命令都不带 `room`;reduce 用 `world.users[nick].room` 定位目标房,工作副本的 `checkout` 也据此解析。
- 唯一例外是 `JoinRoom`:那时用户还没有当前房,目标房只能写在命令里。
- 身份和房间都不进报文,见 [wire.md](wire.md)。

## 房间列表(REST 读)—— 已落地(0048)

一个只读端点,展示用。

```
POST /lobby/rooms 信封内 {} → {rooms: [RoomMeta]}   # app/rest/lobby.py:list_rooms / make_lobby_router(0094 起走信封)
```

`RoomMeta` 是 `app/rest/lobby.py` 里的 Pydantic 模型,字段分两类:

| 类别 | 字段 | 说明 |
|---|---|---|
| 静态配置 | `id`、`small_blind`、`big_blind`、`buy_in`、`max_seats` | v1 没有独立的 `name` 字段,`id` 就是 `world.rooms` 的键,同时就是人读的房名;`big_blind` 由 2×小盲派生 |
| 实时 | `seated`、`watching`、`status` | `seated` = 占用座位数,即 `len([s for s in room.seats if s])`,包含 OFFLINE 保座;`watching` = `users_in_room` 中处于 `WATCHING` 状态的人数。在座与在线是两个正交维度 |

其它约定:

- 实时头数与状态读的是已 committed 的 `world.rooms[r]`。它只读、只用于展示、允许滞后一拍,不参与实时判定。这是唯一读 `world` 的 REST 端点,见 [storage.md](storage.md) 与 [rest.md](rest.md) §共同原则。
- 投影函数 `list_rooms` 纯同步、无 `await`,所以相对唯一写者 GameLoop 是原子读,不会读到撕裂状态。这与 [presence.md](presence.md) 的只读范式相同。
- v1 由客户端轮询,人数 ≤20,几秒一次足够。实时推送(`LobbyBroadcast`)见「待定」。
- `RoomMeta` 是 REST DTO,不是 `Room`:完整游戏状态(`deck`、`hand`、各人筹码)绝不上 lobby,见 [wire.md](wire.md)。它不进 ws 的 `ServerMessage` 联合,所以也不进 `wire.gen.ts`;前端的 REST DTO 目前**手写**在 [frontend/src/transport/rest.ts](../../frontend/src/transport/rest.ts)(枚举字段仍从 codegen 产物 `wire.gen.ts` 取,不手抄字面量,见 0099)。`openapi-typescript` 管线仍未接——但**原先「本机无 node」的理由自 0077 起不成立**(已装 Node 24);真正的阻塞见 [rest.md](rest.md)「共同原则 4」:0094 之后 OpenAPI 里只剩信封,DTO 在密文内层。
- dev 环境明文、无鉴权,与 dev 的 ws 端点一致:它只暴露房间配置和头数,没有隐私。P5 加密信道上线时,按 [rest.md](rest.md)「REST 走会话密钥信封」补齐;不用 JWT(0057)。

## 改昵称(你的决策:只能不在房间时)—— 已落地(0065)

只有在大厅才能改昵称。

- 判据:nick 不在 `world.users`。在房间内一律拒绝:REST 返回 403;改昵称目前**只有 REST 形态**,在房即 403;**`ErrorCode` 里并没有** `CANT_CHANGE_NICK_IN_ROOM` 这个成员,将来真开 ws 形态时再加。
- 原因:`nickname` 是 `world` 的键,座位、`contributed`、ConnectionManager 都按它索引,正在用的时候改会导致键错乱。
- 落点见 [0065](refactor/changes/0065-p7-change-nickname.md):REST `POST /user/nickname`,走加密信封,直接改 DB、会话表和连接键,不经 reduce。下一次 `JoinRoom` 自然就用新 nick 当键。详见 [rest.md](rest.md) §用户资料。

## 与架构契约(必须守住)

1. 大厅无 `world` 状态:大厅本身只是 ConnectionManager(presence)+ REST;`world.users` = 当前在游戏房的人。
2. 进出房走 `JoinRoom`/`LeaveRoom`,守单房间约束;游戏命令不带 `room`,由用户当前房推定。
3. 房间列表是 REST 读(展示、可滞后),不做实时判定;房间状态不持久化、无静态预置(0049),重启从空 `world.rooms` 起步。
4. 改昵称仅限大厅(不在 `world.users` 时),避免 world 键错乱。

## 待定 / future

- 建房余项。包括:建房时自定盲注/买入/座位,做法是往 `join_room` 报文加 `create` 字段让创建者设参,现在用的是 `gameconfig` 默认值加建后调参;房名冲突与命名规则;建房数量上限。本规模是内网 ≤20 人,暂不设这些限制。
- 实时房间列表推送。新增 `LobbyBroadcast(msg)` 事件,由 dispatch 发给所有大厅连接,即那些 nick 不在 `world.users` 的连接;建房、销房时增量推送。v1 先用轮询 `POST /lobby/rooms`(0048;0094 起走信封)。

- messaging(私聊 + 房聊)与 presence 均已落地,不再是待定:房聊 0021/0033/0036/0071、私聊 0038-0041(见 [messaging.md](messaging.md));presence 只读视图 0037,已单列成 [presence.md](presence.md)。
