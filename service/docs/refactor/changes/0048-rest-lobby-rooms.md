# 0048 · P7 首个 REST 面:`GET /lobby/rooms`(房间发现,只读 world)

日期:2026-07-01 · 范围:新建 `app/rest/`(`__init__.py` + `lobby.py`)、`app/shell/lifespan.py`(`create_app` 挂 lobby 路由)、`tests/rest/`(新建)、`docs/lobby.md`/`docs/rest.md`/`docs/refactor/TODO.md`。落地 [lobby.md](../../lobby.md)「房间列表(REST 读)」+ [rest.md](../../rest.md) —— TODO P7 的**未阻塞切片**。

## 背景 / 为什么(批判性:为什么是这一项)

审计(见 0047 前置)确认后端主体全绿(432),真剩:P5 国密(**计划定最后**)、前端消费/e2e(前端集成,延后)、**P7 REST(计划下一阶段)**。P7 里 `GET /lobby/rooms` 是**唯一不被 P5 阻塞**的切片:lobby.md:63-69 明说它读 **committed `world.rooms` 头数**(非 DB),故不需要 leaderboard/hands/profile 那样的 JWT(P5)与 DB 读路径。它还给 `Presence`/`world` 首个 REST 消费者,并立起 `app/rest/` 骨架(README 目标结构里一直留的包)。

**先决范式已由 0047 收尾时坐实**:`lifespan.py:90` 的 `Presence(world, conns)` 已确立 shell 只读消费 world 的安全范式——持稳定 world 引用、读 commit 后态(commit 原地改 `.users`/`.rooms`)、**单线程 asyncio 下 GameLoop.handle 全程无 await ⇒ 任何不 await 的读对它原子、不撕裂**(不变量 2)。`GET /lobby/rooms` 的投影 `list_rooms(world)` 纯同步无 await,照此范式安全。

## 关键设计决策

1. **契约张力显式调和(rest.md ↔ lobby.md)**:rest.md §共同原则 1 写「REST 读 DB,不读 world」;lobby.md §房间列表写「实时头数来自 `world.rooms`」。**二者对 lobby-rooms 冲突**。定论:**`GET /lobby/rooms` 是唯一读 `world` 的 REST 端点**——因为房间花名册/头数是**内存权威、从不落库**(storage.md:房态不持久),DB 里根本没有;leaderboard/hands/profile 读的是**结算后落库**的数据,才守「只读 DB」。两篇互指、各加一句边界说明(rest.md 把「只读 DB」scope 到那三个;lobby.md 标 lobby-rooms 落地 + 例外)。
2. **`RoomMeta` 是 REST DTO,不进 ws wire 联合**:lobby.md:69「RoomMeta 是 wire DTO ≠ Room」指「独立 Pydantic DTO」,**不是** ws `ServerMessage` 成员(它不是 ws 消息)。故放 `app/rest/lobby.py`(Pydantic `BaseModel`),**不进** `app/wire/`,也就**不进 `gen_wire_ts` 图**(codegen 只遍历 ServerMessage/ClientMessage 联合)——REST→TS 走 openapi(P7 无 node 待解,TODO:79),本批只交后端端点 + DTO,TS 生成延后(同 0017 对 ws 的「后端先备、前端消费另计」)。
3. **投影/路由/IO 三分离(可测)**:`list_rooms(world) -> [RoomMeta]` 是**纯同步投影**(无 FastAPI、无 await),单测直接喂 hand-built world;`make_lobby_router(get_world)` 造 `APIRouter`,路由体 = `list_rooms(get_world())`。`get_world` 用**迟绑 getter**(world 在 `DevShell.setup()` 后才建;`create_app` 传 `lambda: shell.world`,测试注入 fake)。httpx/TestClient **本环境未装**,故路由测试**直接 await `router.routes[0].endpoint`**(无需 HTTP client)。
4. **RoomMeta 字段**(lobby.md:66:静态 + 实时):`id`(= `world.rooms` 键;v1 无独立 `name` 字段,键即人读名)、`small_blind`、`big_blind`(= 2×小盲派生,同 `RoomConfigChanged`)、`buy_in`、`max_seats`(= `len(room.seats)`)、`seated`(占用座位数,含 OFFLINE 保座)、`watching`(`users_in_room` 中 `WATCHING` 数)、`status`(`RoomStatus`,StrEnum → 干净 JSON)。**完整游戏态(deck/hand/各人筹码)绝不上大厅**(wire.md 隐私)。
5. **dev 无鉴权**:与 dev ws 端点一致(明文、无 JWT);lobby-rooms 只暴露房配 + 头数(无隐私),公开发现合理。P5 上 JWT 时按 rest.md「REST 走 JWT」补(与 ws 两套)。
6. **`ROOM_FULL`/容量**:v1 不设(lobby.md 决策 5);`seated`/`max_seats` 只作展示,不做实时裁定。

