# 0038 · 私信(DM)发送 + 实时投递 + 落库(未读收件箱「发」路)

日期:2026-06-26 · 范围:`app/wire/client.py`(+`DirectMessage`)、`app/wire/server.py`(+`DMDelivered`/`DMUndelivered`)、`app/shell/messaging.py`(新:DM shell 路由)、`app/db/dm_records.py`(新:`DMWrite` 载荷)、`app/db/models.py`(+`DMMessage` 表)、`alembic/versions/*`(新迁移)、`app/db/queries.py`(+`load_uids_by_nicks`)、`app/db/orm_persister.py`(+`DMWrite` INSERT)、`app/shell/persist.py`(`_state_key` 归类 `DMWrite`)、`app/shell/connection.py`(+`dm_bucket`)、`app/shell/receiver.py`(拦 `DirectMessage` → DM 路由)、`app/shell/lifespan.py`(`persist` 穿 `run_receiver`)、`app/core/errors.py`(+`CANNOT_DM_SELF`)、`app/gameconfig.py`(`DM_*`)、`scripts/gen_wire_ts.py`(+`datetime`→`string`)、重生成 `wire.gen.ts`、测、文档。落地 [messaging.md](../../messaging.md) §私信「未读收件箱」的**「发」路**。

## 背景 / 为什么

[messaging.md](../../messaging.md) §私信定:私聊**走 shell 路由(不进 GameLoop)**,**以 DB 为权威**(发即落库 = 未读),在线再叠加实时投递。0036(房聊历史)/0037(presence,`is_online`)是其前置基座;0036「下一步」明确点名「私聊 DM 未读收件箱」。本批落地其**「发」路**。

**为何拆「发」/「读」两批**:messaging.md §私信完整含三件事——① 发(`DirectMessage`→落库 `DMWrite`+在线投 `DMDelivered`)、② 读游标(`DMMarkRead`→状态写 `DMReadCursorWrite`+回执 `DMRead`)、③ 登录补收(读 DB 未读 + 已读回执)+ 保留清理(PersistWriter DELETE)。一次全落 = 2 client + 4 server 报文 + 2 DB 表 + 2 迁移 + 2 载荷 + persist/orm 各两类写 + 三处路由 + 限速 + 三组配置 + ~20 测,过大、伤 review 质量。**0038 = 发 + 实时投递 + 落库(`DMWrite` 事件写)**(自洽竖切:能发、在线即时收、必落库);**0039 = 读游标 + 登录补收 + 保留清理**(读已落的库)。同 0034→0035(emoji 设计→实现)、0036→0037 的增量节奏。本批是「读基座先于消费者」:`DMMessage`/`DMWrite` 由实时投递同源产出 + 测穷举(orm INSERT),非投机死代码——0039 补收即读它。

## 关键设计决策(批判性,与 messaging.md 对齐 + 三处修订)

