# 0051 · P7 REST:`GET /hands`(手牌历史,游标分页,读 DB)

日期:2026-07-01 · 范围:`app/db/queries.py`(`list_hands`)、新建 `app/rest/hands.py`、`app/shell/lifespan.py`(挂路由)、`app/gameconfig.py` + `app/poker.env.example`(HANDS limit)、`tests/rest/test_hands.py`(新建)、`docs/rest.md`/`docs/refactor/TODO.md`。落地 [rest.md](../../rest.md) §手牌历史 —— P7 REST 第三个切片(承 0048/0050)。

## 背景 / 为什么

P7 REST 未阻塞切片续推:0048 lobby-rooms(读 world)、0050 leaderboard(读 DB)。本批 `GET /hands`——第二个读 DB 的 REST,给玩家查自己的**手牌历史 + 游标分页**。读 `HandRecord`/`HandParticipant`(0026 表,delayDB 事件写追加);**隐私内建**:两表只存结果(uid + 初/末筹码 + 池额),`hole_cards`/`deck` 从不落库(core.md 不变量 3 / models.py),故历史看输赢、看不到底牌。

## 关键设计决策

1. **游标 = `HandRecord.id`(自增 PK),非 `end_time`**:rest.md 原提「按 seq / end_time」,但 `id` 单调(事件写按手结束序追加)+ **唯一**(无并列),`before=<id>` → `WHERE id < before ORDER BY id DESC LIMIT n`,是最简且无 OFFSET 的正确游标;`end_time` 会并列需二级排序键。id 兼作 DTO 里的「下一页游标」。
2. **`user` 过滤按参与者 uid**:`?user=<nick>` → 先 `load_user_by_nick` 解析 uid → `HandRecord.id IN (SELECT hand_id FROM handparticipant WHERE uid=?)`。nick 不存在 → 返回空(无此人 = 无手)。这是主用例(查我的手牌)。
3. **`room` 过滤本批不做(推迟,记原因)**:`HandRecord` **无 room 列**,room 只编码在 `dedupe_key="room:seq"`。动态房(0049)房名任意 → `dedupe_key LIKE room||':%'` 既受 LIKE 通配符(`%`/`_`)注入之扰、又对含 `:` 的房名歧义,**脆弱**。正确解 = 给 `HandRecord` 加独立 `room` 列(改 `HandRecordWrite` + reduce `_finalize_hand` + orm_persister + Alembic 迁移),属**写路径 + schema** 变更,值得单独一篇——本批只做读侧,`room` 过滤留待后续(rest.md/TODO 记)。
4. **DTO 是 REST 专属**(不进 ws 联合 / `wire.gen.ts`,同 RoomMeta/LeaderboardEntry):`HandRecordView{id, dedupe_key, start_time, end_time, final_pot, participants:[HandParticipantView{nickname, initial_points, final_points, net}]}`;`net = final - initial`(便利:本手该玩家盈亏)。参与者按 nickname 升序稳定输出。
5. **请求级 session、一会话装全**:`list_hands` 内一个 `async with sessionmaker()` 做「查手(过滤/游标/limit)→ 查这批手的参与者(join User 取 nick)→ 组装」两查,避免 N+1 也不撑爆笛卡尔积。时间戳 `_as_utc`(sqlite 读回 naive 补 UTC,同 0040)。
6. **limit 入 gameconfig**:`HANDS_DEFAULT_LIMIT`/`HANDS_MAX_LIMIT` + poker.env.example(不留裸字面量);`before` 为 `int|None`(游标)。
7. **dev 无鉴权**(同 lobby-rooms/leaderboard);P5 上 JWT 时按 rest.md「REST 走 JWT」补(手牌历史可要求登录/仅查自己)。可测同前:query 直测种子 DB、route 直接 await、`create_app` 布线。

## 打算改什么(开工前)

- `app/db/queries.py`:`list_hands(sessionmaker, *, participant_uid=None, before_id=None, limit) -> list[tuple]`(每项 `(id, dedupe_key, start, end, final_pot, participants)`,participants 为 `tuple[(nick, initial, final)...]` 按 nick 升序;`_as_utc` 补 tz)。
- 新建 `app/rest/hands.py`:`HandParticipantView` + `HandRecordView` + `make_hands_router(get_sessionmaker)`(`GET /hands?user=&limit=&before=`,解析 user→uid,组 DTO,net 派生)。
- `app/shell/lifespan.py`:`include_router(make_hands_router(lambda: shell.sessionmaker))`。
- `app/gameconfig.py` + `app/poker.env.example`:`HANDS_DEFAULT_LIMIT`/`HANDS_MAX_LIMIT`。
- 新建 `tests/rest/test_hands.py`:query(全量新→旧 / user 过滤 / before 游标 / limit / 参与者组装+排序+net / 空)/ route(user 参数 + 游标 + DTO)/ 布线。
- 文档:rest.md §手牌历史(标落地 + 游标=id + room 过滤推迟原因 + 隐私)、TODO P7(hands 划删,注 room 过滤 future)。

## 实际改了什么

