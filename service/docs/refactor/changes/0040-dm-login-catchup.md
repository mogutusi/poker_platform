# 0040 · 私信(DM)登录补收(未读收件箱「读」路第二半)

日期:2026-06-29 · 范围:`app/db/queries.py`(+`load_unread_dms`/`load_read_receipts`)、`app/shell/messaging.py`(+`deliver_dm_catch_up`)、`app/shell/receiver.py`(连接时调补收)、测、文档。落地 [messaging.md](../../messaging.md) §私信「登录补收」:连接时 shell 读 DB → 补发未读 `DMDelivered` 列表 + 已读回执 `DMRead` 列表。

## 背景 / 为什么

[messaging.md](../../messaging.md) §私信「未读收件箱」三件:① 发(`DMWrite` 落库 + 在线投 `DMDelivered`,**0038**)、② 读游标(`DMMarkRead`→`DMReadCursorWrite` + 在线回 `DMRead`,**0039**)、③ **登录补收**(本批 **0040**)。补收是让 0038 落的库 + 0039 的游标**真正对离线用户生效**的一环:离线时收到的私信 / 对方的已读进度,在(重)连时一次性补齐——否则 0038/0039 落的库无人读回。

## 关键设计决策(批判性,与 messaging.md 对齐)

1. **补收**无新 wire 报文 / 无迁移 / 无 codegen**——复用 `DMDelivered`/`DMRead`**:messaging.md「登录补收 → `DMDelivered` 列表 + 补回执」,与在线实时投递/回执**同形**。客户端按 `msg_id` 去重(实时 + 补收同一条只显一次),`DMRead` 幂等(同 reader/read_through 重复无害)。故补收 = 纯 **shell 读 DB + enqueue 现有报文**,零协议增量。
2. **走 shell 路由、连接时触发、不进 GameLoop / 不读 world**:`deliver_dm_catch_up(conn, sessionmaker)` 在 Receiver 协程内,投 `Connect` 后、收帧循环前跑一次(读 DB、enqueue 到本连接 `outbound`)。**每次(重)连都跑**(幂等:客户端 `msg_id` 去重);顶替再连也补(新连接需对齐未读)。读 DB 是 shell IO(允许,不变量 2 明示「Receiver 读 DB 允许、读 DB 不读 world」)。
3. **两条只读查询**(`app/db/queries.py`,读路径):
   - `load_unread_dms(sm, to_uid)`:`DMMessage` JOIN `User`(取 from_nick)LEFT JOIN `DMReadCursor`(reader=me, peer=from_uid),WHERE `to_uid=me AND (游标 NULL OR created_at > 游标)`,ORDER BY created_at(旧→新)。未读判据 = 「无游标(从没读过该对端)或该消息晚于游标」。
   - `load_read_receipts(sm, peer_uid)`:`DMReadCursor` JOIN `User`(取 reader_nick),WHERE `peer_uid=me`——「谁把我发的读到了几时」(游标一表两用,messaging.md)。
4. **best-effort,失败/满不致命**:补收 DB 读失败 → log + return(不回错:连接刚建,下次重连重试;非游戏裁定);`outbound` 满 → 停本轮补收(`_enqueue_or_stop` 返 False,余项下次重连补——游标未因补收推进,故不丢)。键全用不可变 uid,wire 转 nick。
5. **flush 窗口竞态(messaging.md 既述,接受)**:刚发未 flush 的 `DMWrite` 仍在写缓冲、未进 DB 时收件人恰登录 → 本轮补收漏**但不丢**(下个 flush 进 DB,下次重连 / 发件人在线实时投可见)。本规模自愈。
6. **未读数**:不单开报文——`DMDelivered` 列表本身即未读(每条未读一帧),客户端按 `from_nick` 计数。专门的未读汇总报文是 future nicety(messaging.md 未强制单独「数」字段)。

## 打算改什么(开工前)

- `app/db/queries.py`:`load_unread_dms(sm, to_uid) -> list[(msg_id, from_nick, text, created_at)]`(旧→新)、`load_read_receipts(sm, peer_uid) -> list[(reader_nick, read_through_ts)]`。
- `app/shell/messaging.py`:`deliver_dm_catch_up(conn, *, sessionmaker)`(解析 me_uid → 两查询 → enqueue `DMDelivered`/`DMRead`,best-effort)+ `_enqueue_or_stop` 守 QueueFull。
- `app/shell/receiver.py`:`run_receiver` 投 `Connect` 后调 `await deliver_dm_catch_up(conn, sessionmaker=sessionmaker)`。
- 测:`tests/shell/test_messaging.py`(补收未读→DMDelivered 旧→新 + 尊重游标只补未读 + 已读回执→DMRead + 空库无补 + me 无 DB 行 no-op + DB 失败 no-op);`tests/shell/test_receiver.py`(端到端:预置 DB 未读 → 连接补收到 ws);queries 单测随 messaging 集成覆盖(或单列)。
- 文档:`messaging.md`(登录补收标落地 0040)、`wire-protocol-guide.md`(连接补收复用 dm_delivered/dm_read)、`TODO.md`。