1. **DM 全程 shell 路由,`DirectMessage` 不映射 Command**(messaging.md 契约 1):新 `app/shell/messaging.py` 的 `route_direct_message` 在 Receiver 协程内处理——防护 → 解析 uid → `persist.put(DMWrite)`(必落 = 未读)→ 在线再 `enqueue(DMDelivered)`。`to_command` 加 `case DirectMessage(): raise`(同 `JoinRoom`/`FetchRoomChat` 特例),供穷尽 + 协议直测。
2. **【修订/澄清 messaging.md】失败回执二分**:messaging.md §私信「身份」说「to_nick 不存在则回 Err」、「路由」又说「`DMUndelivered{to_nick}` 只在对端不存在时回」——口径不一。**本批落定**:`to_nick` **对端不存在** = `DMUndelivered{to_nick}`(投递结果,带 `to_nick` 供前端把该条外发标失败;非校验错);**发给自己 / 空 / 超长 / 限速** = `ErrorMessage`(`CANNOT_DM_SELF`/`INVALID_MESSAGE`/`MESSAGE_TOO_LONG`/`RATE_LIMITED`,同 0033 房聊防护的回执通道)。新增 `CANNOT_DM_SELF` 错误码(项目惯例:具体码,见 `CANNOT_OPEN_VOTE`)。
3. **防护序(同 0033 房聊):空 → 超长 → 自发 → 限速 → 解析 uid → 投递**。内容/语义错(空/长/自发)先拒(廉价、不耗令牌、不读 DB);合法再过令牌桶(每连接 `dm_bucket`,同 `chat_bucket`);过桶才读 DB 解析 uid(贵)。
4. **`DMWrite` 载荷置 `app/db/dm_records.py`(新),不入 `core/records.py`**:`core/records.py` 头明定「reduce 摊牌/结束产出的载荷」——DM 是 **shell 产**,core 永不碰,放 core 概念错。`PersistPayload` 基类在 `core/events.py`(共享契约),`DMWrite` 子类化它但定义在 db 层:① `app/shell/persist.py` `_state_key` 与 ② `app/db/orm_persister.py` 都 import 它,皆 shell→db / db 内,无越层(`app/db/__init__.py` 无 import,故 persist.py 仍 import 即 SQLAlchemy-free)。对称:**core 产载荷在 core/records.py,shell 产载荷在 db/dm_records.py**,基类同在 core/events。frozen dataclass(对齐 records.py 实现,非 db.md 示意的 BaseModel)。
5. **`DMWrite` 是事件写(追加),靠 `dedupe_key=msg_id` 幂等 INSERT**(db.md 两类写):`_state_key` 加显式 `case DMWrite(): return None`(避免落「unknown payload」warning);OrmPersister `_apply_event_write` SELECT-by-dedupe_key 再 INSERT(同 HandRecord 幂等式)。`msg_id = uuid4().hex`(shell 生成;比 db.md 示意的 `f"{from_uid}:{微秒}"` 更稳——免同微秒撞键)。`created_at` = shell `datetime.now(utc)`(展示 + 0039 未读/已读比较键)。
6. **实时投递尽力而为,落库是权威**:在线(`conns.get(to_nick)`)才 `enqueue(DMDelivered)`;**收件人 outbound 满 → 丢这次实时投递 + WARNING,不丢消息**(已落库,0039 登录补收兜)。**不在此 drop 收件人连接**——本协程是发件人的 Receiver,drop 收件人(投 Disconnect)是 GameLoop/其自身背压职责,跨协程 drop 越界。这正契合「DB 权威 + 实时投递只是优化」(messaging.md 决策)。
7. **v1 不回发件人回执 / 无 echo**:messaging.md「可回发件人一个回执」是可选;发件人本地乐观渲染自己发的消息,**送达确认走 0039 的 `DMRead` 已读回执**。本批发件人成功路径零回包(失败才回 Err/DMUndelivered),减面。
8. **`DMDelivered.created_at: datetime` → codegen 加 `datetime→string` 映射**:Pydantic JSON 把 datetime 序列化成 ISO 串,TS 端即 `string`。`gen_wire_ts._ts_type` 原无 `datetime` 分支(会 `TypeError`),补一行通用映射(后续带时间戳的消息同享)。

## 打算改什么(开工前)

