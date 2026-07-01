# 0050 · P7 REST:`GET /leaderboard`(排行榜,读 DB 结算积分)

日期:2026-07-01 · 范围:`app/db/queries.py`(`top_users_by_points`)、新建 `app/rest/leaderboard.py`、`app/shell/lifespan.py`(挂路由)、`app/gameconfig.py` + `app/poker.env.example`(limit 上下限)、`tests/rest/test_leaderboard.py`(新建)、`docs/rest.md`/`docs/refactor/TODO.md`。落地 [rest.md](../../rest.md) §排行榜 —— P7 REST 第二个切片(承 0048)。

## 背景 / 为什么

P7 REST 未阻塞切片按序推进:0048 落地 `GET /lobby/rooms`(唯一读 world 的 REST)。本批落地 `GET /leaderboard`——**第一个读 DB 的 REST**,恰好补全 0048 里 rest.md「REST 只读 DB」的正主场景(排行榜/历史/资料读结算后落库值)。leaderboard/hands 只读 DB、不碰 world、不需 P5 鉴权即可服务(公开排名);profile(改昵称/密码)+ 强鉴权依赖 P5,后置。

## 关键设计决策

1. **读 DB 结算积分,非身家**(rest.md §排行榜 决策):排的是 `User.points`(DB 落库的**结算后全局积分**),**不含桌上筹码**(`Seat.points` 内存不落库,storage.md)。一个把积分全买进牌桌的人,榜上只显桌下余额——这是「房态不落库」的直接结果,清晰且只读 DB 够用。含桌上筹码的「总身家」需读 world,列为 future。
2. **请求级 session、读 DB 不读 world**(rest.md §共同原则):查询 `top_users_by_points(sessionmaker, limit)` 每次调用 `async with sessionmaker()` 开一会话(= 请求级),与 PersistWriter 写 session 不复用;读路径无行锁、比内存滞后(展示够用)。与 0048 的 lobby-rooms(读 world)分属两类,正是 0048 调和的契约两侧。
3. **`LeaderboardEntry` 是 REST DTO**(rank/nickname/points),放 `app/rest/`,**不进 ws `ServerMessage` 联合、不进 `wire.gen.ts`**(同 RoomMeta,0048);前端 REST 类型走 openapi(P7 无 node 待解)。
4. **limit 上下限进 gameconfig**(不留裸字面量,config.md「加 tunable = 3 处」):`LEADERBOARD_DEFAULT_LIMIT`/`LEADERBOARD_MAX_LIMIT` + poker.env.example;路由 `Query(default=DEFAULT, ge=1, le=MAX)` 防超大查询。app/rest 非 core,可 import gameconfig。
5. **排序稳定**:`ORDER BY points DESC, nickname ASC`——同分按昵称定序,rank 稳定可复现(测试可断言)。
6. **dev 无鉴权**(同 lobby-rooms / dev ws):排名公开、无隐私。P5 上 JWT 时按 rest.md「REST 走 JWT」补(排行榜可留公开)。
7. **可测**(httpx 未装):`top_users_by_points` 对种子内存 DB 直测(真逻辑);路由 endpoint 直接 await(注入 seeded sessionmaker);`create_app` 布线测。

## 打算改什么(开工前)

- `app/db/queries.py`:`top_users_by_points(sessionmaker, limit) -> list[tuple[str,int]]`(select nickname/points,points 降序 + nickname 升序,limit)。
- 新建 `app/rest/leaderboard.py`:`LeaderboardEntry(rank,nickname,points)` + `make_leaderboard_router(get_sessionmaker)`(`GET /leaderboard?limit=`,rank = 序号+1)。
- `app/shell/lifespan.py`:`include_router(make_leaderboard_router(lambda: shell.sessionmaker))`。
- `app/gameconfig.py` + `app/poker.env.example`:`LEADERBOARD_DEFAULT_LIMIT`/`LEADERBOARD_MAX_LIMIT`。
- 新建 `tests/rest/test_leaderboard.py`:查询(降序 + 同分 nick 定序 + limit 截断 + 空表)/ 路由(await endpoint → rank 递增、limit 生效)/ 布线(`create_app` 含 `/leaderboard`)。
- 文档:rest.md §排行榜(标 0048→0050 落地 + DTO 字段 + dev 无鉴权)、TODO P7(`GET /leaderboard` 划删)。

## 实际改了什么

