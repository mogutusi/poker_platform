# 0039 · 私信(DM)已读游标 + 实时已读回执(未读收件箱「读」路第一半)

日期:2026-06-26 · 范围:`app/wire/client.py`(+`DMMarkRead`)、`app/wire/server.py`(+`DMRead`)、`app/shell/messaging.py`(+`route_dm_mark_read`)、`app/db/dm_records.py`(+`DMReadCursorWrite`)、`app/db/models.py`(+`DMReadCursor` 表)、`alembic/versions/*`(新迁移)、`app/db/orm_persister.py`(+`DMReadCursorWrite` UPSERT)、`app/shell/persist.py`(`_state_key` 归类 `DMReadCursorWrite`)、`app/shell/receiver.py`(拦 `DMMarkRead`)、`pyproject.toml`(filterwarnings:忽略 aiosqlite 测试 teardown 告警)、重生成 `wire.gen.ts`、测、文档。落地 [messaging.md](../../messaging.md) §私信「读」路的**游标写 + 实时回执**部分。

## 背景 / 为什么

[messaging.md](../../messaging.md) §私信定「未读收件箱 + 完整已读回执」:发即落库 `DMWrite`(未读,**0038 已落地**),**收件人读了** → `DMMarkRead` 推进已读游标 + 回执发件人,**登录补收**读未读 + 回执。本批落地「读」路的前半:**游标写**(`DMMarkRead` → `DMReadCursorWrite` 状态写 + UPSERT)+ **实时已读回执**(`DMRead` 发在线发件人)。

**为何再拆(0039 游标写 / 0040 补收)**:messaging.md「读」路含两件——① 游标写 + 实时回执(收件人在线标读、发件人在线即收回执);② 登录补收(连接时读 DB 未读 + 回执——含 cursor↔message 关联查询 + uid↔nick 双向解析,体量近 0038)。一次全做过大。**0039 = 游标写 + 实时回执**(自洽:online-online 已读回执 UX 完整可用——A 发 → B 收 → B 标读 → A 收回执);**0040 = 登录补收**(读 0038 的 `DMMessage` + 本批的 `DMReadCursor`);**0041/future = 保留清理**(PersistWriter DELETE 已读满期)。同 0038 的「读基座先于消费者」:游标本批写入(UPSERT 测穷举)、补收 0040 读它,实时回执提供即时价值,非投机死代码。

## 关键设计决策(批判性,与 messaging.md 对齐)

1. **`DMMarkRead` 走 shell 路由(同 `DirectMessage`),不进 GameLoop**:`route_dm_mark_read` 在 Receiver 协程内——reader=连接 nick(不信报文)、peer=`msg.peer_nick`、`read_through`=客户端回传(它从 `DMDelivered.created_at` 拿到的串)。解析 uid → `put(DMReadCursorWrite)`(状态写)→ peer 在线 `enqueue(DMRead)` 回执。`to_command` 加 `case DMMarkRead(): raise`(shell 路由特例)。
2. **游标是状态写(按 `(reader,peer)` 覆盖,不是事件写)**:`_state_key(DMReadCursorWrite) = ("dm_cursor", reader_uid, peer_uid)`——同会话后写盖前写,只留最新进度(messaging.md「一表两用 / 只留最新游标」)。`DMWrite` 才是事件写(追加)。
3. **状态写 UPSERT,行可能不预存**:不同于 `PointsWrite`(User 行 seed/load 必存 ⇒ 定向 UPDATE),已读游标**首次读某会话时行不存在** ⇒ OrmPersister `_apply_state_write` 对 `DMReadCursorWrite` 走 **SELECT-by-PK → 无则 INSERT、有则改 `read_through_ts`**(唯一写者 race-free、跨方言,免 ON CONFLICT 二分;同 `_insert_dm` 幂等式)。**这是状态写的新子情形,db.md 同步标注**(state-write 不总是「行必存」)。
4. **`read_through` 由客户端供(信任其自身读进度)**:客户端把收到的 `DMDelivered.created_at`(ISO 串)回传作 `read_through`;服务端不校验其真实性——它只影响**该客户端自己**的未读计算(标早了只少看自己几条),无安全/他人影响(≤20 友善内网)。`reader_uid`/`peer_uid` 由服务端按连接 nick / peer_nick 解析(身份不信报文)。`created_at` 与 `read_through` 同一墙钟域(都源自 shell),比较 `created_at > read_through_ts` 正确。
5. **失败回执**:peer 不存在 = `ErrorMessage(INVALID_MESSAGE)`(标读一个不存在的会话 = 畸形请求,**非** DM 发送的 `DMUndelivered` 投递语义);peer = 自己 = `CANNOT_DM_SELF`(无自己↔自己会话,对称 DM 禁自发);reader 缺 DB 行 / DB 读失败 = `INTERNAL`(同 `route_direct_message`)。
6. **实时回执尽力而为 + 不限速**:复用 `_try_deliver`(收件人 outbound 满 → 丢回执不丢游标,游标已落库、补收 0040 兜)。**`DMMarkRead` v1 不限速**——状态写幂等(同键覆盖,刷 N 次只落 1 次)、廉价,同 `FetchRoomChat` 拉取免限速的判据(洪泛由 outbound 满触发背压兜)。

