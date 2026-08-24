# 用户与全局积分(user 模块)

## 一句话定位

全局积分(`points`)是[存储模型](storage.md)的一个实例:内存权威 + delayDB(延迟落库,内存先改、DB 异步追平)。

- **载入**:进房(`JoinRoom`)时从 DB 读一次,装进 `world.users`(见「生命周期」)。
- **运行**:买入扣分、离桌退分内存即时生效,DB 异步追平。
- **回滚**:无用户专用机制。`reduce` 只改工作副本(GameLoop 为这条命令深拷的临时状态),失败整份丢弃、权威状态不动,即 commit-or-discard,见 [storage.md](storage.md)。

> `UserState` 很小(昵称 + 积分),所以用默认的「整份深拷工作集」,不引入 `uRead`/`uWrite`;日后深拷开销可观,再按 [storage.md](storage.md) 的大实体优化改用 `uRead`(引用读)/ `uWrite`(写时拷)。积分不是真实货币,所以「内存先生效、DB 异步落后」带来的崩溃窗口可以接受。

## 为什么内存权威,而不是 DB 权威

一句话:DB 权威会逼买入在 GameLoop 里 `await` DB,而 reduce 不许 `await`,违反不变量 1/3。

- 积分因此与房间状态同语义:载入一次,此后只改内存,由 delayDB 异步落库,DB 不参与实时判定。
- 买入合法性在改内存之前校验,校验过了就一定成功。
- DB 失败只是落库滞后,由 PersistWriter 重试;旧设计里那条 `BuyInFailed` 回滚命令不存在。

## 数据模型

内存的 `UserState` 与 DB 的 `User` 行不是一回事。

```python
# core —— 纯同步,无 IO
@dataclass
class UserState:
    uid: int                    # 不可变账号主键(= DB User.id);落库按它,不按可变的 nickname
    nickname: str               # 可改游戏昵称;是 world.users 的键,只能在大厅改(见 lobby.md)
    points: int                 # 全局积分余额,内存权威
    room: str                   # 当前所在房间(见「单房间约束」)
    # …其它需要内存权威的全局字段后续在此扩展

class World:
    rooms: dict[str, Room]
    users: dict[str, UserState]     # nickname -> 内存权威;由 JoinRoom 载入
```

| | 内存的 `UserState` | DB 的 `User` 行 |
|---|---|---|
| 位置 | `world` | SQLModel 表 |
| 谁读写 | core | PersistWriter 落库 |
| 放什么 | 只放需要权威的字段 | 放鉴权字段 |

core 只碰 `UserState`,`Persist` 是两者唯一的过桥点。`hash_password`、`k_cur`(即 K_user)、`session_token` 是鉴权秘密,不进 `UserState`,见 [auth.md](auth.md)。

## 在 reduce 里怎么改积分(买入为例)

GameLoop 已把 `users` 表一起深拷进工作副本,reduce 直接改副本即可。

```python
case BuyIn(seat=s, amount=amt):                 # 模型 2:命令不带 room/nick
    nick = cmd.origin                           # 身份 = 连接绑定的 nick(见 wire.md)
    user = work.users[nick]
    room = work.rooms[user.room]                # 目标房 = 用户当前房(见 lobby.md)
    # 校验:任一不过就 return,副本被丢弃
    if (e := validate_seat(room, nick, s)) is not None:
        return [], e
    if user.points < amt:
        return [], Err(ErrorCode.INSUFFICIENT_POINTS, detail=f"have={user.points} need={amt}")
    # 修改:改的都是工作副本
    user.points -= amt
    room.seats[s].points += amt
    persist = Persist(PointsWrite(uid=user.uid, points=user.points))   # 按不可变 uid,带快照值
    return [Broadcast(room=user.room, msg=PlayerBuyIn(...)), persist], None
```

「模型 2」指连接模型 2:连接绑定用户,房间只是连接里的频道,所以命令报文里不带 `room`、也不带昵称,见 [wire.md](wire.md)。

要点:

- 失败安全来自「副本被丢弃」而非校验顺序;校验写在修改之前只是为了读着清晰。
- `user` 和 `room` 在同一份副本里,一起 commit 或一起 discard,不存在「user 改了、room 没改」的中间态。
- 只读命令直接读 `work.users[nick]`,不产出 `Persist`;`Persist` 带快照值而非引用,`PointsWrite` 装的是一个 `int`。

## 账号从哪来:**内部注册,没有开户接口**(用户定案,0086)

**这是有意的设计,不是缺口——别去实现「注册端点」。** 开户由内部人**直接往 `user` 表插一行**完成(`name` 登录名、`nickname` 昵称、`hash_password`、`k_cur`);dev 环境下这件事由 `seed_dev_users` 幂等做掉。`K_user` 由管理员 CLI `scripts/kuser_admin.py issue` 生成并**带外**交给用户(新钥进日志会破脱敏红线,见 [auth.md](auth.md) / [changes/0066](refactor/changes/0066-p5-kuser-rotation.md))。

由此推出两条,读代码时不要误判成 bug:

- **登录端点不创建用户**:`POST /user/login` 查不到 `name` 就 401,与密码错、blob 坏一律不区分(fail-closed,见 [changes/0059](refactor/changes/0059-p5-login-endpoint.md))。
- **`world.users` 的载入永远能在 DB 找到行**:`_build_join` 读不到行时回的是 `INTERNAL`「无 DB 账号行」而不是建号——鉴权说有、DB 说无,那是内部不一致,不是新用户。

