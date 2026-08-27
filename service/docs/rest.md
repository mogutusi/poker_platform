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
- 唯一例外 · `POST /lobby/rooms`(见 [lobby.md](lobby.md),0048 落地;0094 收编进信封):
  - 它读 committed `world.rooms`,不读 DB;原因是房间花名册和头数为内存权威、从不落库(见 [storage.md](storage.md)),DB 里根本没有这些数据。
  - 它仍守「只读、可滞后、不做实时裁定」。读法是纯同步、无 `await` 的投影,对唯一写者 GameLoop 而言是原子的,不会读到撕裂的中间态(同 [presence.md](presence.md))。

**2. 请求级 `DBsession`**

- 每请求一个 session,与 PersistWriter 的写 session 互不复用(见 [db.md](db.md));读路径无行锁。

**3. 鉴权走加密信封**(已落地 [0062](refactor/changes/0062-p5-rest-envelope-user-me.md))

- 需身份的端点:请求 `POST {sid, frame}`,响应 `{frame}`;解密即认证,无 JWT。
- 信封格式、密钥分域、滑动窗防重放、seq 回显见 [auth.md](auth.md) §加密信道「REST 信封」;助手在 [app/rest/secure.py](../app/rest/secure.py):`open_request` / `seal_response`。
- **每个端点都走信封,没有例外**(0094)。`POST /user/login` 是唯一暴露在外的入口——它必须明文,因为此刻还没有会话密钥可用(登录本身用 `K_user` 加密一来一回,见 [auth.md](auth.md) §登录握手)。
- 收编前 lobby/leaderboard/hands 是明文 GET,那是 P5 落地之前的残留(执行序被 [0016](refactor/changes/0016-replan-wire-first.md) 重排,读端点先于加密信道落地),不是设计。

**4. wire/DTO 同源**

- REST DTO **没有** TS 生成,和 ws 不同。0094 把每个端点收进信封后,OpenAPI 里只剩 `SecureRequest`/`SecureResponse`——真正的请求/响应形状在密文内层,schema 看不见,`openapi-typescript` 这条路等于关掉了。前端按 `app/rest/*.py` 的字段注释手写(枚举仍从 `wire.gen.ts` 取,见 0099),别塞进 `wire.gen.ts`。

## 排行榜 leaderboard —— 已落地(0050)

一句话:按 DB 里的全局积分排名,桌上的筹码不算。

```
POST /leaderboard  信封内 {limit?}  →  {entries: [{ rank, nickname, points }]}   # app/rest/leaderboard.py + db/queries.top_users_by_points
```

- 读 DB `users` 表,按 `points` 降序取前 N;同分按 `nickname` 升序,保证 `rank` 稳定。
- `limit` 由 `gameconfig.LEADERBOARD_DEFAULT_LIMIT` / `MAX_LIMIT` 兜。
- `LeaderboardEntry` 是 REST DTO,不进 ws 联合、不进 `wire.gen.ts`,同 `RoomMeta`。
- **走信封**(0094):`POST /leaderboard`,内层参数 `{"limit"?: int}`,响应 `{"entries": [...]}`。要登录才看得到。
- `limit` 的范围校验从 FastAPI 的 `Query(ge=, le=)` 挪进了端点自己:参数进了信封,框架就管不着了。越界回 **400**(信封已验过 ⇒ 是客户端 bug,不是鉴权问题),**不默默截断**成合法值。

**坑 · 排的是「结算后的全局积分」,不是身家**

- 买进牌桌的筹码在 `Seat.points`,那是内存、不落库,DB 里没有;所以一个人把积分全买进牌桌后,排行榜只显示他桌下剩余的积分。
- 决策(可改):排行榜定义为银行余额(settled points)。筹码要等离桌(`LeaveRoom`)结算还回全局后才完整体现;这个定义清晰,而且只读 DB 就够。要「含桌上筹码的总身家」就得读 `world`,属于跨切,列为 future。

**DB 滞后**:刚买入/离桌的变更可能还没 flush,排行榜会短暂偏旧。可接受。

## 手牌历史 hand history —— 已落地(0051 + room 过滤 0052)