- `app/core/errors.py`:`CANNOT_DM_SELF`。
- `app/gameconfig.py`:`DM_MAX_TEXT_LEN`/`DM_RATE_BURST`/`DM_RATE_PER_SEC`(注:保留清理配置随 0039)。
- `app/db/dm_records.py`(新):`DMWrite(PersistPayload)` frozen dataclass。
- `app/db/models.py`:`DMMessage`(id/dedupe_key unique/from_uid FK/to_uid FK+index/text/created_at)。
- `alembic/versions/*`:autogenerate「add dm_message」迁移 + 审。
- `app/db/queries.py`:`load_uids_by_nicks(sm, nicks) -> dict[str,int]`。
- `app/db/orm_persister.py`:`_apply_event_write` 加 `DMWrite` → SELECT-by-dedupe_key 再 INSERT `DMMessage`。
- `app/shell/persist.py`:`_state_key` 加 `case DMWrite(): return None`(import `DMWrite`)。
- `app/wire/client.py`:`DirectMessage{to_nick,text}` + 注册 + 联合 + `to_command` raise 特例。
- `app/wire/server.py`:`DMDelivered{msg_id,from_nick,text,created_at}`/`DMUndelivered{to_nick}` + 注册(import `datetime`)。
- `app/shell/messaging.py`(新):`route_direct_message` + `_try_deliver`。
- `app/shell/connection.py`:`Connection.dm_bucket` + create 建桶。
- `app/shell/receiver.py`:`run_receiver`/`_frame_to_command` 加 `conns`(已有)+`persist`;拦 `DirectMessage` → `await route_direct_message`,return None。
- `app/shell/lifespan.py` + `tests/shell/test_dev_db_e2e.py` + `tests/shell/test_receiver.py`:`run_receiver` 透传 `persist`(`tests/shell/_fakes.py` 的 `Shell.persist` 自 0036 已有,无需改)。
- `scripts/gen_wire_ts.py`:`datetime → "string"`;重生成 `wire.gen.ts`。
- 测:`tests/shell/test_messaging.py`(新:在线投递/离线只落库/对端不存在 DMUndelivered/自发/空/超长/限速/载荷字段)、`test_orm_persister.py`(+DMWrite INSERT/幂等/FK)、`test_persist.py`(+DMWrite 归事件写)、`test_protocol.py`(+DirectMessage 样本+raise、DMDelivered/DMUndelivered 隐私)、`test_receiver.py`(+端到端 DM 帧投达在线收件人)。
- 文档:`messaging.md`(发路标落地 + 决策 2/6/7 + 0038/0039 拆分)、`db.md`(私信事件写落地 + DMWrite 置 dm_records + frozen dataclass 澄清)、`db-migrations.md`(+DMMessage 表)、`wire-protocol-guide.md`(+direct_message/dm_delivered/dm_undelivered)、`TODO.md`。

## 实际改了什么

- **`app/db/dm_records.py`(新)**:`DMWrite(PersistPayload)` frozen dataclass(dedupe_key/from_uid/to_uid/text/created_at)。置 db 层(非 core/records.py):shell 产、core 永不碰。
- **`app/db/models.py`**:`DMMessage`(id PK / dedupe_key unique+index / from_uid FK / to_uid FK+index / text 无 max_length / created_at tz)。
- **`alembic/versions/79d1fd60fc7f_add_dm_message.py`(新)**:autogenerate「add dm_message」;sqlite upgrade/downgrade/重升 round-trip 通过。
- **`app/db/queries.py`**:`load_uids_by_nicks(sm, nicks) -> dict[str,int]`(批量 nick→uid;缺的不在 dict;空入参省查询)。
- **`app/db/orm_persister.py`**:`_apply_event_write` 加 `DMWrite` → `_insert_dm`(SELECT-by-dedupe_key 再 INSERT,同手牌记录幂等式;FK 强制坏 uid 回滚)。
- **`app/shell/persist.py`**:`_state_key` 加 `case DMWrite(): return None`(事件写;import `app.db.dm_records.DMWrite`——`app/db/__init__.py` 无 import,persist.py 仍 SQLAlchemy-free)。
- **`app/wire/client.py`**:`DirectMessage{to_nick,text}` + 注册 CLIENT_MESSAGES + 联合;`to_command` 加 `case DirectMessage(): raise`(shell 路由特例)。
- **`app/wire/server.py`**:`DMDelivered{msg_id,from_nick,text,created_at}` / `DMUndelivered{to_nick}` + 注册(import `datetime`)。
- **`app/shell/messaging.py`(新)**:`route_direct_message`(防护序 空→超长→自发→限速 → `load_uids_by_nicks` → 对端不存在 `DMUndelivered` / 发件人缺行 `INTERNAL` → `put(DMWrite)` 必落 → 在线 `_try_deliver(DMDelivered)`)+ `_try_deliver`(QueueFull 吞、不丢消息、不 drop 收件人连接)。`msg_id=uuid4().hex`、`created_at=datetime.now(utc)`。
- **`app/shell/connection.py`**:`Connection.dm_bucket` + create 建桶(`DM_RATE_BURST`/`DM_RATE_PER_SEC`;与 `chat_bucket` 共用一次 `time.monotonic()`)。
- **`app/shell/receiver.py`**:`run_receiver`/`_frame_to_command` 加 `persist`(+`_frame_to_command` 加 `conns`);拦 `DirectMessage` → `await route_direct_message`,return None(不进 inbox)。
- **`app/shell/lifespan.py`** + **`tests/shell/test_dev_db_e2e.py`** + **`tests/shell/test_receiver.py`**:`run_receiver` 透传 `persist`(15 处调用点:lifespan 1 + dev_db_e2e 1 + test_receiver 13;`_fakes.Shell.persist` 自 0036 已有,无需改)。
- **`app/core/errors.py`**:`CANNOT_DM_SELF`。
- **`app/gameconfig.py`**:`DM_MAX_TEXT_LEN=1000` / `DM_RATE_BURST=5.0` / `DM_RATE_PER_SEC=1.0`(保留清理参数注明随 0039)。
- **`scripts/gen_wire_ts.py`**:`_ts_type` 加 `datetime → "string"`(Pydantic JSON 序列化为 ISO 串);**重生成 `wire.gen.ts`**(+`DMDelivered`/`DMUndelivered`/`DirectMessage`,`created_at: string`,+`CANNOT_DM_SELF` 入 `ErrorCode`)。
- **测**:`tests/shell/test_messaging.py`(新 8:在线投达+落库/离线只落库/对端不存在 DMUndelivered/自发不耗令牌/空/超长/限速/outbound 满丢实时不丢消息);`test_orm_persister.py`(+4:DMWrite INSERT/重放幂等/批内幂等/FK 回滚);`test_persist.py`(+1:DMWrite 归事件写);`test_protocol.py`(+DirectMessage 样本+raise、DMDelivered/DMUndelivered 隐私无牌);`test_receiver.py`(+端到端 DM 帧投达在线收件人 + DMWrite 落库)。
- **文档**:`messaging.md`(§私聊 发路标落地 + 失败回执二分 + 实时尽力而为 + 防护序 + 无 echo + uuid4;§投递与落库表标 0038/0039)、`db.md`(当前实例标 0038 + Persist 接口加「BaseModel 是示意/frozen dataclass/core 产 vs shell 产」注 + DMWrite 标落地 + uuid4)、`db-migrations.md`(DMMessage←DMWrite + 迁移号)、`wire-protocol-guide.md`(§3/§4/§8 加 direct_message/dm_delivered/dm_undelivered)、`TODO.md`(发路划掉、读路标 0039)。

