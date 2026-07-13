# REST 查询面与用户资料(rest)

## 定位

排行榜、手牌历史、用户资料这类**事后/聚合查询 + 账号管理**走 REST,不进 WS / reduce / world。它们是 [connection.md](connection.md) 里 **C 落点**(REST 读 DB)的集合。

> 前置:[db.md](db.md)(读路径走请求级 `DBsession`、DB 比内存滞后)、[auth.md](auth.md)(REST 走会话密钥加密信封,与 ws 同,见 changes/0057)、[storage.md](storage.md)(内存权威 + delayDB)。

## 共同原则(三个模块都守)

1. **读 DB,不读 `world`(本篇三模块)**:排行榜/历史/资料读的是 delayDB 落库后的值,**比内存滞后**——展示完全够用;**实时判定一律以内存为准**(下注、余额够不够在 reduce 里判,不在 REST)。
   - **唯一例外 · `GET /lobby/rooms`**(见 [lobby.md](lobby.md),0048 落地):它读 **committed `world.rooms`**,不读 DB——因为**房间花名册/头数是内存权威、从不落库**(storage.md:房态不持久,DB 里根本没有),与这三个「读结算后落库数据」的模块正交。它仍守「只读、可滞后、不做实时裁定」;读法是纯同步无 `await` 的投影,对唯一写者 GameLoop 原子、不撕裂(同 [presence.md](presence.md))。
2. **请求级 `DBsession`**:每请求一个 session,与 PersistWriter 的写 session 互不复用(见 [db.md](db.md));读路径无行锁。
3. **鉴权走加密信封(已落地 [0062](refactor/changes/0062-p5-rest-envelope-user-me.md))**:需身份的端点请求为 `POST {sid, frame}`、响应 `{frame}`(`frame`=hex(iv‖ct‖mac),REST 域密钥 + 每会话滑动窗防重放 + 响应 seq 回显绑定),**解密即认证、无 JWT**——助手在 [app/rest/secure.py](../app/rest/secure.py)(`open_request`/`seal_response`),见 [auth.md](auth.md) §加密信道「REST 信封」。**公开读(本页 lobby/leaderboard/hands)留明文**(无隐私,明示接受;要全量加密再收编)。
4. **wire/DTO 同源**:REST 响应模型也是 Pydantic,经 OpenAPI → TS 生成,前端不手写(见 [wire.md](wire.md))。

## 排行榜 leaderboard —— 已落地(0050)

```
GET /leaderboard?limit=N  →  [{ rank, nickname, points }]   # app/rest/leaderboard.py + db/queries.top_users_by_points
```

- 读 DB `users` 按 `points` 降序取前 N(同分按 `nickname` 升序 → `rank` 稳定);`limit` 由 `gameconfig.LEADERBOARD_DEFAULT_LIMIT`/`MAX_LIMIT` 兜(默认/上限)。`LeaderboardEntry` 是 REST DTO(不进 ws 联合 / `wire.gen.ts`,同 `RoomMeta`)。请求级 session(查询内 `async with sessionmaker()`)、读路径无行锁。**dev 无鉴权**(排名公开;P5 上加密信道时按「共同原则 3」补,可留公开)。
- **坑 · 排的是"结算后的全局积分",不是身家**:玩家买进牌桌的筹码在 `Seat.points`(内存、不落库),**不在 DB**。所以一个把积分全买进牌桌的人,排行榜上只显示他**桌下剩余的全局积分**。这是因为「room 状态不落库」(见 [storage.md](storage.md))。
  - **决策(可改)**:排行榜定义为**银行余额(settled points)**——离桌(`LeaveRoom`)结算把筹码还回全局后才完整体现。这个定义清晰、且只读 DB 就够;若要"含桌上筹码的总身家",得读 `world`(跨切),列为 future。
- DB 滞后:刚买入/离桌的变更可能还没 flush,排行榜短暂偏旧——可接受。

