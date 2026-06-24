# 0030 · P4(三之二-c):per-join wire-load —— wire `JoinRoom` + Receiver 读 DB + dev 连接→join 流

日期:2026-06-24 · 范围:`app/wire/client.py`(加 `JoinRoom` 报文)、`app/db/queries.py`(新,`load_user_by_nick` 读路径)、`app/shell/receiver.py`(JoinRoom 帧异步读 DB 富化 `uid`/`loaded` 构命令 + `sessionmaker` 参数)、`app/shell/lifespan.py`(dev 房空预置、退役启动整载、Receiver 传 `sessionmaker`)、`frontend/src/types/wire.gen.ts`(重 codegen)、`tests/`(wire/receiver/e2e)、文档(lobby/connection/wire-protocol-guide/TODO)。

## 背景 / 打算改什么

[0029](0029-p4-db-backed-dev-shell.md) 让 dev shell DB-backed,但用「预置用户在房 WATCHING + 启动整体载入」绕开 `JoinRoom`。本篇兑现架构的**真 per-join 载入**(storage.md / user.md / lobby.md):用户连接进**大厅** → 主动 `join_room{room}` → Receiver 按连接 nick **读 DB** 富化 `uid`/`loaded` → `JoinRoom(room, uid, loaded)` → reduce `_join_room`(0022 已落地)装入 world。

### 设计决策(开工前定)

