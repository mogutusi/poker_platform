# REST 查询面与用户资料(rest)

## 定位

一句话:排行榜、手牌历史、用户资料这类事后/聚合查询,加上账号管理,走 REST,只碰 DB,不进 WS / reduce / world。

它们是 [connection.md](connection.md) 里 C 落点(REST 读 DB)的集合。

> 前置:
>
> - [db.md](db.md):读路径走请求级 `DBsession`,DB 比内存滞后。
> - [auth.md](auth.md):REST 走会话密钥加密信封,见 changes/0057。
> - [storage.md](storage.md):内存权威 + delayDB。「delayDB」= 内存里的状态是权威,落库是异步延迟写的,所以 DB 永远慢一拍。

## 共同原则(三个模块都守)

一句话:读 DB 不读 `world`、每请求一个 session、带身份就走加密信封、DTO 同源生成。

**1. 读 DB,不读 `world`**

- 排行榜/历史/资料读的是 delayDB 落库后的值,比内存滞后,展示够用。实时判定(下注、余额)一律在 reduce 里以内存为准,不在 REST。
- 唯一例外 · `GET /lobby/rooms`(见 [lobby.md](lobby.md),0048 落地):
  - 它读 committed `world.rooms`,不读 DB;原因是房间花名册和头数为内存权威、从不落库(见 [storage.md](storage.md)),DB 里根本没有这些数据。
  - 它仍守「只读、可滞后、不做实时裁定」。读法是纯同步、无 `await` 的投影,对唯一写者 GameLoop 而言是原子的,不会读到撕裂的中间态(同 [presence.md](presence.md))。

**2. 请求级 `DBsession`**

- 每请求一个 session,与 PersistWriter 的写 session 互不复用(见 [db.md](db.md));读路径无行锁。

**3. 鉴权走加密信封**(已落地 [0062](refactor/changes/0062-p5-rest-envelope-user-me.md))

- 需身份的端点:请求 `POST {sid, frame}`,响应 `{frame}`;解密即认证,无 JWT。
- 信封格式、密钥分域、滑动窗防重放、seq 回显见 [auth.md](auth.md) §加密信道「REST 信封」;助手在 [app/rest/secure.py](../app/rest/secure.py):`open_request` / `seal_response`。
- 公开读(本页 lobby/leaderboard/hands)无隐私,留明文。要全量加密再收编。

**4. wire/DTO 同源**

- REST 响应模型也是 Pydantic,经 OpenAPI → TS 生成,前端不手写(见 [wire.md](wire.md))。

## 排行榜 leaderboard —— 已落地(0050)

一句话:按 DB 里的全局积分排名,桌上的筹码不算。

```
GET /leaderboard?limit=N  →  [{ rank, nickname, points }]   # app/rest/leaderboard.py + db/queries.top_users_by_points
```

- 读 DB `users` 表,按 `points` 降序取前 N;同分按 `nickname` 升序,保证 `rank` 稳定。
- `limit` 由 `gameconfig.LEADERBOARD_DEFAULT_LIMIT` / `MAX_LIMIT` 兜。
- `LeaderboardEntry` 是 REST DTO,不进 ws 联合、不进 `wire.gen.ts`,同 `RoomMeta`。
- dev 无鉴权,排名公开。P5 上加密信道时按「共同原则 3」补,补的时候可以选择留公开。

**坑 · 排的是「结算后的全局积分」,不是身家**

- 买进牌桌的筹码在 `Seat.points`,那是内存、不落库,DB 里没有;所以一个人把积分全买进牌桌后,排行榜只显示他桌下剩余的积分。
- 决策(可改):排行榜定义为银行余额(settled points)。筹码要等离桌(`LeaveRoom`)结算还回全局后才完整体现;这个定义清晰,而且只读 DB 就够。要「含桌上筹码的总身家」就得读 `world`,属于跨切,列为 future。

**DB 滞后**:刚买入/离桌的变更可能还没 flush,排行榜会短暂偏旧。可接受。