- **`app/db/queries.py`**:`top_users_by_points(sessionmaker, limit) -> list[tuple[str,int]]`——`select nickname, points ORDER BY points DESC, nickname ASC LIMIT n`,请求级 session(`async with sessionmaker()`,同现有 read 查询范式)。
- **`app/rest/leaderboard.py`**(新建):`LeaderboardEntry(rank, nickname, points)`(Pydantic REST DTO)+ `make_leaderboard_router(get_sessionmaker)`(`GET /leaderboard`,`limit=Query(default=gameconfig.LEADERBOARD_DEFAULT_LIMIT, ge=1, le=MAX)`,rank=序号+1)。只 import fastapi/pydantic/sqlalchemy 类型/gameconfig/db.queries(不 import shell/core reduce)。
- **`app/shell/lifespan.py`**:import `make_leaderboard_router`;`create_app()` 加 `include_router(make_leaderboard_router(lambda: shell.sessionmaker))`(sessionmaker 在 `__init__` 已建、恒非 None,route 内 `assert` 防御)。
- **`app/gameconfig.py`** + **`app/poker.env.example`**:新增 `LEADERBOARD_DEFAULT_LIMIT`(20)/`LEADERBOARD_MAX_LIMIT`(100),Field `ge=1,le=1000`(config.md「加 tunable = 3 处」)。
- **`tests/rest/test_leaderboard.py`**(新建,5):查询降序+同分 nickname 定序 / limit 截断 / 空表 / 路由 rank 递增+limit / `create_app` 布线含 `GET /leaderboard`。**`tests/test_gameconfig.py`**:`_valid_kwargs` 补两个新字段(涟漪:新增无默认字段 → 该构造须带)。
- **docs**:rest.md §排行榜(标 0050 落地 + DTO/limit/dev 无鉴权说明)、TODO P7(`leaderboard` 划删)。
- **未改**:core / wire / world / reduce;`wire.gen.ts`(`LeaderboardEntry` 是 REST DTO、不进 ws 联合,已验 `grep`=0)。

450 全绿(445→450,+5;含 gameconfig 涟漪修 1);`gen_wire_ts --check` OK。

## 自 review

对照 review.md 逐维(本批低风险:只读 DB 查询 + REST 投影,无 money/state 变更):

- **① 分层 / 不变量**:app/rest **只读 DB、不碰 world、不入 reduce**;查询在 `db/queries.py`(read 路径,与写侧 orm_persister 分文件)。app/rest 非 core → 可 import gameconfig/fastapi;**core/wire 不 import app/rest**(承 0048,grep 净);app/rest 经 `get_sessionmaker` 回调与 shell 解耦(同 lobby 的 `get_world`)。
- **② 代码↔文档同步**:rest.md §排行榜 字段/limit/DTO/dev-无鉴权 与代码一致;TODO 划删。
- **③ 文档↔文档一致**:rest.md §排行榜(读 DB)↔ §共同原则 1(0048 调和:leaderboard/hands/profile 读 DB、lobby-rooms 读 world)一致;结算积分语义 ↔ storage.md「房态不落库」一致。
- **④ 数据模型**:`LeaderboardEntry` 类型忠实;`points` 是**结算后全局积分**(桌上筹码不计,已在 DTO/模块头/rest.md 三处点明,非隐性坑);`limit` 由 gameconfig 兜(非魔法数)。**对抗自问「rank 会不会错位/不稳定」**→ `ORDER BY points DESC, nickname ASC` + `enumerate(start via i+1)`,同分定序、rank 连续,测试钉死 `[1,2]`。
- **⑤ 规范合规**:字段/函数带「为什么」注释;无裸字面量(limit 上下限入 gameconfig + poker.env.example)。修一处测试误注释(「dana」实为 alice 截断)。
- **⑥ 测试充分**:查询四臂(降序/同分定序/limit/空)+ 路由(rank 递增 + limit)+ 布线;httpx 未装 → 直接 await endpoint(覆盖路由体 + rank 赋值)。`LeaderboardEntry` 不泄 wire.gen.ts 已 grep 验。**未覆盖**:FastAPI 层 `Query(ge/le)` 边界拒(需 HTTP client,httpx 未装)——属框架校验、非本模块逻辑,记为覆盖空缺。
- **⑦ 流程账本**:打算↔实际一致;TODO 划删;提交将引用 0050、全英文。

**对抗核实**:自问 2——(a)「settled points 会不会误含桌上筹码」→ 读 `User.points`(DB),桌上筹码在 `Seat.points` 内存不落库,结构上不可能混入(存活=语义正确、已文档化);(b)「LeaderboardEntry 漏进 wire.gen.ts」→ 非 ws 联合、codegen 图不含,grep=0(存活=隔离)。0 真 bug。

## 待办 / 下一步

- P7 余:`GET /hands`(游标分页,读 HandRecord/HandParticipant,0026 表)——只读 DB、不阻塞;profile(`GET /user/me` / `PATCH /user/nickname` 调 `ConnectionManager.rename` + `Presence` 大厅门 / `PATCH /user/password`)依赖 **P5** 鉴权/密码哈希。
- REST → TS codegen(openapi,无 node 待解):RoomMeta/LeaderboardEntry 等 REST DTO 的前端类型生成另开一篇。
