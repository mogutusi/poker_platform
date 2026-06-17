# 用户与全局积分(user 模块)

## 一句话定位

**全局积分(`points`)是[存储模型](storage.md)的一个实例:内存权威 + delayDB。** 登录时从 DB 读一次进 `world.users`,之后买入扣分、离桌退分**全在内存即时生效**,DB 只滞后追平。回滚不靠用户专用机制——和房间状态一样,走 [storage.md](storage.md) 的**工作副本 commit-or-discard**:`reduce` 改的是副本,失败整份丢弃、权威没动。

> **`UserState` 现在很小(昵称 + 积分),用默认的「整份深拷工作集」即可,不引入 `uRead`/`uWrite`。** 只有当它日后长大到「每条命令都整份深拷开销可观」时,才按 [storage.md](storage.md) 的大实体优化改用 `uRead`(引用读)/`uWrite`(写时拷),把深拷省到真要写的时候。那是性能优化,不改下面的逻辑。

> 本平台是**积分(point)不是真实货币**,所以「内存先生效、DB 异步落后」成立,崩溃窗口可接受。

## 为什么内存权威,而不是 DB 权威

若积分「以 DB 为权威」,买入这类高频操作就得在 GameLoop 里 `await` DB,直接撞碎不变量 1/3(reduce 不得 `await`)。所以积分必须和房间状态同一套语义:登录从 DB 读一次载入内存,此后改内存 + delayDB 异步落库,DB 不参与实时判定。

这也干掉了旧设计的 `BuyInFailed` 回滚命令:**DB 不再是买入的关卡**,买入的合法性(积分够不够)在改内存**之前**校验,过了就一定成功;DB 失败只是落库滞后,由 PersistWriter 重试,内存始终自洽。

## 数据模型

```python
# core —— 纯同步,无 IO
@dataclass
class UserState:
    uid: int                    # 不可变账号主键(= DB User.id);落库/记录都按它,绝不按可变的 nickname
    nickname: str               # 可改游戏昵称;是 world.users 的键,但只能在大厅改(见 lobby.md)
    points: int                 # 全局积分余额,内存权威
    room: str                   # 当前所在房间;一个用户只在一个房间(见「单房间约束」)
    # …其它需要内存权威的全局字段后续在此扩展

class World:
    rooms: dict[str, Room]
    users: dict[str, UserState]     # nickname -> 内存权威;由 JoinRoom 命令载入(见「生命周期」)
```

**区分两个 User**:内存的 `UserState`(在 `world`,core 读写,只放需要权威的字段如 `points`)vs DB 的 `User` 行(SQLModel,PersistWriter 落库,放鉴权字段等)。core 只碰前者;`Persist` 是唯一过桥点。**别把 `hash_password`/`refresh_token` 放进 `UserState`**——那不是游戏权威状态。

## 在 reduce 里怎么改积分(买入为例)

不需要 `uRead` / `uWrite`。GameLoop 已把 `users` 表一起深拷贝进工作副本,reduce 直接改副本即可——失败丢弃副本,权威自然没动。

```python
case BuyIn(seat=s, amount=amt):                 # 模型 2:命令不带 room/nick
    nick = cmd.origin                           # 身份 = 连接绑定的 nick(见 wire.md)
    user = work.users[nick]
    room = work.rooms[user.room]                # 目标房 = 用户当前房(见 lobby.md)
    # ── 校验:任一不过就 return,工作副本被丢弃,权威 & room 都没动 ──
    if (e := validate_seat(room, nick, s)) is not None:
        return [], e
    if user.points < amt:
        return [], Err(ErrorCode.INSUFFICIENT_POINTS, detail=f"have={user.points} need={amt}")
    # ── 修改:改的都是工作副本 ──
    user.points -= amt
    room.seats[s].points += amt
    persist = Persist(PointsWrite(uid=user.uid, points=user.points))   # 按不可变 uid 落库,带快照值
    return [Broadcast(room=user.room, msg=PlayerBuyIn(...)), persist], None
```

要点:

- **失败安全 = 工作副本被丢弃**,不必把写回放在最后一步、也不必担心「user 改了 room 没改」的中间态——两者在同一份副本里,一起 commit、一起 discard。
- **校验先于修改仍是好习惯**(清晰),但不再是正确性的硬要求(正确性由 discard 保证)。
- **只读命令**(展示余额):直接读 `work.users[nick]`,不产出 `Persist`。
- **`Persist` 带快照值不带引用**:`PointsWrite` 装的是 `int`,天然快照。

## 生命周期:何时载入、何时驱逐

载入是 DB 读(IO),只能在 shell。沿用「外部数据外移进命令」。**模型 2 下载入发生在 `JoinRoom`(进房),不是连接握手**——大厅用户不进 `world.users`(见 [lobby.md](lobby.md)):