## 打算改什么(开工前)

- `app/db/dm_records.py`:`DMReadCursorWrite(PersistPayload)` frozen dataclass(reader_uid/peer_uid/read_through_ts)。
- `app/db/models.py`:`DMReadCursor`((reader_uid, peer_uid) 复合主键 + 双 FK + read_through_ts)。
- `alembic/versions/*`:autogenerate「add dm_read_cursor」迁移 + 审 + sqlite round-trip。
- `app/shell/persist.py`:`_state_key` 加 `case DMReadCursorWrite(): return ("dm_cursor", str(reader), str(peer))`。
- `app/db/orm_persister.py`:`_apply_state_write` 加 `DMReadCursorWrite` → SELECT-by-PK → INSERT/UPDATE。
- `app/wire/client.py`:`DMMarkRead{peer_nick, read_through}` + 注册 + 联合 + `to_command` raise。
- `app/wire/server.py`:`DMRead{reader_nick, read_through}` + 注册。
- `app/shell/messaging.py`:`route_dm_mark_read`(防护 → 解析 uid → put 游标 → 在线回执);`_try_deliver` 形参放宽到 `ServerMessage`。
- `app/shell/receiver.py`:`_frame_to_command` 拦 `DMMarkRead` → `route_dm_mark_read`,return None。
- 重生成 `wire.gen.ts`(+`DMMarkRead`/`DMRead`;datetime→string 已于 0038 支持)。
- 测:`test_messaging.py`(+标读落游标+在线回执/离线只落游标/peer 不存在 INVALID_MESSAGE/自己 CANNOT_DM_SELF/reader 缺行 INTERNAL/DB 失败 INTERNAL)、`test_orm_persister.py`(+游标 INSERT 新建/UPDATE 覆盖/FK)、`test_persist.py`(+游标归状态写按键覆盖)、`test_protocol.py`(+DMMarkRead 样本+raise、DMRead 隐私)、`test_receiver.py`(+端到端标读帧 → peer 收 dm_read)。
- 文档:`messaging.md`(读路游标写标落地 + 0039/0040/0041 拆分)、`db.md`(DMReadCursorWrite 落地 + 状态写 UPSERT 子情形)、`db-migrations.md`(+DMReadCursor 表)、`wire-protocol-guide.md`(+dm_mark_read/dm_read)、`TODO.md`。

## 实际改了什么