- **`app/db/queries.py`**:import 加 `HandParticipant`/`HandRecord`;`list_hands(sessionmaker, *, participant_uid=None, before_id=None, limit)`——一会话两查:① 查手(可选 `id IN (select hand_id where uid=participant_uid)` + `id < before_id` + `ORDER BY id DESC LIMIT n`)② 查这批手的参与者(`join User` 取 nick);组装 `(id, dedupe_key, start, end, final_pot, participants)`,participants 按 nick 升序、时间 `_as_utc` 补 tz。
- **`app/rest/hands.py`**(新建):`HandParticipantView{nickname, initial_points, final_points, net}` + `HandRecordView{id, dedupe_key, start_time, end_time, final_pot, participants}` + `make_hands_router(get_sessionmaker)`(`GET /hands?user=&before=&limit=`:解析 user→uid[无则空]、组 DTO、`net=final-initial`)。
- **`app/shell/lifespan.py`**:import + `include_router(make_hands_router(lambda: shell.sessionmaker))`。
- **`app/gameconfig.py`** + **`app/poker.env.example`**:`HANDS_DEFAULT_LIMIT`(50)/`HANDS_MAX_LIMIT`(200)。**`tests/test_gameconfig.py`**:`_valid_kwargs` 补两字段(涟漪)。
- **`tests/rest/test_hands.py`**(新建,9):查询(新→旧 / user 过滤 / before 游标严格< / limit / 参与者组装+nick 升序 / 空)+ 路由(user+net DTO / 未知用户空)+ 布线;`_seeded()` 播种父行(User/HandRecord)后 `await s.flush()` 再插 HandParticipant(满足 FK,sqlite foreign_keys=ON 即时校验)。
- **docs**:rest.md §手牌历史(标落地 + 游标=id + user 过滤 + 隐私 + room 过滤推迟原因)、TODO P7(hands 划删 + room 过滤 future 注)。
- **未改**:core / wire / world / reduce / DB schema(**无迁移**——room 过滤推迟正是为避免本批加列);`wire.gen.ts`(DTO 不进 ws 联合,grep=0)。

459 全绿(450→459,+9;含 gameconfig 涟漪修 1);`gen_wire_ts --check` OK。

## 自 review

对照 review.md 逐维(低风险:只读 DB 查询 + REST 投影,无 money/state 变更):

- **① 分层 / 不变量**:app/rest **只读 DB、不碰 world/reduce**;查询在 `db/queries.py`(read 路径)。core/wire 不 import app/rest(承 0048/0050);app/rest 经 `get_sessionmaker` 与 shell 解耦。
- **② 代码↔文档同步**:rest.md §手牌历史字段/游标/user 过滤/隐私/room 推迟 与代码一致;TODO 划删 + room future 注。
- **③ 文档↔文档一致**:rest.md §手牌历史(读 DB)↔ §共同原则 1(0048 调和)一致;隐私「不含底牌」↔ core.md 不变量 3 / models.py 注释一致;room 推迟三处同述(rest.md/TODO/本记录)。
- **④ 数据模型**:DTO 类型忠实;`net=final-initial`(便利派生,非存储);游标 `id`(单调唯一 PK)选型正确、DTO 暴露 id 供下一页。
- **⑤ 规范合规**:注释讲为什么;无裸字面量(limit 入 gameconfig);room 过滤的**脆弱性**显式记为不做的理由(非遗漏)。
- **⑥ 测试充分**:查询六臂(新旧序/user/游标/limit/参与者组装+排序/空)+ 路由三臂(user+net/未知用户空/布线);游标**严格<**(before=3→[2,1],不含 3)钉死;参与者组装含**对手**(user=alice 的 hand 仍返回 carol)+ nick 升序 + net。**未覆盖**:FastAPI `Query(ge=1)` 边界拒(需 HTTP client,httpx 未装);`room` 过滤(本批不做)。
- **⑦ 流程账本**:打算↔实际一致(含明确推迟 room 过滤 + 理由);TODO 划删;提交将引用 0051、全英文。

**对抗核实**:自问 3——(a)「游标会漏/重条目?」→ `id < before` 严格小于 + 唯一 PK,不重上页末条,测钉死(存活=正确);(b)「参与者查 N+1?」→ 一次 `IN hand_ids` 批查 + Python 分组,非 N+1(存活=无 N+1);(c)「历史泄底牌?」→ HandRecord/HandParticipant 结构上无牌面列,`select` 只取 nick/筹码/时间/池额,不可能泄(存活=结构性隐私)。0 真 bug。

## 待办 / 下一步

- **`GET /hands` 的 `room` 过滤**:先给 `HandRecord` 加 `room` 列(`HandRecordWrite` + reduce + orm_persister + Alembic 迁移),再 `WHERE room=?`(健壮,免 LIKE)——单独一篇。
- P7 余:profile(`GET /user/me` / `PATCH /user/nickname` 调 `ConnectionManager.rename` + `Presence` 大厅门 / `PATCH /user/password`)依赖 **P5** 鉴权/密码哈希。
- REST → TS codegen(openapi,无 node 待解):RoomMeta/LeaderboardEntry/HandRecordView 等 REST DTO 前端类型生成另开一篇。