1. **载入**:用户在大厅点某房 → Receiver 从 DB 读该 nick 的 `uid` + `points` → 装进 `JoinRoom(room, uid, loaded=points)` 投 `inbox`(`uid`/`points` 都是 shell 读 DB 得到、随命令外移进 core)。
2. **安装由 reduce 决定**(不在 shell 读 `world`,守不变量 2):
   - `nick` **不在** `work.users` → 用 `uid`/`loaded` 安装 `UserState(uid, nickname=nick, points=loaded, room=cmd.room)`,加入房间为 `WATCHING`,私发 `Personal(StateSnapshot)`。
   - `nick` **已在** `work.users` → **拒绝**:`return [], Err(ALREADY_IN_ROOM)`,已在别房,要先 `LeaveRoom`(单房间约束)。
   > 重连(`Connect`)不在此载入:它只恢复已在 `world.users`、之前 `OFFLINE` 的用户(内存比 DB 新,绝不用 DB 覆盖),见 [connection.md](connection.md)。
3. **驱逐**:用户离场(`LeaveRoom` / `Cleanup` 退完分)时,reduce 产出**最后一笔退分 `Persist` 后**,再 `del work.users[nick]`,用户回大厅。因一个用户只在一个房间,这条就是它的彻底离场,**驱逐无歧义**。
   > 断线**不立即驱逐**:`OFFLINE` 期间座位筹码、`UserState` 都保留;等 `LIVENESS_TIMEOUT` 到期投 `Cleanup` 才真正退分 + 驱逐。重连落在窗口内则 `UserState` 安然无恙(见 [timer.md](timer.md))。

## 出入口窄:只在买入/离桌动全局积分

**对局内的筹码流转(下注、底池、结算回座位)不碰全局积分。** `Hand` 里的下注、`Seat.in_game_points`、手牌结束把筹码还回 `Seat.points` 全是**房间内**积分,不落 DB、不经 `UserState`。`UserState.points` **只在买入(扣)和离桌/清理(还)两处变动**——出入口窄,易审计。

## 单房间约束(一个用户只在一个房间)

`UserState` 是**全局**的(键是 nick,跨房间唯一),但全局积分的载入与驱逐都绑在房间事件上(`Connect` 载入、`Cleanup`/`LeaveRoom` 驱逐)。若允许一个用户同时在多个房间,这些房间各自的 `Cleanup` 都看不到全貌——一个房间的清理会把别的房间还在用的全局积分误删,载入决策也会乱。

所以**规定:一个用户同一时刻只在一个房间**,落在 `UserState.room`:

- `Connect` 到 `UserState.room` 之外的房间 → reduce 直接拒(`ALREADY_IN_ROOM`),前端先离开当前房间再进下一个。
- 于是该用户的彻底离场**只有一个来源**(它所在房间的 `Cleanup`/`LeaveRoom`),驱逐 `del work.users[nick]` 无歧义,也不必引用计数。

> 这条约束是当前规模下的简化(对应 [architecture.md](architecture.md) 不变量 9)。日后真要支持"一人多房"再改成 refcount 驱逐,但本规模无必要。

## 持久化:交给 [db.md](db.md)

- 产出的 `Persist` 携带积分的**最新全量值**(`PointsWrite`),不是增量 → delayDB 对同一用户多条待写**可覆盖、只落最新**。
- 落库失败由 delayDB 重试,**绝不投回滚命令**——内存权威是对的,DB 只是没追上。
- 手牌结束的手牌记录是另一类 `Persist`(**追加**语义),同样走 delayDB。

## 与架构契约(必须守住)

1. **DB 读只在 shell,经 `Connect` 命令把数据带进 core**;core 内绝不 `await` DB / `import sqlalchemy`。
2. **载入决策在 reduce**(判 `nick` 是否已在 `work.users`),shell 不读 `world`;**绝不重载已在内存的实体**。
3. **改积分只改工作副本**(GameLoop 已深拷贝 `users` 表),失败丢弃即回滚,无需用户专用机制。
4. **积分校验先于修改**(好习惯),`Persist` 带快照值。
5. **全局积分只在买入/离桌两处变动**,对局内流转走房间内积分,不落 DB。
6. **一个用户只在一个房间**(`UserState.room`):`Connect` 到别房即拒,驱逐无歧义、不必引用计数。

## 注意点

- **落库按 `uid` 不按 `nickname`**:`world.users` 用 nickname 当键(座位/路由都按它),但 nickname 可在大厅改;DB 主键必须用不可变的 `uid`(= `User.id`)。`PointsWrite`/手牌记录一律带 `uid`,所以大厅改名后落库行不会错位(见 [db.md](db.md))。
- **重连不重载**:安装的前提永远是「`nick` 不在 `work.users`」,否则覆盖未落库的内存变更。
- **驱逐要等最后一笔 Persist 产出后**:先产出退分 `Persist` 再 `del`,顺序不能反。
- **错误码**:积分不足用 `ErrorCode.INSUFFICIENT_POINTS`,`detail` 带 `have`/`need`,绝不裸字符串(见 [error.md](error.md))。
- **可调参数进配置**:买入上下限、初始赠分一律走 [config.md](config.md) 的 settings,不写字面量。
- **崩溃窗口**:内存已变、`Persist` 未落库时崩溃 ⇒ 这笔丢失。积分非货币,本规模接受;重启从 DB 载入初值即可。