1. **`JoinRoom` 报文只带 `room`,`uid`/`loaded` 由 shell 读 DB 盖**(身份/积分不进报文,wire.md 形状 #5):故 `JoinRoom` **不走** `to_command`(它无 DB)——Receiver 收帧时 `isinstance(msg, wire.JoinRoom)` 拦截,走**异步** DB 读路径构命令;其余 9 报文仍走同步 `to_command`。`to_command` 加 `case JoinRoom(): raise`(保持对 `CLIENT_MESSAGES` 穷尽 + 点明 JoinRoom 归 Receiver)。
2. **读路径 `load_user_by_nick` 落 `app/db/queries.py`**(新,读侧;与写侧 `orm_persister.py` 分文件):`SELECT User WHERE nickname` → `(uid, points) | None`。P7 REST 读查询日后亦归此。
3. **找不到用户行 = `INTERNAL`**(非新错误码):dev 握手已拒非 `DEV_USERS`、且都种子过;生产 session 由注册(P5)签发 ⇒ 有 session 必有行。`None` 是「鉴权说有、DB 说无」的内部不一致,`INTERNAL` 合适,不为防御态加 wire 错误码。
4. **Receiver 加 `sessionmaker` 参数**:`run_receiver(conn, conns, inbox, timer, sessionmaker)`;dev_ws 传 `shell.sessionmaker`。现有 receiver 测试(非 JoinRoom)补传一个 in-memory sessionmaker(JoinRoom 路径不命中即不查)。
5. **dev 流翻转(退役 0029 启动整载)**:`build_dev_world()` 改回**空 dev 房**(房存在、`users_in_room` 空、`world.users` 空);`setup()` **保留** `seed_dev_users`(DB 须有用户供 join 载入),**删** `load_dev_users` + 缺失校验 + `build_dev_world(loaded)`。dev 用户连接 → 大厅(`Connect` no-op)→ `join_room{"dev"}` → 载入。dev 房须存在(`_join_room` 校 `NO_SUCH_ROOM`),故仍预置空房。

### 拆分说明

`_join_room`(reduce)+ `JoinRoom` 命令早由 [0022](0022-join-room-state-snapshot.md) 落地;本篇只补**缺的 shell 半**(wire 报文 + Receiver DB 富化 + dev 流)。`StateSnapshot`/`UserJoined` 出站 wire 已在 0022。

## 实际改了什么

- **`app/wire/client.py`**:加 `JoinRoom(ClientMessage)`(`type="join_room"` / `room: str`)+ 入 `CLIENT_MESSAGES` + 判别联合;`to_command` 加 `case JoinRoom(): raise AssertionError`(保持穷尽 + 点明归 Receiver)。
- **`app/db/queries.py`**(新):`load_user_by_nick(sessionmaker, nick) -> (uid, points) | None`(`SELECT User WHERE nickname`),读侧,不 import shell。
- **`app/shell/receiver.py`**:`run_receiver` 加 `sessionmaker` 参数;`_to_command` → **async `_frame_to_command`**:`isinstance(msg, wire.JoinRoom)` 拦截走 `_build_join`(await `load_user_by_nick` → `JoinRoom(origin, room, uid, loaded)`;无行回 `INTERNAL`);其余仍 `to_command`。
- **`app/shell/lifespan.py`**:`build_dev_world()` 改回**空** dev 房(无预置用户、`world.users={}`);`setup()` 删 `load_dev_users`+缺失校验,保留 `seed_dev_users`(DB 须有用户供 join);dev_ws 传 `shell.sessionmaker` 给 `run_receiver`;删 `load_dev_users` 函数 + `UserState`/`UserStatus` 死 import + 头注更新。
- **`frontend/src/types/wire.gen.ts`**:重 codegen(加 `JoinRoom` 接口 + 入 `ClientMessage` 联合)。
- **测试**:`tests/shell/test_receiver.py`(2 调用点补 `sessionmaker` + 新 `test_join_room_frame_loads_user_from_db`)、`tests/shell/test_dev_db_e2e.py`(3 测随 dev 流翻转改:setup 空 world / 幂等查 DB / buyin 先 JoinRoom;+ 新全链 `test_e2e_connect_join_buy_through_dev_shell`)、`tests/wire/test_protocol.py`(注册表纳 JoinRoom + parse 往返 + JoinRoom 走 to_command 即 raise)。
- **文档同步**:`connection.md`(收帧循环 JoinRoom 例外 + dev shell 落地注 0030)、`lobby.md`(进房全链已落地)、`wire-protocol-guide.md`(`join_room` 入客户端报文表 + 移出「还没有」)、`TODO`(0030 + P4 delayDB 标 `[x]`)。

## 测试 / 验证

全量 **272 passed**(270 → +2:receiver join 1 + 全链 e2e 1;另有 3 e2e/3 receiver/1 protocol 随翻转改写,净 +2)。覆盖:

- **wire**:`join_room` parse 往返;JoinRoom 在 `CLIENT_MESSAGES`;`to_command(JoinRoom)` 抛(契约:归 Receiver)。
- **Receiver per-join**(Shell fake + 种子 DB):空 dev 房 → 喂 `join_room` 帧 → 读 DB 富化 `uid=7`/`points=888` → reduce 装入 `world.users`(WATCHING)+ 回帧(UserJoined/快照)。
- **全链**(真 `DevShell` + `Receiver` + 真 `sessionmaker`):connect alice(大厅)→ `join_room`(读 DB 载入 1000)→ `sit_down` → `buy_in{100}` → **内存先生效**(world 900)→ `flush_once` → **DB 追平**(User.points=900)。
- **dev 流翻转回归**:`setup()` 后 `world.users=={}` + dev 房空 + DB 种子 6 用户;种子幂等(改 DB→42,重启 setup 不重置)。
- 分层:`app/db/queries.py` / `app/core` 不 import shell;`receiver`(shell)→ `db.queries` 合规;dev shell `create_app` boot;`gen_wire_ts.py --check` OK。

## 自 review(push 前对抗式 7 维)

> 方法:3 维度 finder(正确性/身份 · 测试/生命周期 · 文档/codegen)× 各 finding 独立 verifier「默认反驳」(含 repro)。候选 32,确认真问题去重后 **4 类可行动(全已修)**,其余正向确认。**SAFE-TO-PUSH**。

**对抗式抓到 + 已修:**

- **(① CRITICAL)`_build_join` 读 DB 抛异常会静默 drop 连接**:`load_user_by_nick` 抛(DB 断/超时)→ 冒到 `run_receiver` 外层 `except` → 连接被关、**不回错误**,违反「Receiver 层错误回发 ErrorMessage」契约(error.md / connection.md)。已在 `_build_join` `try/except` 兜:回 `INTERNAL` 错误 + 保活连接(同解析错误路径)。+ 测 `test_join_room_db_error_errors_internal_keeps_conn`(未建表 sessionmaker → 查抛 → INTERNAL + 连接仍 current)。
- **(③ MAJOR)`storage.md` 残留「dev 启动期整体载入(0029)」**:0030 已退役启动整载、改 per-join → 改述为「dev 曾启动整载(0029),0030 起真 per-join 载入;启动期例外现仅预置空房」。
- **(⑤ MINOR)`app/db/queries.py` 模块头注偏「essay」**(违 [code-comment-style] 不堆模块头大段说明)→ 压成一行 + 指 storage.md/db.md。
- **(⑥ MINOR)缺错误路径测试**:补 4 个 Receiver 集成测试——无 DB 行→`INTERNAL`、DB 抛→`INTERNAL`(均保活)、目标房不存在→`NO_SUCH_ROOM` 回发、重复进房→`ALREADY_IN_ROOM` 回发。

**逐维核(verifier repro 实证):**

- **① 分层/不变量/身份**:**身份不可伪造**——`join_room` 报文只有 `{type, room}`(Pydantic 丢弃注入的 `uid`/`points`,repro 确认 `model_fields==['type','room']`);`uid`/`loaded` 仅来自 DB(`load_user_by_nick(conn.nick)`),`nick` 取连接会话(不信报文)。`to_command` 唯一调用方是 Receiver,且 JoinRoom 在调 `to_command` 前被拦截(`isinstance` 守门)——穷尽性 + 特例 raise 双保。core 纯;`app/db/queries.py` 不 import shell;`receiver`(shell)→`db.queries` 合规。✓
- **② 代码↔文档**:connection.md 收帧循环 JoinRoom 例外 + dev shell 落地注;lobby.md 进房全链;wire-protocol-guide.md `join_room` 入表 + 移出「还没有」;storage.md per-join 订正;TODO P4 标 `[x]` + 0030 条——逐处对代码核一致。✓
- **③ 文档↔文档**:无残留「dev 预置用户在房 / 启动整载」死claim(grep 复验);codegen `wire.gen.ts` 与源一致(`--check` 通)。✓
- **④ 数据模型**:无模型改动;`JoinRoom(room,uid,loaded)` 经 checkout(唯一带 room 命令)达 `_join_room`(0022),房存在校验 / 单房间约束 / 装 `UserState(points=loaded)` 复用。✓
- **⑤ 规范**:类型标注齐(`sessionmaker` 参数、`_build_join`);无死代码(`load_dev_users` + `UserState`/`UserStatus` import 清);头注已压缩;`wire.JoinRoom` vs `commands.JoinRoom` 命名不撞(限定符区分)。✓
- **⑥ 测试充分**:276 全绿;wire parse 往返 + to_command 契约 raise + 注册穷尽;Receiver per-join 读 DB 富化(uid/points 来自 DB 非报文)+ 4 错误路径 + 全链 connect→join→buy→DB;dev 流翻转回归(空 world / 种子幂等)。✓
- **⑦ 流程账本**:记录「打算↔实际」对照(含拆分:_join_room 归 0022);TODO 勾项;提交全英文引用 0030。✓

**正向确认(verifier REFUTED / 实证正确)**:身份不进报文(wire.md #5);per-command 单房间;displacement 仍工作;全链 e2e 真穿 Receiver→DB→reduce→persist→DB;codegen 一致;connection.md lifespan「预置静态房」非陈旧(那是预置**房**非用户)。

## 待办 / 下一步

- **P4 三之二收尾**:至此「内存权威 + 载入一次 + delayDB 落库」write+load 全链在 dev 跑通(connect→join→load→play→persist)。`Connect` 重连 `StateSnapshot`(0022 `_connect`)经 dev ws 实测留观察。
- **P8 收尾**:lifespan drain 边界(`DB_DRAIN_TIMEOUT_MS`)、`DATABASE_URL` 进 `app/config`、端到端冒烟(前端 ↔ dev ws 走一手牌)。
- **P5 国密**:替换 dev 明文握手/帧;`load_user_by_nick` 的 nick 改由 session 解析(不再信 `?nick=`)。