## 实际改了什么

- **`app/db/queries.py`**:`load_unread_dms(sm, to_uid) -> list[(msg_id, from_nick, text, created_at)]`(`DMMessage` JOIN `User` 取 from_nick + LEFT JOIN `DMReadCursor`(reader=me,peer=from_uid),WHERE to_uid=me AND (游标 NULL OR created_at>游标),ORDER BY created_at)、`load_read_receipts(sm, peer_uid) -> list[(reader_nick, read_through_ts)]`(`DMReadCursor` JOIN `User` 取 reader_nick,WHERE peer=me)+ **`_as_utc(dt)`**(sqlite 读 `DateTime(timezone=True)` 丢 tz→naive,补回 UTC,使补收 wire 形与实时一致、序列化带 Z;见自 review)。import `and_`/`or_`/`datetime`/`timezone` + `DMMessage`/`DMReadCursor`。
- **`app/shell/messaging.py`**:`deliver_dm_catch_up(conn, *, sessionmaker)`(解析 me_uid → `load_unread_dms`/`load_read_receipts` → enqueue `DMDelivered`/`DMRead`;me 无行/DB 失败 → log.warning + return,无回错;best-effort)+ `_enqueue_or_stop`(QueueFull 返 False、停本轮)。import `load_unread_dms`/`load_read_receipts`。
- **`app/shell/receiver.py`**:`run_receiver` 投 `Connect` 后调 `await deliver_dm_catch_up(conn, sessionmaker=sessionmaker)`(收帧循环前,每次连都跑)。import `deliver_dm_catch_up`。
- **无 wire / 无迁移 / 无 codegen**:补收复用 `DMDelivered`/`DMRead`,`wire.gen.ts` 不变(`--check` 干净;server.py 仅改注释,不影响 codegen 产物)。
- **自 review 驱动的修正**:① `_as_utc` tz 归一(见 queries 项,修补收 wire 形 SQLite 漂移);② 既有 `route_direct_message` 两处注释 + `wire/server.py` `DMDelivered` 注释「登录补收 0039」改 **0040**(catch-up 实际落本批,纠正旧前瞻注释)。
- **测**:`tests/shell/test_messaging.py`(+7:未读旧→新有序【+断言 created_at tz-aware 值正确】/ 尊重游标只补未读 / 已读回执 DMRead【+断言 read_through 值 + tz】/ outbound 满首条即停 / 空库无补 / me 无行 no-op / DB 失败 no-op;+ `_seed_dms` 助手)、`tests/shell/test_receiver.py`(+端到端:DB 预置 bob→alice 未读 → alice 连接补收到 ws 的 dm_delivered)。既有 receiver 测不破(种子库无 DM → 补收无投;未建表 → 补收 best-effort no-op)。
- **文档**:`messaging.md`(§私聊 登录补收标落地 0040 + §投递与落库 补收 row 落地 + 函数/查询名)、`wire-protocol-guide.md`(§8 补收对前端透明、复用 dm_delivered/dm_read、按 msg_id 去重)、`TODO.md`(补收划掉、清理标 0041)。

365 全绿(357→365);codegen `--check` 干净、`wire.gen.ts` 未变;core 无越层 import。

## 自 review

方法:对照 [review.md](../../review.md) 跑对抗式 **4 维 review 子代理工作流**(SQL 正确性 · 分层/并发 · 代码↔文档 · 测试/账本;每维独立审 → 每候选默认反驳)。15 agent、11 候选,**抓到 1 个真问题(minor,已修)**:这是 0038–0040 三批里 review 首次抓到的实质性发现——补收 wire 时间戳的 tz 漂移。逐维:

- **④ SQL 正确性(最高风险面)**:`load_unread_dms` 的 LEFT JOIN 游标过滤经实查**正确**——`outerjoin(reader=me,peer=from_uid) + or_(read_through IS NULL, created_at > read_through)` 正确「无游标=全未读 / 晚于游标=未读」,边界 `created_at == read_through` 排除(已读,inclusive),`ORDER BY created_at` 旧→新;`load_read_receipts` 正确返「谁读了我的(peer=me,reader_nick=对方)」,无 reader/peer 互换。`test_catch_up_respects_read_cursor`(T0 含→m1 排除、m2 补)钉死边界。
- **① 分层 / 并发**:`deliver_dm_catch_up` 经实查**只读 DB、不读/写 world、不 await commit**(非第二写者);Receiver 协程内 `put_nowait`(`_enqueue_or_stop`);core 纯(grep);连接生命周期不破(补收在 `try` 内、失败 best-effort return、不泄漏半初始化连接);每连都跑对既有未建表测安全(best-effort no-op)。
- **②③ 代码↔文档**:补收复用 `DMDelivered`/`DMRead`「无新协议」属实(`git diff` 确认 server.py 仅注释、`wire.gen.ts` 未变、`--check` 干净);messaging.md/wire-guide/TODO 同步。
- **⑤ 规范**:`_as_utc` 注释讲清「为什么」(sqlite 丢 tz);无裸字面量。
- **⑥ 测试**:365 全绿(357→365)。守恒/边界:未读旧→新有序 + **created_at tz-aware 值正确**(tz 漂移回归守门)/ 尊重游标边界 / 已读回执值 + tz / outbound 满首条停 / 空 / me 无行 / DB 失败 no-op / 端到端连接补收。
- **⑦ 账本**:打算↔实际一致;采纳并修账本 nit(测计数 +7 catch-up + `_seed_dms` 助手、总 357→365)。

**对抗核实存活 / 采纳 / 驳回**:11 候选——**确认 5(采纳全修)**:① **[minor·真问题] 补收 wire 时间戳 tz 漂移**——sqlite 读 `DateTime(timezone=True)` 回 naive,补收的 `DMDelivered.created_at`/`DMRead.read_through` 序列化**无 `Z`**,而实时路径(`datetime.now(utc)`,aware)带 `Z`,**同一条消息实时 vs 补收时间戳串不一致**,破 wire-guide「同形」契约(3 个 agent 独立实证复现)。影响:仅显示(dedup 按 msg_id、无丢数据、游标不受影响)、且 postgres 不复现(timestamptz 返 aware)、前端现把该字段当 opaque string——故 minor 非 major。**修**:`_as_utc` 在查询边界把 naive 补 UTC + 回归测断言补收 `created_at`/`read_through` tz-aware 且值正确。② 既有「登录补收 0039」注释(messaging.py ×2 + server.py DMDelivered)→ 改 0040(catch-up 实落本批)。③ 已读回执测补断言 `read_through` 值 + tz。④ 测计数账本歧义 → 厘清。⑤ outbound 满截断分支补测。**驳回 6**:server.py DMDelivered「0039」当作 0040 引入的矛盾(驳:server.py 不在 0040 diff、该注释 HEAD 既有——但**本批顺手纠为 0040**,因 catch-up 此刻才落地)/「+7 over-attribute」(驳:`+7` = 6 测 + `_seed_dms` 助手,文内已列;另一 agent 反向报为 nit,本批厘清表述)/ outbound 满分支无测「is_real=false」(驳:已正确、非缺陷;**但仍补测求全**)/ 自 review 占位(驳:占位本就 push 前填,本段即填)。

> 批判性自评:本批 review **首次抓到真 bug**,价值正在于此——「绿测 ≠ 可提交」再次兑现(tz 漂移测全绿却藏 wire 不一致)。最该自省:为何我写补收时漏了 tz?因 sqlite 读回丢 tz 是**只读路径独有**(0028 起 HandRecord 只写不读、从未把 DB datetime 上 wire,0040 是首个 DB-datetime→wire 路径),无先例护栏。修在查询边界(漂移产生处)+ 回归测锚死,并诚实标注 postgres 不复现 / 现仅显示影响,非夸大。最高风险面(游标 SQL 过滤)review 实跑确认正确,未被 tz 噪声掩盖。

## 待办 / 下一步

- **0041/future**:保留清理(PersistWriter 周期 DELETE 已读满 `DM_READ_RETENTION_SECONDS` 的 `DMMessage`,`DM_CLEANUP_INTERVAL_SECONDS`)——见 db.md / messaging.md。
- 未读汇总「数」报文、富文本@提及、内存未读镜像(消 flush 窗口竞态)——future。**好友/黑名单不做**(用户明示)。
