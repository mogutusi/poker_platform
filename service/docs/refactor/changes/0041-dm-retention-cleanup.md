# 0041 · 私信(DM)保留清理(未读收件箱收尾:已读满期即删)

日期:2026-06-29 · 范围:`app/gameconfig.py`(`DM_READ_RETENTION_SECONDS`/`DM_CLEANUP_INTERVAL_SECONDS`)、`app/shell/persist.py`(`Persister.cleanup_dms` 协议 + `NullPersister.cleanup_dms` + `PersistWriter.maybe_cleanup` 周期清理)、`app/db/orm_persister.py`(`OrmPersister.cleanup_dms` DELETE)、测、文档。落地 [db.md](../../db.md) / [messaging.md](../../messaging.md) §私信「已读即删 + 未读保活」的保留清理。

## 背景 / 为什么

[messaging.md](../../messaging.md) §私信 / [db.md](../../db.md):私信**未读保活、已读再留 `DM_READ_RETENTION_SECONDS` 后清**;清理是 DB 写(DELETE),**归唯一写者 PersistWriter**(不另起写库协程),周期 `DM_CLEANUP_INTERVAL_SECONDS`。DM 发(0038)/读游标(0039)/补收(0040)已完;不清理则 `DMMessage` 永久累积。本批补这最后一环——DM 收件箱功能闭环。

## 关键设计决策(批判性,与 db.md / messaging.md 对齐)

1. **清理归 PersistWriter,DELETE 落 OrmPersister**(db.md 唯一写者铁律):PersistWriter `run()` 周期循环里附带 `maybe_cleanup()`(门控 `DM_CLEANUP_INTERVAL_SECONDS`),到点调 `persister.cleanup_dms(cutoff)`。**绝不另起协程写库**(守 db.md 不变量 5)。真 DELETE 在 `OrmPersister.cleanup_dms`(`Persister` 协议新方法),PersistWriter 不碰 DM 表细节、只管「周期 + cutoff」。
2. **删除判据(db.md):「已读 且 `created_at < now - 保留期`」**。`cutoff = now - DM_READ_RETENTION_SECONDS`(PersistWriter 用注入的 `now` 墙钟算,shell 可读钟;core 才禁)。**已读** = 存在收件人对该发件人的游标且读过该条(`DMReadCursor(reader=to_uid, peer=from_uid).read_through_ts >= DMMessage.created_at`,与 0040 未读判据 `created_at > read_through` 互补、inclusive 一致)。**未读永不删**(无论多老);**已读但未过期**(`created_at >= cutoff`)留。
3. **年龄基于 `created_at` 非「读的时刻」**(db.md 明定 `created_at < now-保留期`):简化——不为每条存「读时间」,保留期从消息**创建**起算。残留:已读很久的老消息按创建时间清(够用;严格「读后 N 天」需另存读时刻,本规模不必)。
4. **cutoff 由 PersistWriter 算、传 datetime 给 Persister**:retention/周期配置 + `now` 注入都在 PersistWriter(可测,同 timer/dispatch 注入法);`cleanup_dms(cutoff)` 边界干净——Persister 只「删已读且早于 cutoff 的私信」,不知 retention 语义。
5. **best-effort,失败仅 log**:清理失败(DB 抖动)→ `log.error` + 跳过,不影响 flush 主职、不回灌(清理幂等,下周期/重启再删)。取消落在清理半途 → `session.begin()` 回滚半删,下次重跑(DELETE 幂等)。
6. **NullPersister 返 0**(dev 无 DB 无清理);dev shell 用 OrmPersister,清理随既有 `PersistWriter.run()` 自动跑(无 lifespan 改动)。

## 打算改什么(开工前)

