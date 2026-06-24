# 0025 · P4(二之一):PersistWriter 写回协程 + Persister 抽象 + drain

日期:2026-06-24 · 范围:`app/shell/persist.py`(+`Persister`/`NullPersister`/`PersistWriter`)、`app/gameconfig.py`(+delayDB 旋钮)、`app/shell/lifespan.py`(DevShell 接 PersistWriter:start 跑循环 / stop drain)、`tests/shell/test_persist_writer.py`(新)、文档同步(db.md / TODO)。

## 背景 / 打算改什么

0024 落地了 `WriteBuffer` 双缓冲(纯同步)。本篇接 delayDB 的 **async 控制流**:`PersistWriter`——周期 `swap` → 落库,失败回灌、毒丸、优雅 drain(见 [db.md](../../db.md)「PersistWriter 主循环 / 失败与重试 / 优雅关闭」)。这是 db.md 着墨最多的并发红线(**先 swap 后 await**、回灌**更新者优先**、drain 必须有),正好把 0024 的 `swap`/`requeue` 用起来。

按 [README §0](../README.md) 质疑粒度:P4(二)余量 = `PersistWriter`(async)+ `to_orm` + `db/` ORM 模型 + Alembic 迁移 + 真 Persister。现状 DB 栈是 **Postgres/psycopg(同步驱动)、无 aiosqlite**,且 `app/config.py` 读 `.env`(无则崩)——真 ORM + Alembic + async session 是**需要 DB 基建的重活**,应单独审慎落地。最干净的缝:

- **`PersistWriter` 控制流 + drain** 抽象在一个 **`Persister` 协议**(`async flush(dirty, appends)`)之后 → **脱真 DB、用 fake 即可穷举**(swap-then-await、失败回灌、毒丸、drain 边界)。这正是 db.md 最强调的并发正确性面,且与 0018「fake-ws 测 shell」同法。
- 真 `OrmPersister`(`to_orm` + SQLModel 模型 + session)+ Alembic 迁移 = **下一篇(P4 三)**,届时备 DB 基建。

**本篇(0025)**:`PersistWriter` + `Persister` 协议 + `NullPersister`(dev 无 DB:丢弃 + 日志)+ gameconfig DB 旋钮 + 接进 DevShell(start 跑循环、stop drain)+ fake 单测。**不碰**真 ORM/Alembic/session。

### 设计决策(开工前定)

1. **`Persister` 协议作落库后端缝**:`async flush(dirty, appends) -> None`,失败抛异常(PersistWriter 据此整批回灌)。真现 = `to_orm` + `session.merge/add` + `commit`(P4 三);`NullPersister` = dev 丢弃 + DEBUG 日志(dev 端点本就无 DB,见 lifespan)。这样 `PersistWriter` 不依赖 SQLAlchemy,纯 fake 可测。
2. **抽 `flush_once()` / `drain()` 供直测**(同 timer.py 把 `tick()` 从 `run()` 循环抽出):`run()` 只是 `while True: sleep; flush_once` 薄壳;测试不睡眠直接驱动 `flush_once`/`drain`,避免脆弱的时序睡眠。
3. **配置经 `__init__` 覆盖**(`flush_interval_s`/`max_retry`/`drain_timeout_s`,缺省取 gameconfig;同 timer 的 `timeout_s` 覆盖法),测试传小值,免 monkeypatch 模块常量。
4. **毒丸**:同批连续失败 `>= DB_WRITE_MAX_RETRY` 次 → 丢批 + CRITICAL(别卡死后续,bug 信号),清失败计数。
5. **drain 有界**:`now()`(`time.monotonic`,shell 许可,同 timer)+ `DB_DRAIN_TIMEOUT_MS` 上限;超时 CRITICAL + 放弃(进程要退,接受该窗口)。
6. **接 DevShell**:`start()` 加 `persistwriter.run()` task;`stop()` 先 cancel 生产者(gameloop/timer/writer 循环)再 `await drain()` 终结 flush(db.md drain 序:停 GameLoop → PersistWriter 终结 flush)。dev 用 `NullPersister`(无 DB),故 dev 缓冲实际会被 drain 清空——不影响 `test_dev_smoke`(它用 `tests/shell/_fakes.Shell` 假壳、不起 PersistWriter)。

## 实际改了什么

- **`app/shell/persist.py`**:
  - `Persister` 协议(`async flush(dirty, appends)`,失败抛异常)+ `NullPersister`(dev 无 DB:丢弃 + DEBUG 日志)。
  - `PersistWriter`:`run()`(周期 `sleep` → `flush_once`)/ `flush_once()`(先 `swap` 后 `await persister.flush`;**`except CancelledError` 先回灌再 re-raise**、`except Exception` 未达毒丸回灌 / 达 `max_retry` 丢批 CRITICAL;成功复位 `fail_streak`)/ `drain()`(循环 flush 至空或超 `DB_DRAIN_TIMEOUT_MS`,失败回灌时按周期节流防自旋)。可调参数经 `__init__` 覆盖(缺省取 gameconfig)。