- **`app/db/dm_records.py`**:`DMReadCursorWrite(PersistPayload)` frozen dataclass(reader_uid/peer_uid/read_through_ts)。
- **`app/db/models.py`**:`DMReadCursor`((reader_uid, peer_uid) 复合主键 + 双 FK→user.id + read_through_ts tz)。
- **`alembic/versions/7ff9cb0a8db1_add_dm_read_cursor.py`(新)**:autogenerate「add dm_read_cursor」;down_revision=`79d1fd60fc7f`;sqlite upgrade/downgrade/重升 round-trip 通过、单 head。
- **`app/shell/persist.py`**:`_state_key` 加 `case DMReadCursorWrite(): return ("dm_cursor", str(reader), str(peer))`(状态写按 (reader,peer) 覆盖)。
- **`app/db/orm_persister.py`**:`_apply_state_write` 加 `DMReadCursorWrite` → `_upsert_dm_cursor`(`session.get` by 复合主键 → 无则 INSERT、有则改 `read_through_ts`;唯一写者 race-free,行非必存故 UPSERT 而非纯 UPDATE)。
- **`app/wire/client.py`**:`DMMarkRead{peer_nick, read_through}` + 注册 + 联合;`to_command` 加 `case DMMarkRead(): raise`(shell 路由特例)。
- **`app/wire/server.py`**:`DMRead{reader_nick, read_through}` + 注册。
- **`app/shell/messaging.py`**:`route_dm_mark_read`(自己→`CANNOT_DM_SELF` / 解析 uid / peer 不存在→`INVALID_MESSAGE` / reader 缺行→`INTERNAL` / DB 失败→`INTERNAL` → `put(DMReadCursorWrite)` → peer 在线 `_try_deliver(DMRead)`);`_try_deliver` 形参由 `DMDelivered` 放宽到 `ServerMessage`(复用给 DMRead)。
- **`app/shell/receiver.py`**:`_frame_to_command` 拦 `DMMarkRead` → `await route_dm_mark_read`,return None(不进 inbox)。
- **`scripts/gen_wire_ts.py`**:无改(`datetime→string` 已于 0038 支持);**重生成 `wire.gen.ts`**(+`DMMarkRead`/`DMRead`,`read_through: string`)。
- **`pyproject.toml`**:`[tool.pytest.ini_options].filterwarnings` 按**线程名** `_connection_worker_thread` 锚定、忽略 aiosqlite 在 teardown 抛的未处理异常告警(本批新增建引擎测试 messaging +6 / orm +3 / receiver +1 累积、把既有 0028 helper「测试引擎不 dispose」latent 债推到显现;生产 lifespan.stop 正常 dispose,纯测试期噪声,见自 review)。
- **测**:`tests/shell/test_messaging.py`(+6:落游标+在线回执/离线只落游标/未知对端 INVALID_MESSAGE/自己 CANNOT_DM_SELF/reader 缺行 INTERNAL/DB 失败 INTERNAL);`test_orm_persister.py`(+3:游标 INSERT 新建/UPDATE 覆盖只一行/FK 回滚);`test_persist.py`(+1:游标归状态写、同 (reader,peer) 覆盖、不同 peer 各占);`test_protocol.py`(+DMMarkRead 样本+raise、DMRead 隐私无牌);`test_receiver.py`(+端到端标读帧 → peer 收 dm_read + 落游标)。
- **文档**:`messaging.md`(§私聊 读路游标写标落地 + §投递与落库 读 row 0039 / 登录补收 0040 / 保留清理 0041)、`db.md`(当前实例标 0039 + **两类写表加「行是否预存」UPSERT 子情形** + DMReadCursorWrite 示意标落地)、`db-migrations.md`(DMReadCursor←DMReadCursorWrite + 迁移号)、`wire-protocol-guide.md`(§3/§4/§8 加 dm_mark_read/dm_read)、`TODO.md`(读路游标写划掉、补收 0040/清理 0041)。

357 全绿(346→357);codegen `--check` 干净;core 无越层 import;迁移 round-trip 通、单 head。

## 自 review

方法:对照 [review.md](../../review.md) 跑对抗式 **4 维 review 子代理工作流**(分层/并发 · 设计正确性 · 代码↔文档 · 测试/账本;每维独立审查 → 每候选默认反驳)。10 agent、6 候选,**0 code defect / 0 blocker / 0 major**:两个正确性维度均洁(分层维 `findings:[]`、设计维仅一处 `ts` 标注 nit),「游标回退无测」候选**被正确驳回**(client-trusted read_through 是 0039 决策 4 明定行为、非缺陷),5 处确认项全 nit、已当场修。逐维:

- **① 分层 / 不变量 / 并发**:**红线全过**(review 报 `findings:[]`)——`grep app/core` 无 `await`/越层 import;`route_dm_mark_read` 全在 Receiver 协程、**不读写 world**;`persist.put` 同步无 await;**路由只读 uid(`load_uids_by_nicks`)、绝不 `await commit`**(唯一写者仍 PersistWriter);`app/db` 不 import `app/shell`;`persist.py` 仍 SQLAlchemy-free(`import app.db.dm_records` 不拉 sqlalchemy);`_try_deliver` 放宽到 `ServerMessage` 仍 `put_nowait`、不 drop 对端连接。
- **② 代码↔文档**:`DMMarkRead`/`DMRead` 签名、防护序、失败回执二分(peer 不存在→`INVALID_MESSAGE` ≠ `DMUndelivered`、自己→`CANNOT_DM_SELF`)、状态写 UPSERT 与 messaging.md/db.md 同步;codegen `--check` 干净(`read_through: string`)。
- **③ 文档↔文档**:messaging.md/db.md/db-migrations.md/wire-guide/TODO/本记录交叉引用一致(迁移 `7ff9cb0a8db1`、0039/0040/0041 拆分);db.md 新增「状态写·行是否预存」UPSERT 子情形(`DMReadCursorWrite` 行非必存)与代码一致。
- **④ 数据模型正确性**:**核心红线全过**——`DMReadCursorWrite` 在 `_state_key` 归**状态写**(键 `("dm_cursor",reader,peer)`,**非 append**,否则游标不收敛,已测覆盖+反驳核实);`_upsert_dm_cursor` `session.get` 复合主键按 `(reader_uid, peer_uid)` 正序、INSERT-or-UPDATE 正确;reader/peer 无互换;model↔迁移↔payload 字段对齐;错误臂均不落库;隐私无 hole_cards/deck。
- **⑤ 规范**:中文字段注释 + 「为什么」(UPSERT 行非必存 / 不限速 / 实时尽力而为);**采纳 nit**:`_upsert_dm_cursor` 的 `ts` 补 `: datetime` 标注 + import(对齐兄弟 helper 全标注)。
- **⑥ 测试**:357 全绿(346→357)。守恒/隐私/边界:落游标+在线回执(键 reader,peer)/离线只落游标/未知对端 INVALID_MESSAGE/自己 CANNOT_DM_SELF/reader 缺行·DB 失败 INTERNAL/游标 INSERT 新建·UPDATE **覆盖只一行**(状态写收敛核心属性已断言)·FK 回滚/`_state_key` 归状态写按键覆盖/端到端标读帧投达。
- **⑦ 账本**:**采纳并修三处账本 nit**——① 范围 header(line 3)补 `pyproject.toml`;② filterwarnings 缘由「4 个建引擎测试」误计 → 改「messaging +6 / orm +3 / receiver +1」(与正文 +6/+3/+1 一致);③ 本「自 review」段回填(原占位)。打算↔实际一致;提交引用 0039、全英文。

**对抗核实存活 / 采纳 / 驳回**:6 候选——**确认 5(全 nit)**:① `ts` 缺类型标注(修)② 缘由建引擎数误计(修)③ 范围 header 漏 pyproject(修)④ filterwarnings 注释「精确到 message」措辞过满(实为按**线程名**锚定;改注释澄清,并点明仍不掩盖其它线程异常)⑤ 自 review 段占位(填)。**驳回 1**:「游标回退无测」——驳回(read_through client-trusted 是 0039 决策 4 + messaging.md 明定行为「只影响该客户端自身未读、无他人影响」,状态写按最新 put 收敛即契约本身;非缺陷、非遗漏,候选自身亦承认「not a bug」)。

> 批判性自评:本批最该警惕的两点——(a)**状态写 vs 事件写归类**:游标若误归 append 则永不收敛、未读永远算错;review 实查 `_state_key` 归状态写 + 覆盖测固化,锚死。(b)**filterwarnings 是否在「掩盖问题」**:它是 0028 起既有「测试引擎不 dispose」latent 债被本批新测推显,按线程名精确锚定 aiosqlite worker、不掩盖其它线程异常,生产路径正常 dispose;诚实标注为测试卫生债、留专项清理,非藏 bug。最高风险面(UPSERT 复合主键顺序、路由不 await commit / 不读 world)经 review 实跑确认锚在既有不变量。

## 待办 / 下一步

- **0040**:登录补收——连接时 shell 读 DB:未读(`DMMessage` where to_uid=me AND created_at > cursor)→ `DMDelivered` 列表 + 未读数;读 `DMReadCursor` where peer=me → `DMRead` 回执补发。
- **0041/future**:保留清理(PersistWriter 周期 DELETE 已读满 `DM_READ_RETENTION_SECONDS` 的 `DMMessage`,`DM_CLEANUP_INTERVAL_SECONDS`)。
- 好友/黑名单、富文本@提及、内存未读镜像——future(messaging.md §待定)。