## 手牌历史 hand history —— 已落地(0051 + room 过滤 0052)

一句话:从 DB 查已结束的手,只有结果,永远没有底牌。

```
GET /hands?room=&user=&limit=&before=  →  [HandRecordView]   (游标分页,新→旧)   # app/rest/hands.py + db/queries.list_hands
```

**数据来源**

- 读 DB 手牌记录,由 delayDB 的事件写追加(见 [db.md](db.md))。表在 [`app/db/models.py`](../app/db/models.py):`HandRecord` / `HandParticipant`,对齐 `HandRecordWrite` / `ParticipantWrite`。
- 响应 DTO:`HandRecordView{id, dedupe_key, start_time, end_time, final_pot, participants:[{nickname, initial_points, final_points, net}]}`。它是 REST DTO,不进 ws 联合。
- 请求级 session。一会话两查(先查手,再查参与者 join User 取 nick)以避 N+1。

**`user` 过滤**:按参与者过滤,`?user=<nick>` → 解析 uid → 只返回该玩家参与过的手;返回的每手仍含该手全部参与者;nick 不存在 → 空。

**`room` 过滤**(0052):`?room=<name>` → `WHERE HandRecord.room == name`,精确匹配,可与 `user` / `before` 组合。

- 早先 room 只存在于 `dedupe_key="room:seq"` 里,用 `dedupe_key LIKE` 匹配对动态房名(可能含通配符或 `:`)很脆弱,所以 0051 推迟了这个过滤。
- 0052 给 `HandRecord` 加了一列 denormalized `room` 兑现它。改动涉及 `HandRecordWrite` + reduce + orm_persister + 迁移 `010d8e8a08d7`(见 [changes/0052](refactor/changes/0052-handrecord-room-column.md))。

**隐私**

- 记录只存结果,`hole_cards` / `deck` 从不落库(见 [core.md](core.md) 不变量 3 / [log.md](log.md));所以历史看得到输赢,看不到底牌。
- 两表结构上就没有牌面字段,查询天然不泄。

**分页用 `before`(游标),不用 OFFSET**

- 游标 = `HandRecord.id`,自增 PK,单调唯一;事件写按手尾追加。比用 `end_time` 好,免了并列问题。
- `before=<id>` → `id < before ORDER BY id DESC LIMIT n`;`id` 兼作「下一页游标」。
- `limit` 由 `gameconfig.HANDS_DEFAULT_LIMIT` / `MAX_LIMIT` 兜。
- dev 无鉴权。上加密信道时可以改成要求仅查自己。

## 用户资料 profile —— /user/me 已落地(0062,首个信封消费者)

一句话:三个带身份的端点,全走加密信封;密码和昵称是同步直写 DB,不走 delayDB。

```
POST  /user/me            →  信封内 { name, nickname, points }   # 已落地(0062):app/rest/profile.py,points 取 DB(滞后)
POST  /user/password      →  信封内 { old_password, new_password } → { status:"ok" }  # 已落地(0064):验旧 → 重算 salt$rounds$digest → 同步直写
POST  /user/nickname      →  信封内 { new_nickname } → { status:"ok", nickname }  # 已落地(0065):仅大厅;DB+会话表+连接键三处联动
```

**`/user/me`**

- 信封解开后,身份就是会话里的 `name`;按 name 读 DB 投影 `db/queries.load_profile_by_name`,不带 hash / k_cur / k_prev 这些秘密列,结果用信封封回。
- 信封失败统一 401;信封验过之后的 DB 错或行缺失,如实 500,那不是鉴权问题。

**`/user/password`**(0064)

- 内层参数 `{old_password, new_password}`。步骤:
  1. 先验旧密码(`verify_password`)。这是第二因子,防止盗到 token 的人直接锁死真用户。
  2. 重算 `hash_password(new, PWD_HASH_ROUNDS)`,用新盐。
  3. 同步直写 `db/user_writes.update_password_hash`。鉴权列是 DB 权威、无内存副本,所以不走 delayDB(见 [storage.md](storage.md)「鉴权列写路径」)。