344 全绿(330→344);codegen `--check` 干净;core 无越层 import(grep 复验);迁移 sqlite round-trip 通。

## 自 review

方法:对照 [review.md](../../review.md) 跑对抗式 **4 维 review 子代理工作流**(分层/并发 · 设计正确性 · 代码↔文档/文档↔文档 · 测试/账本;每维独立审查 → 每候选默认反驳)。11 agent、7 候选,**0 code defect / 0 blocker / 0 major-in-code**:两个最高风险面经实查被**正确驳回**,三处确认项全是变更记录自身的账本/覆盖 nit,已当场修。逐维:

- **① 分层 / 不变量 / 并发**:**核心红线全过**——`grep app/core` 无 `await`/`import shell|fastapi|sqlalchemy`;DM 路由全在 Receiver 协程、**绝不读写 world**(私聊定向到人、不 checkout 房);`persist.put` 同步无 await(写缓冲第二生产者,守不变量 3);`app/db` 不 import `app/shell`;**`persist.py` 仍 SQLAlchemy-free**(`import app.db.dm_records` 不拉 sqlalchemy——`app/db/__init__.py` 无 import,dm_records 仅 import core.events,实查确认);`_try_deliver`/路由用 `put_nowait` 不 await;**路由只读 uid、绝不 `await commit`**(唯一写者仍 PersistWriter)。
- **② 代码↔文档**:`route_direct_message` 签名/防护序/失败回执二分(DMUndelivered vs ErrorMessage)/best-effort 投递/msg_id=uuid4 与 messaging.md、db.md、wire-protocol-guide 同步;`datetime→string` codegen 与 `wire.gen.ts`(`created_at: string`)一致;`--check` 干净。
- **③ 文档↔文档**:messaging.md/db.md/db-migrations.md/wire-guide/TODO/本记录交叉引用一致(迁移号 `79d1fd60fc7f`、`CANNOT_DM_SELF`、0038/0039 拆分口径);db.md 加「BaseModel 是示意 / 实为 frozen dataclass / core 产 vs shell 产」澄清,消解早先 DMWrite 示意漂移。
- **④ 数据模型正确性**:`route_direct_message` 端到端 trace——guard 序(自发在限速前拒、不耗令牌,已测)/from_uid·to_uid 不互换(自发先拒故 from≠to)/`DMUndelivered` vs `INTERNAL` 分支/OrmPersister `_insert_dm` SELECT-then-INSERT 幂等 + FK 强制/`_state_key` 归 DMWrite **事件写(追加,绝不覆盖——覆盖会丢消息,已测)**/`DMMessage` model↔迁移↔`DMWrite` 字段对齐/隐私(无 hole_cards/deck,test_protocol 钉死)。**review 报 `findings:[]`**。
- **⑤ 规范**:中文字段注释 + 「为什么」(best-effort 投递/不 drop 收件人/uuid4/防护序);`DM_*` 配置化无裸字面量;新错误码 `CANNOT_DM_SELF` 入 codegen。
- **⑥ 测试**:346 全绿(330→346)。守恒/隐私/边界:在线投达+落库(msg_id==dedupe_key)/离线只落库/对端不存在不落库/自发不耗令牌/空/超长/限速只落第一条/**outbound 满丢实时不丢消息**/DMWrite 不被覆盖(append)/orm INSERT·重放幂等·批内幂等·FK 回滚/端到端 DM 帧投达。**采纳覆盖 nit**:补两条防御臂测(发件人无 DB 行→INTERNAL、DB 读失败→INTERNAL,镜像 `_build_join`)。
- **⑦ 账本**:**采纳并修两处账本不实**——① 范围 header + 打算改什么误列 `tests/shell/_fakes.py` 为本批改动(实查 `git status`/`git log`:`_fakes.Shell.persist` 自 0036 已有、本批未碰),已删并注明;② 「11 处调用点」误计(实为 15:lifespan 1 + dev_db_e2e 1 + test_receiver 13),已订正。打算↔实际一致;提交引用 0038、全英文。