## 生命周期:何时载入、何时驱逐

载入要读 DB,属于 IO,只能在 shell 做。模型 2 下载入发生在 `JoinRoom` 而非连接握手——大厅用户不进 `world.users`,见 [lobby.md](lobby.md)。

1. **载入**:用户在大厅点某个房间,依次发生四步。
   - Receiver 先过载入屏障:调 `inbox.join()` 等 GameLoop 排空,再调 `PersistWriter.barrier()` 让刚离房的退分写先落库(0073);屏障失败回 `INTERNAL`,不进 reduce。
   - 从 DB 读该 nick 的 `uid` 与 `points`,再投 `JoinRoom(room, uid, loaded=points)` 进 `inbox`。
2. **安装由 reduce 决定**(shell 不读 `world`,以守不变量 2)。
   - `nick` 不在 `work.users`:安装 `UserState(uid, nickname=nick, points=loaded, room=cmd.room)`,加入房间并置 `WATCHING`,私发 `Personal(StateSnapshot)`。
   - `nick` 已在:回 `Err(ALREADY_IN_ROOM)`,要求先 `LeaveRoom`(单房间约束)。
   - 重连(`Connect`)不载入:它只恢复已在 `world.users`、此前为 `OFFLINE` 的用户。此时内存比 DB 新,不能用 DB 覆盖,见 [connection.md](connection.md)。
3. **驱逐**:发生在 `LeaveRoom`、`Cleanup` 退完分、观战者 `Disconnect`(0070)三种时点。
   - reduce 先产出最后一笔退分 `Persist`(观战者无座无分,直接驱逐),再 `del work.users[nick]`,用户回大厅。
   - 在座者断线不立即驱逐:`OFFLINE` 期间座位筹码与 `UserState` 都保留,断线满 `LIVENESS_TIMEOUT` 才投 `Cleanup`、那时退分 + 驱逐,窗口内重连原样恢复,见 [timer.md](timer.md)。观战者断线即时驱逐(0070)。

## 出入口窄:只在买入 / 腾座动全局积分

对局内的筹码流转不碰全局积分,所以出入口窄、容易审计。

- 下注、底池、结算全走房间内积分:`Hand` 下注、`Seat.in_game_points`、手结束还回 `Seat.points`,它们不落 DB,也不经 `UserState`。
- **`UserState.points` 只在两类时点变动**:买入(全局 → 座位)与腾座(座位 → 全局)。腾座涵盖 `LeaveRoom`、`Cleanup`、起身 `SetUserStatus(WATCHING)` 三种情况,见 [changes/0015](refactor/changes/0015-p1-seat-buyin.md) 的 `_release_seat`。

## 单房间约束(一个用户只在一个房间)

规则:**一个用户同一时刻只在一个房间**,记在 `UserState.room`。

- 理由:一人多房时,各房的 `Cleanup` 看不到全貌,会误删别房还在用的全局积分。
- 已在 `world.users` 的人再发 `JoinRoom` → `ALREADY_IN_ROOM`,前端要先发 `LeaveRoom`;`Connect` 不带 room,不参与此判定,见 [connection.md](connection.md)。
- 于是驱逐时的 `del work.users[nick]` 没有歧义,不必引用计数,对应 [architecture.md](architecture.md) 不变量 9;日后要支持一人多房,再改成 refcount 驱逐。

## 持久化:交给 [db.md](db.md)

`Persist` 携带积分的最新全量值(装在 `PointsWrite` 里),不是增量。

- delayDB 对同一用户的多条待写可以覆盖,只落最新一条;落库失败由 delayDB 重试,不投回滚命令。
- 手牌记录是另一类 `Persist`,语义是追加,同样走 delayDB。

## 与架构契约(必须守住)

1. DB 读只在 shell,经 `JoinRoom` 带进 core;core 内绝不 `await` DB、不 `import sqlalchemy`。
2. 载入决策在 reduce,shell 不读 `world`;不重载已在内存的实体(重连 `Connect` 不重载)。
3. 改积分只改工作副本,失败丢弃即回滚;`Persist` 带快照值。
4. 全局积分只在买入与腾座时变动;对局内流转走房间内积分,不落 DB。
5. 一个用户只在一个房间(`UserState.room`):已在房再 `JoinRoom` 即拒。

## 注意点

- **落库按 `uid` 不按 `nickname`**。`world.users` 以 nickname 为键,但 nickname 可以在大厅改;DB 主键用不可变的 `uid`,`PointsWrite` 与手牌记录一律带 `uid`,改名后落库行才不会错位。见 [db.md](db.md)。
- **驱逐后重进要过载入屏障**。「不重载」只保护仍在内存的实体;驱逐(`_evict`)之后退分写可能还在缓冲里,直接读 DB 会拿到陈旧值(0072·N1),屏障(0073)让缓冲先落库再读,见 [storage.md](storage.md)「载入屏障」。
- 积分不足回 `ErrorCode.INSUFFICIENT_POINTS`,`detail` 带 `have` 与 `need`,见 [error.md](error.md)。
- 买入上下限、初始赠分走 [config.md](config.md) 的 settings,不写字面量。
- 崩溃窗口:内存已变、`Persist` 还没落库时崩溃,这笔就丢了,本规模接受;重启时从 DB 载入初值。