- `app/gameconfig.py`:`DM_READ_RETENTION_SECONDS = 604800`(7 天)、`DM_CLEANUP_INTERVAL_SECONDS = 3600`(每小时)。
- `app/shell/persist.py`:`Persister` 协议 +`async cleanup_dms(cutoff) -> int`;`NullPersister.cleanup_dms` 返 0;`PersistWriter.__init__` +`cleanup_interval_s`/`retention_s`/`now` 形参 + `_last_cleanup`;`run()` 循环加 `await self.maybe_cleanup()`;`maybe_cleanup()`(门控周期 → 算 cutoff → `persister.cleanup_dms` → log)。import `datetime`/`timezone`/`timedelta`。
- `app/db/orm_persister.py`:`OrmPersister.cleanup_dms(cutoff)`(`DELETE FROM dmmessage WHERE created_at < cutoff AND EXISTS(已读游标)`,一短事务,返删除行数)。import `delete`/`datetime` + `select` 既有。
- 测:`tests/shell/test_orm_persister.py`(cleanup 删已读+过期、留未读(虽老)、留已读未过期、空返 0、计数)、`tests/shell/test_persist_writer.py`(`maybe_cleanup` 门控:未到周期不调、到点调 + cutoff=now-retention;`FakePersister.cleanup_dms` 记录;`NullPersister.cleanup_dms` 返 0)。
- 文档:`db.md`(保留清理标落地 0041)、`messaging.md`(同)、`config.md`/gameconfig 注释、`TODO.md`(DM 收件箱全闭环)。

## 实际改了什么