**对抗核实存活 / 采纳 / 驳回**:7 候选——**确认 3(全 nit/minor、全在本变更记录文档,0 code)**:① 账本误列 _fakes.py(两维各报一次,合并修)② 调用点计数 11→15 ③ 两条 INTERNAL 防御臂无测(补)。**驳回 2(均经实查)**:① 「发件人侧 `put_nowait` 不守 QueueFull」——驳回(architecture.md:49 明定「outbound 满 = Sender 卡死 → 丢该连接」即发件人侧文档化行为;且为 0033 既有 receiver 回执模式,本批未引入,非 best-effort 承诺范围);② 「`from_uid` 无索引而 `to_uid` 有」——驳回(有意非对称:0039 只按 `to_uid` 查未读、`from_uid` 无查询者;0038 根本不查 DMMessage;finding 自身 suggested_fix=「No action now」)。

> 批判性自评:本批最该自省的是**「发路落库但 0038 内无读者」**(DMMessage 写进库、补收读它在 0039)——会否成投机死代码?结论否:实时投递与落库**同源同批**产 `DMWrite`、orm INSERT 路径**测穷举**(幂等/FK/批内),是 0037 式「读基座先于消费者」的有据前瞻,且 0039 紧邻补收即读。最高风险面(分层:DM 路由读 DB 但不读 world、不 await commit、persist.py 不沾 sqlalchemy)经 review 实跑锚在既有不变量,非新假设。

## 待办 / 下一步

- **0039**:`DMMarkRead`→`DMReadCursorWrite`(状态写,键 `("dm_cursor",reader,peer)`)+ `DMRead` 回执 + 登录补收(读 DB 未读 → `DMDelivered` 列表 + 未读数;读游标 → 已读回执)+ PersistWriter 保留清理(`DM_READ_RETENTION_SECONDS`/`DM_CLEANUP_INTERVAL_SECONDS` DELETE)。
- 好友/黑名单、富文本@提及、内存未读镜像(消 flush 窗口竞态)——future(messaging.md §待定)。