一句话:从 DB 查已结束的手,只有结果,永远没有底牌。

```
POST /hands  信封内 {room?, user?, before?, limit?}  →  {hands: [HandRecordView]}   (游标分页,新→旧)   # app/rest/hands.py + db/queries.list_hands
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
- **走信封**(0094):`POST /hands`,内层参数 `{room?, user?, before?, limit?}`,响应 `{"hands": [...]}`。要登录才看得到。参数校验同 leaderboard,越界/类型错回 400。
- **授权范围仍是待定项**:登录用户目前**可以查任何人**(`user=` 点名照旧)。0094 只解决「传输裸奔 + 未登录可读」;要不要收紧成「只能查自己」是另一个决定,它会连带决定前端历史页「全部」页签的去留,尚未拍板。

## 用户资料 profile —— /user/me 已落地(0062,首个信封消费者)

一句话:四个带身份的端点,全走加密信封;密码和昵称是同步直写 DB,不走 delayDB。

```
POST  /user/me            →  信封内 { name, nickname, points }   # 已落地(0062):app/rest/profile.py,points 取 DB(滞后)
POST  /user/password      →  信封内 { old_password, new_password } → { status:"ok" }  # 已落地(0064):验旧 → 重算 salt$rounds$digest → 同步直写;成功即吊销别处会话(0097)
POST  /user/nickname      →  信封内 { new_nickname } → { status:"ok", nickname }  # 已落地(0065):仅大厅;DB+会话表+连接键三处联动
POST  /user/logout        →  信封内 { } → { status:"ok" }  # 已落地(0097):吊销当前这一个会话
```

**`/user/me`**

- 信封解开后,身份就是会话里的 `name`;按 name 读 DB 投影 `db/queries.load_profile_by_name`,不带 hash / k_cur / k_prev 这些秘密列,结果用信封封回。
- 信封失败统一 401;信封验过之后的 DB 错或行缺失,如实 500,那不是鉴权问题。

**`/user/password`**(0064)

- 内层参数 `{old_password, new_password}`。步骤:
  1. 先验旧密码(`verify_password`)。这是第二因子,防止盗到 token 的人直接锁死真用户。
  2. 重算 `hash_password(new, PWD_HASH_ROUNDS)`,用新盐。
  3. 同步直写 `db/user_writes.update_password_hash`。鉴权列是 DB 权威、无内存副本,所以不走 delayDB(见 [storage.md](storage.md)「鉴权列写路径」)。
  4. **吊销该账号其它会话**(0097),留下当前这个。
- 错误分层:信封不过 → 401;旧密码错、或该账号未启用 → 403;缺参、新密码空、参数非串 → 400;DB 错、会话 name 查无此行 → 500。
- **改密即吊销其它会话**([0097](refactor/changes/0097-revocation-that-actually-bites.md) 翻掉此前的「v1 不吊销」)。旧说法还附了个错误的前提——「撤销需要 name→sessions 索引」:不需要,同类的 `rename_nickname` 一直是线性扫 `_by_id`,在线 ≤20 的规模下再建索引只是多一份要维护的事实源。吊销会就地判死 `Session` 对象,所以别处那些设备的活 ws 在下一帧被 4401 关掉(见 [auth.md](auth.md) §吊销)。失败(旧密码错)不吊销任何东西。

**`/user/logout`**(0097)

- 内层参数 `{}`,响应 `{"status": "ok"}`。吊销发起方自己那一个会话——信封验过 ⇒ `sid` 就是被认证的会话句柄,不需要也不接受「吊销谁」这种参数。
- 只吊销自己这一个,不是「退出所有设备」;后者是改密码的语义。
- 错误分层:信封不过(含 sid 未知/已吊销/已过期)→ 401;此外没有失败臂,幂等。
- **先封响应再吊销**:响应用会话密钥封回,顺序写死才不依赖「seal 恰好不查 exp」这个巧合。

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
- 日后若上 wss(反代 + 自动证书),这套应用层信封可整套拆除。**REST 加密本身早已不是待定**:0062 建机制、0094 全量收编,见本文开头「共同原则」。