## 手牌历史 hand history —— 已落地(0051 + room 过滤 0052)

```
GET /hands?room=&user=&limit=&before=  →  [HandRecordView]   (游标分页,新→旧)   # app/rest/hands.py + db/queries.list_hands
```

- 读 DB 的手牌记录(由 delayDB **事件写**追加,见 [db.md](db.md));读 [`app/db/models.py`](../app/db/models.py) 的 `HandRecord`/`HandParticipant` 表(对齐 `HandRecordWrite`/`ParticipantWrite`)。`HandRecordView{id, dedupe_key, start_time, end_time, final_pot, participants:[{nickname, initial_points, final_points, net}]}`(REST DTO,不进 ws 联合 / `wire.gen.ts`,同 `RoomMeta`/`LeaderboardEntry`)。请求级 session、一会话两查(手 + 其参与者 join User 取 nick)避 N+1。
- **`user` 过滤**按参与者:`?user=<nick>` → 解析 uid → 只返回该玩家参与过的手(仍含该手全部参与者);nick 不存在 → 空。
- **`room` 过滤**(0052):`?room=<name>` → `WHERE HandRecord.room == name` 精确匹配(`HandRecord` 加了 denormalized `room` 列 + 迁移 `010d8e8a08d7`;**免** `dedupe_key LIKE` 对动态房名的脆弱)。可与 `user`/`before` 组合。
- **隐私**:记录只存**结果**,`hole_cards`/`deck` **从不落库**(见 [core.md](core.md) 不变量 3 / [log.md](log.md))——历史看输赢、看不到底牌;两表结构上无牌面字段,查询天然不泄。
- 分页用 `before`(游标),不用 OFFSET:游标 = **`HandRecord.id`**(自增 PK,单调唯一,事件写按手尾追加;比 `end_time` 免并列),`before=<id>` → `id < before ORDER BY id DESC LIMIT n`;`id` 兼作 DTO 里「下一页游标」。`limit` 由 `gameconfig.HANDS_DEFAULT_LIMIT`/`MAX_LIMIT` 兜。**dev 无鉴权**(P5 上加密信道时可要求仅查自己)。
- **room 列由来**(0052):早先 `HandRecord` 无 `room` 列、room 仅在 `dedupe_key="room:seq"`,`dedupe_key LIKE` 对动态房名(通配符/`:`)脆弱,故 0051 推迟 room 过滤;0052 给 `HandRecord` 加 denormalized `room` 列(改 `HandRecordWrite`+reduce+orm_persister+迁移 `010d8e8a08d7`)兑现之(见 [changes/0052](refactor/changes/0052-handrecord-room-column.md))。

## 用户资料 profile —— /user/me 已落地(0062,首个信封消费者)

```
POST  /user/me            →  信封内 { name, nickname, points }   # 已落地(0062):app/rest/profile.py,points 取 DB(滞后)
POST  /user/password      →  信封内 { old_password, new_password } → { status:"ok" }  # 已落地(0064):验旧 → 重算 salt$rounds$digest → 同步直写
POST  /user/nickname      →  信封内 { new_nickname } → { status:"ok", nickname }  # 已落地(0065):仅大厅;DB+会话表+连接键三处联动
```