- **`app/gameconfig.py`**:+`DB_FLUSH_INTERVAL_MS`/`DB_WRITE_MAX_RETRY`/`DB_DRAIN_TIMEOUT_MS`(带默认常量,P8 接 poker.env)。
- **`app/shell/lifespan.py`**:`DevShell` 持 `PersistWriter(persist, NullPersister())`;`start()` 加 `persistwriter.run()` task;`stop()` cancel 生产者后 `await persistwriter.drain()` 终结 flush。
- **`tests/shell/test_persist_writer.py`**(新,13 测试)。
- **文档同步**:`db.md` PersistWriter 主循环伪码重写为 `flush_once` + `Persister` seam(+ 0025 偏离注 + 毒丸「达 N 次」+ 日志分级毒丸 CRITICAL);`TODO` P4 标 0025 进度。

## 测试

`tests/shell/test_persist_writer.py`(13:初版 11 + 自 review 补 2),**全量 254 绿**(0024 的 241 + 本篇 13)。FakePersister 替身(可控失败 / gate 暂停)直驱 `flush_once`/`drain`:空 no-op、落库清空、失败回灌后成功、**回灌更新者优先(gate 时序:250 先入缓冲再 requeue 旧 100,真考 setdefault)**、毒丸达阈值丢批 + 复位、**双缓冲(swap 后 put 不污染在飞批)**、**取消落 flush 半途批回灌待 drain 补落(MAJOR 修复用例)**、**成功复位 fail_streak(fail→success→fail 不误触毒丸)**、drain 清空 / 持久失败经毒丸兜底不挂死 / 超时返回残留、run 周期 flush 冒烟、NullPersister 静默吞批。

## 自 review(push 前对抗式 7 维)

> 多 agent 对抗式 7 维复审:候选 ~14、确认 **6**、反驳/去重 8+。**最高 MAJOR 1 项(定级 MUST-FIX-FIRST)→ 已修**。

- **① 并发(最高风险)**:**MAJOR——关闭取消落在 in-flight flush 半途静默丢批**:`flush_once` 先 `swap` 后 `await persister.flush`、仅 `except Exception`,而 Py3.12 `CancelledError` 属 `BaseException` 绕过该 except;`stop()` 无条件 `cancel()` 写者,若取消落在真 persister 的 await commit 半途,已 swap 出的批既不落库也不回灌,随后 drain 也捞不回(db.md drain 红线正为防此)。**修**:`flush_once` 加 `except asyncio.CancelledError: self._buf.requeue(...); raise`(drain 在写者 task 收割后单线跑、重落幂等故安全);补 `test_cancel_during_flush_requeues_for_drain` 端到端钉。NullPersister 的 flush 无内部 await 故 dev 不触发,但真 OrmPersister(P4 三)必触,先堵。其余并发反驳 CLEAN:swap 跨 await(swap/requeue/is_empty 全同步、swap 在唯一 await 前)、run/drain 不并发竞 swap(stop 先收割写者 task)、毒丸不楔死、drain 有界(整批 swap 一轮清空 / 毒丸 / deadline 三退出)。
- **⑥ 测试充分**:3 项 MINOR(变异存活)→ 已补:① updater-wins 原测试 `put(250)` 在 requeue **之后**、未真考 `setdefault`(改 gate 时序,250 先入缓冲);② 成功复位 `fail_streak` 无测(补 fail→success→fail、`max_retry=2` 不误毒丸);③ drain 节流 sleep 无测——记为**接受**(非正确性,deadline 才是终止界;加计时断言易 flaky,不补)。
- **②③ 文档同步**:1 项 MINOR——db.md 主循环伪码未随 `Persister`/`flush_once`/`drain` seam 更新 → 已重写 + 加「0025 偏离」注(镜像 0024 体例)。
- **⑥ NIT**:`max_retry=1` 首败即丢(`>=`)→ 注释/gameconfig 标明「达 N 次=总尝试数」,非缺陷(默认 10)。
- **反驳为 CLEAN**:分层(persist 属 shell,asyncio/time 许可;core 不 import 之)、config 单位换算同 timer、NullPersister 诚实、`DB_FLUSH_MAX_BATCH` 故意缺省(db.md 标可选)、`>=`/swap-before-await 变异均被现有测试杀。

确认项全修,修后 **254 绿** + core 纯度通过。

## 待办 / 下一步(补充)

- **DevShell start/stop 接线无自动化测试**(`test_dev_smoke` 用 fake `Shell`、不起 PersistWriter):dev 入口经手动跑端点验;组件本身已单测。可后补一个 DevShell 生命周期 async 测。
- **关闭序的 inbox 排空**:`stop()` 当前直接 cancel gameloop(未先排空 inbox 把在途命令处理完),属 P8 lifespan drain 收尾范畴(TODO「lifespan drain」),dev 无害。
- (其余见上「P4 三:真落库」。)

## 待办 / 下一步

- **P4(三):真落库** —— `OrmPersister`(`Persister` 实现:`to_orm` 把 `PointsWrite`/`HandRecordWrite` 映射 SQLModel + `session.merge`(UPSERT)/`add`(INSERT)+ `commit`,事件写用 `INSERT ... ON CONFLICT(dedupe_key) DO NOTHING`)+ `db/` 模型(`User` 含 `uid`/积分、`HandRecord`+`Participant` 对齐 `HandRecordWrite`,`end_time` 由 shell 派发时盖)+ Alembic 迁移 + lifespan 接真 session 工厂(替 `NullPersister`)+ 载入(`JoinRoom` 经 Receiver 读 DB 富化 uid/loaded)。需要 DB 基建(选 async 驱动:psycopg async 或 aiosqlite-for-test)。
- gameconfig 收编(P8):本篇 DB 旋钮先以带默认常量落 `gameconfig`,P8 接 `poker.env` + bounds。