- 错误分层:信封不过 → 401;旧密码错、或该账号未启用 → 403;缺参、新密码空、参数非串 → 400;DB 错、会话 name 查无此行 → 500。
- v1 不吊销其它会话。改密码只防未来登录,现有已认证会话仍有效;撤销需要 name→sessions 索引,记为 future。

**`points`**

- 取 DB,滞后。精确余额在 ws:`StateSnapshot` 和买入广播给的是内存权威值。大厅展示用 DB 近似值即可。

**改昵称:仅当用户不在任何房间**(已落地 0065,`make_nickname_router`)

- 原因:`nickname` 是 `world` 的键,座位、`contributed`、ConnectionManager 全按它索引,在用时改会让键错乱;大厅用户不在 `world.users`,改它安全。
- 判定「是否在房」+ 连接重挂的完整机制见 [presence.md](presence.md),流程是:
  1. `current_room(old_nick) is None`(即在大厅)才允许。`old_nick` 以 DB 为准,因为会话表可能滞后。
  2. CAS 同步直写 DB:`user_writes.update_nickname`,条件 `WHERE id=uid AND nickname=old_nick`。「CAS」= 只在旧值仍是预期值时才写。同账号并发双改名只有一个赢,输的那个 0 命中 → 409,并跳过内存联动,防止 DB / 会话表 / 连接键三处发散。
  3. `SessionStore.rename_nickname`,改该账号的全部会话。
  4. 有 live 连接则 `ConnectionManager.rekey(conn, new)`。**连接在全部 await 之后才查**(0083):窗前捕获的引用可能已被 ws 顶替、成了死对象,重挂它等于只改一个没人用的对象、把真正的活连接永久留在旧键下。防「误挂他人连接」的责任改由归属校验承担——加密连接比会话账号名,dev 明文连接比 `session_id`(dev 端点建连时就把它盖成握手用的 nick);`rekey` 自身的 `is` 判定作为第二道。
  - 后两步纯同步、无 await,所以是原子的;DB 失败或 CAS 输,则内存完全未动。
- 唯一性:`nickname` 全局唯一,两道防线——预查 `nickname_taken` → 409;写时唯一约束兜底,`IntegrityError` → 409,它盖住预查与写之间的 await 窗。
- 错误分层(承 0062/0064):信封不过 → 401;在房 → 403;撞名、CAS 输 → 409;同名、空、首尾空白(` Bob` 这种冒充面)、超长(>50)、非串 → 400;DB 错、presence 未接线 → 500。

## 与架构契约(必须守住)

1. REST 只读/写 DB,不碰 `world`;实时判定一律以内存为准(REST 给的是滞后视图)。
2. 排行榜 = 结算后的全局积分(桌上筹码不计,room 不落库);要含桌上筹码是 future(需读 world)。
3. 历史只存结果、绝无底牌(`hole_cards`/`deck` 不落库)。
4. 改昵称仅限大厅(不在 `world.users`),改后重挂连接 nick 键 + 更新会话表。
5. REST 与 ws 走同一加密信封(会话密钥,无 JWT);请求级 `DBsession`、无行锁。
6. 鉴权列(密码等)同步直写、不走 delayDB(DB 权威、无内存副本;与 PersistWriter 列不相交 ⇒ 仍无锁,见 [storage.md](storage.md)「鉴权列写路径」/ [changes/0064](refactor/changes/0064-p7-change-password.md))。

## 待定 / future

- 排行榜含桌上筹码的「总身家」:需读 `world`,或把 `in_game_points` 也落库。本规模先不做。
- 统计维度:胜率、手数、盈亏曲线。基于手牌记录聚合,加查询即可。
- 资料扩展:头像、签名等。非游戏权威字段放 DB,不进 `UserState`(见 [user.md](user.md))。
- REST 加密:走会话密钥信封(见 [changes/0057](refactor/changes/0057-p5-unified-encrypted-channel-design.md))。日后上 wss 可整套拆除。