- **`app/gameconfig.py`**:`DM_READ_RETENTION_SECONDS = 604800`(7 天)、`DM_CLEANUP_INTERVAL_SECONDS = 3600`(每小时),置 delayDB 配置段(db.md 归属)。
- **`app/shell/persist.py`**:`Persister` 协议 +`async cleanup_dms(cutoff) -> int`;`NullPersister.cleanup_dms` 返 0;`PersistWriter.__init__` +`cleanup_interval_s`/`retention_s`/`now` 形参 + `_last_cleanup`(init=monotonic);`run()` 循环 flush 后加 `await self.maybe_cleanup()`;`maybe_cleanup()`(门控周期 → `cutoff = now() - timedelta(retention)` → `persister.cleanup_dms` → 失败 ERROR 吞 / 成功 INFO 记删除数)。import `datetime`/`timedelta`/`timezone`/`Callable`。
- **`app/db/orm_persister.py`**:`OrmPersister.cleanup_dms(cutoff)`(`DELETE dmmessage WHERE created_at < cutoff AND EXISTS(已读游标:`DMReadCursor(reader=to_uid, peer=from_uid).read_through_ts >= created_at`));一短事务、返 `rowcount or 0`)。import `delete`(+既有 select)。
- **无 wire / 无迁移 / 无 codegen**:清理是落库后端行为,不碰协议/表结构(`DMMessage`/`DMReadCursor` 0038/0039 已建);`wire.gen.ts` 不变。**无 lifespan 改动**:dev shell 既有 `PersistWriter.run()` 自动跑清理(OrmPersister)。
- **测**:`tests/shell/test_orm_persister.py`(+7:删已读+过期 / 留未读虽老 / 留已读未过期 / 空返 0 + **3 边界**:created_at==cutoff 留(锁严格 `<`)/ read_through==created_at 删(锁 inclusive `>=`)/ 错对端游标不过匹配(锁 peer==from_uid 关联))、`tests/shell/test_persist_writer.py`(+4:`maybe_cleanup` 未到周期不调 / 到点调 + cutoff=now-retention + 刚清不重复 / 清理抛错被吞 / `NullPersister.cleanup_dms` 返 0;`FakePersister` +`cleanup_dms` 记录)。
- **文档**:`db.md`(§注意点 私信保留清理标落地 0041 + 机制)、`messaging.md`(§持久化 保留多久标落地 0041 + cleanup_dms/maybe_cleanup)、`TODO.md`(清理划掉 + 标「私信收件箱功能闭环」)、gameconfig 注释。

376 全绿(365→376);codegen `--check` 干净、`wire.gen.ts` 未变;core 无越层 import。

## 自 review

方法:对照 [review.md](../../review.md) 跑对抗式 **4 维 review 子代理工作流**(SQL 正确性 · 调度/分层 · 代码↔文档 · 测试/账本;每维独立审 → 每候选默认反驳)。7 agent、3 候选,**0 code defect**:两个高风险维(SQL 正确性、调度/分层)经 agent **写脚本实证**确认正确,doc-sync 维 `findings:[]`。逐维:

- **④ SQL 正确性(最高风险面)**:`OrmPersister.cleanup_dms` 的关联 EXISTS 经 agent **6 用例 sqlite 脚本实证**全对——`reader_uid==to_uid AND peer_uid==from_uid AND read_through_ts >= created_at`(收件人读过该条,inclusive,正是 0040 未读判据 `created_at > read_through` 的互补);未读(无匹配游标)永不删、已读未过期(`created_at >= cutoff`)留、方向不互换、关联非常量。`rowcount or 0` 纯日志用(不影响删哪些行)。
- **① 调度 / 分层**:`maybe_cleanup` 门控 `time.monotonic()`、**await 前先更 `_last_cleanup`**(慢/失败清理不会下 tick 重触)、best-effort(异常 `log.error` + return、不回灌、不破 flush 循环)、**不引入第二 DB 写者**(走同一 PersistWriter task,OrmPersister 自有短事务);取消半途由 `session.begin()` 回滚;`now()` 读墙钟在 shell(PersistWriter)合法、core 仍纯(grep)。
- **②③ 代码↔文档**:`findings:[]`——db.md/messaging.md/TODO/本记录与代码一致;无迁移/无 codegen/无 lifespan 改动属实;config 值 604800/3600 对齐;`wire.gen.ts` 未变(`--check` 干净)。
- **⑤ 规范**:`cleanup_dms`/`maybe_cleanup` 注释讲「为什么」(唯一写者 / inclusive / best-effort / cutoff);配置具名注释带单位。
- **⑥ 测试**:376 全绿(365→376)。KEEP 与 DELETE 双向断言 + **3 边界 mutation-killing**(实跑验:`>=`→`>` 破 read_through==created_at 删测、`<`→`<=` 破 created_at==cutoff 留测,均 FAIL,改回即绿)+ 调度门控/cutoff 值/错误吞/NullPersister。
- **⑦ 账本**:打算↔实际一致;测计数 +7(orm)/+4(writer)、总 365→376;提交引用 0041、全英文。

**对抗核实存活 / 采纳 / 驳回**:3 候选——**确认 1(minor,采纳修)**:**清理谓词的等值边界无测**——代码正确,但既有测时间戳相隔数月、两处单字符变异(`>=`→`>`、`<`→`<=`)**能逃过整套测**(agent 实证)。补 3 边界测(created_at==cutoff 留 / read_through==created_at 删 / 错对端游标不过匹配)并**实跑确认杀变异**。**驳回 2**:① rowcount MySQL 方言隐患(驳:rowcount 纯日志用、`or 0` 兜底、MySQL 非目标后端,候选自评「No bug / No code change」)② 自 review 占位(驳:占位本就 push 前填,本段即填)。

> 批判性自评:本批 0 真 bug,但 review 的价值仍兑现——它抓到「最高风险面(等值边界)虽实现正确却无测护栏,变异能逃」。这正是「绿测 ≠ 可提交」的另一面:不只抓错代码,也抓**对的代码缺回归护栏**。补的边界测经实跑确认 mutation-killing(非空断言)。DM 私信收件箱至此功能闭环(发/读游标/补收/清理),四批 review 共抓 1 真 bug(0040 tz 漂移)+ 多处账本/护栏 nit,无一空手。

## 待办 / 下一步

- DM 私信收件箱**至此功能闭环**(发 0038 / 读游标 0039 / 登录补收 0040 / 保留清理 0041)。
- 富文本@提及、内存未读镜像、未读汇总「数」报文——future。**好友/黑名单不做**(用户明示)。
- 配置收编(P8):`DM_*` 等随 `gameconfig` env 化 + `poker.env(.example)`。