## 打算改什么(开工前)

- 新建 `app/rest/__init__.py`(空包)+ `app/rest/lobby.py`(`RoomMeta` + `_room_meta` + `list_rooms` + `make_lobby_router`)。
- `app/shell/lifespan.py`:`create_app()` 里 `app.include_router(make_lobby_router(lambda: shell.world))`。
- 新建 `tests/rest/__init__.py` + `tests/rest/test_lobby.py`:① `list_rooms` 纯投影(多房排序 / 字段 / big_blind 派生 / seated 含 OFFLINE 保座 / watching 计数 / 空 world / HAND_STARTED 状态);② 路由 endpoint await(fake world → 等于 list_rooms;world=None → HTTPException 503);③ 布线(`create_app()` 的 app 含 `/lobby/rooms` 路由)。
- `docs/lobby.md`(§房间列表标落地 + RoomMeta 字段 + 只读 world 说明 + dev 无鉴权)、`docs/rest.md`(§共同原则 1 scope + lobby-rooms 例外)、`docs/refactor/TODO.md`(P7 行:`GET /lobby/rooms` 勾,余 leaderboard/hands/profile + REST-TS 待续)。

## 实际改了什么

- **`app/rest/__init__.py`**(新建):包注释(REST 查询面定位;首端点 lobby-rooms 唯一读 world,余读 DB 依赖 P5)。
- **`app/rest/lobby.py`**(新建):`RoomMeta(BaseModel)`(id/small_blind/big_blind/buy_in/max_seats/seated/watching/status)+ `_room_meta(room_id, room)`(seated=占用座位数含 OFFLINE 保座、watching=`WATCHING` 状态数、big_blind=2×小盲)+ `list_rooms(world)`(按 id 排序、纯同步无 await)+ `make_lobby_router(get_world)`(`GET /lobby/rooms`,world=None→503)。只 import `fastapi`/`pydantic`/`app.core`(**不 import shell/db**——经 `get_world` 回调与 shell 解耦)。
- **`app/shell/lifespan.py`**:import `make_lobby_router`;`create_app()` 里 `app.include_router(make_lobby_router(lambda: shell.world))`(world 迟绑)。
- **`tests/rest/__init__.py` + `tests/rest/test_lobby.py`**(新建,8 测):空 world / 字段+big_blind 派生 / 按 id 排序 / OFFLINE 保座计 seated 不计 watching / HAND_STARTED 状态+seated / 路由 endpoint await 等于 list_rooms / world=None→503 / `create_app()` 布线含 `GET /lobby/rooms`。
- **docs**:`lobby.md`(§房间列表标 0048 落地 + RoomMeta 字段 + 唯一读 world 说明 + dev 无鉴权 + 不进 ws 联合);`rest.md`(§共同原则 1 scope 到三 DB 模块 + lobby-rooms 例外说明);`TODO.md`(P7 行 `GET /lobby/rooms` 划删并注 0048;REST-TS 尾注更新)。
- **未改**:core / wire / db / 其它 shell;wire.gen.ts(RoomMeta 是 REST DTO,不进 ws `ServerMessage` 联合、不进 codegen 图,已验 `grep RoomMeta wire.gen.ts` = 0)。

440 全绿(432→440,+8);`gen_wire_ts.py --check` OK(RoomMeta 不泄进 wire.gen.ts);core 无越层 import(app/rest 不被 core/wire import,grep 净)。

## 自 review