- **`/user/me` 走加密信封**(共同原则 3):`POST {sid, frame}`(`/user/me` 无参,内层 `{}`)→ 身份 = 会话 `name` → 读 DB 投影(`db/queries.load_profile_by_name`,**不带** hash/k_cur/k_prev 秘密列)→ 信封封回。信封失败统一 401;信封验过后的 DB 错/行缺失如实 500(非鉴权问题)。
- **`/user/password` 走加密信封(0064)**:内层 `{old_password, new_password}`→ 身份 = 会话 `name` → **验旧密码**(第二因子,专防盗 token 锁死真用户;`verify_password`)→ 重算 `hash_password(new, PWD_HASH_ROUNDS)`(新盐)→ **同步直写** `db/user_writes.update_password_hash`(鉴权列 DB 权威、无内存副本,不走 delayDB,见 [storage.md](storage.md)「鉴权列写路径」)。**错误分层**:信封不过 401;旧密码错/未启用 **403**;缺参/新密码空/参数非串 **400**;DB 错/会话 name 无行 500。**v1 不吊销其它会话**(改密码防未来登录,现有已认证会话仍有效;撤销需 name→sessions 索引,记为 future)。
- **`points`** 取 DB(滞后);**精确余额在 ws**(进房后 `StateSnapshot` / 买入广播给的是内存权威值)。大厅展示用 DB 近似值即可。
- **改昵称:仅当用户不在任何房间**(你的决策;**已落地 0065**,`make_nickname_router`)。`nickname` 是 `world` 的键(座位/`contributed`/ConnectionManager 全按它),在用时改会让键错乱;大厅用户不在 `world.users`,改它安全。
  - **判定"是否在房" + 连接重挂的完整机制见 [presence.md](presence.md)**:`current_room(old_nick) is None`(在大厅)才允许(old_nick 以 **DB** 为准,会话表可能滞后)→ **CAS** 同步直写 DB(`user_writes.update_nickname`,`WHERE id=uid AND nickname=old_nick`——同账号并发双改名只有一个赢,输者 0 命中 → 409 且**跳过内存联动**,防 DB/会话表/连接键三处发散)→ `SessionStore.rename_nickname`(该账号**全部**会话)→ 有 live 连接则 `ConnectionManager.rekey(conn, new)`(改名 handler 在 await 前捕获的**那个对象**,`is` 判定重挂,防 await 窗内误挂他人连接)。后两步纯同步无 await ⇒ 原子;DB 失败/CAS 输则内存未动。
  - 唯一性:`nickname` 全局唯一——预查(`nickname_taken` → 409)+ 写时唯一约束兜底(`IntegrityError` → 409,预查与写之间的 await 窗)。
  - **错误分层(承 0062/0064)**:信封 401 / 在房 403 / 撞名·CAS 输 409 / 同名·空·**首尾空白**(" Bob" 冒充面)·超长(>50)·非串 400 / DB 错·presence 未接线 500。
- **改密码(已落地 0064)**:重算 `salt$rounds$digest`(见 [auth.md](auth.md)),与传输加密正交;详见上「`/user/password` 走加密信封」。

## 与架构契约(必须守住)

1. **REST 只读/写 DB,不碰 `world`**;实时判定一律以内存为准(REST 给的是滞后视图)。
2. **排行榜 = 结算后的全局积分**(桌上筹码不计,room 不落库);要含桌上筹码是 future(需读 world)。
3. **历史只存结果、绝无底牌**(`hole_cards`/`deck` 不落库)。
4. **改昵称仅限大厅**(不在 `world.users`),改后重挂连接 nick 键 + 更新会话表。
5. **REST 与 ws 走同一加密信封**(会话密钥,无 JWT);请求级 `DBsession`、无行锁。
6. **鉴权列(密码等)同步直写、不走 delayDB**(DB 权威、无内存副本;与 PersistWriter 列不相交 ⇒ 仍无锁,见 [storage.md](storage.md)「鉴权列写路径」/ [changes/0064](refactor/changes/0064-p7-change-password.md))。

## 待定 / future

- **排行榜含桌上筹码的"总身家"**:需读 `world` 或把 `in_game_points` 也落库,本规模先不做。
- **统计维度**:胜率、手数、盈亏曲线——基于手牌记录聚合,加查询即可。
- **资料扩展**:头像、签名等;非游戏权威字段放 DB,不进 `UserState`(见 [user.md](user.md))。
- **REST 加密**:走会话密钥信封(见 [changes/0057](refactor/changes/0057-p5-unified-encrypted-channel-design.md));日后上 wss 可整套拆除。