方法:纯投影 `list_rooms` 直接单测(真逻辑面)+ 路由 endpoint 直接 await(httpx 未装,免 HTTP client)+ 布线测;收工 grep 复验分层 + wire.md 一致。逐维:

- **① 分层 / 不变量**:app/rest **只读 world、不写、不入 reduce**;`list_rooms` 纯同步无 `await` ⇒ 对唯一写者 GameLoop.handle(全程无 await)**原子读、不撕裂**(不变量 2,同 presence.py 范式)。**对抗核实「读会不会撕裂」**:endpoint 在 `world=get_world()` 与 `return list_rooms(world)` 间无 await;且 FastAPI 序列化发生在 handler 返回**之后**,此时 RoomMeta 列表已是**值快照**(从 world 拷出的标量),即便序列化期间 GameLoop 改 world 也无害。app/rest 非 core → 可 import fastapi/pydantic;**core/wire 不 import app/rest**(grep 净);app/rest **不 import shell/db**(经 `get_world` 回调解耦,只 import app.core.domain/enums)。
- **② 代码↔文档同步**:lobby.md 的 RoomMeta 字段/语义、rest.md 的例外、TODO P7 划删,均与落地代码逐字对齐。
- **③ 文档↔文档一致**:**调和 rest.md↔lobby.md 契约张力**(rest.md「只读 DB」scope 到三模块 + lobby-rooms 例外;lobby.md 标「唯一读 world 的 REST」;两篇 + storage.md 互指)。**对抗核实 wire.md 是否矛盾**:wire.md:21/83 只说「REST→OpenAPI→openapi-typescript(待 P7)」,**未**声称 RoomMeta 是 ws 消息——故 lobby.md 从旧「wire DTO」改述为「REST DTO、不进 ws 联合」与 wire.md 一致,非矛盾。
- **④ 数据模型**:RoomMeta 类型忠实(id str / 计数 int / status StrEnum→干净 JSON);`seated`=占座数(含 OFFLINE 保座,与 online 正交)、`watching`=`WATCHING` 状态数(二者不重叠);`big_blind`=2×小盲(与 domain `Room` 注释 + `RoomConfigChanged` 派生一致,非魔法数)。
- **⑤ 规范合规**:字段/函数带「为什么」注释;无裸字面量(2× 有派生说明);`get_world` 迟绑有注释解释(world 在 setup 后才建)。
- **⑥ 测试充分**:8 测覆盖投影全字段 + 派生 + 排序 + OFFLINE 保座边界 + 手牌中状态 + 空 + 路由 happy/503 + 布线;**杀「seated 用 users_in_room 数」变异**——test_seated_counts_offline_held_seat 钉死「seated 数座位不数状态」。未 HTTP 层测(httpx 未装)以直接 await endpoint 替代,覆盖路由体 + 503 分支。
- **⑦ 流程账本**:打算↔实际一致(无偏离);TODO P7 划删 + 尾注;提交将引用 0048、全英文。

**对抗核实存活 / 采纳 / 驳回**:2 个自问均**驳倒风险**——(a)「REST 读 world 会撕裂?」→ 无 await 的投影对 GameLoop 原子 + 返回值是拷出快照,不撕裂(存活=安全);(b)「RoomMeta 会漏进 wire.gen.ts?」→ 非 ws 联合成员、codegen 图不含,`grep`=0 实证(存活=隔离)。0 真 bug;新增 8 测全绿。

## 待办 / 下一步

- P7 余:`GET /leaderboard`(读 DB users 降序)、`GET /hands`(游标分页,读 HandRecord)、`GET /user/me` + `PATCH /user/nickname`(调 `ConnectionManager.rename` + `Presence.current_room` 大厅门)+ `PATCH /user/password`——后三者(profile)+ REST 鉴权依赖 **P5**(JWT/密码哈希);leaderboard/hands 只读 DB,功能上不阻塞但 rest.md 绑 JWT。
- REST → TS codegen(openapi,无 node 待解,TODO:79):RoomMeta 等 REST DTO 的前端类型生成,同 ws 的自包含 Python 生成器思路另开一篇。
- `LobbyBroadcast` 实时推送(lobby.md 待定):v1 前端轮询本端点即可。
