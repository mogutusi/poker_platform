# 0028 · P4(三之二-a):`OrmPersister` 写路径 + `to_orm` + async engine + `end_time` 盖戳

日期:2026-06-24 · 范围:`app/db/engine.py`(新,async engine + session 工厂)、`app/db/orm_persister.py`(新,`to_orm` + `OrmPersister`)、`app/core/records.py`(`HandRecordWrite` 加 `end_time` 字段)、`app/shell/dispatch.py`(派发 `Persist(HandRecordWrite)` 时盖 `end_time` 墙钟)、`pyproject.toml`(加 `aiosqlite`)、`tests/shell/`(aiosqlite 穷举)、文档同步(db.md / records 注释 / README §3 / TODO)。

## 背景 / 打算改什么

[0026](0026-p4-db-models-alembic.md) 落 `app/db/` 模型 + Alembic;[0027](0027-prototype-teardown.md) 拆原型解除 metadata collision。本篇接 **P4(三)的运行时写路径**:把 [persist.py](../../app/shell/persist.py) 的 `Persister` 协议(0025 留的缝,dev 用 `NullPersister`)接上**真落库后端** `OrmPersister`。

### 拆分(本篇只做写路径,wiring + 载入留 0029)

P4 三之二原计划一锅端(`OrmPersister` + lifespan 接真 session + 载入 Receiver 读 DB)。按 [README §0](../README.md) 拆最干净的缝:

- **本篇(三之二-a)写路径**:`to_orm`(Write 载荷 → ORM)+ `OrmPersister`(`Persister` 实现:async session 一批一短事务,状态写 UPDATE / 事件写 INSERT + 幂等)+ `app/db/engine.py`(async engine/session)+ `end_time` 盖戳 + **aiosqlite 穷举测试**。**不碰 dev shell 装配**——`OrmPersister` 作为已测就绪组件交付,经 `PersistWriter(buf, persister)` 现成缝接入。
- **三之二-b(0029)wiring + 载入**:lifespan 用 sqlite/postgres async engine + `OrmPersister` 替 `NullPersister`、**种子 dev 用户进 DB**(否则 `HandParticipant.uid` FK → `user.id` 解析不到、`PointsWrite` UPDATE 空命中)、Receiver 读 DB 富化 `JoinRoom` 的 `uid`/`loaded`。**dev 用户必须先在 DB 才能跑真落库** ⇒ wiring 与载入天然同篇。

### 设计决策(开工前定)

1. **`PointsWrite` 状态写 = 定向 `UPDATE user SET points`,不是整行 merge/UPSERT**:`User` 表有 `nickname`(NOT NULL unique)等 `PointsWrite` 不拥有的列;`session.merge(User(id=uid, points=...))` 会把 `nickname` 写成 NULL 破约束。**内存权威 + 载入一次**保证写积分时 user 行必已存在(`JoinRoom` 从 DB 载入过),故 `UPDATE ... WHERE id=uid` 是正确映射(行不存在则 0 命中、无害)。**这与 db.md 早先「UPSERT/merge」措辞不符 → 同步改 db.md**:状态写落库语义是「覆盖实体当前状态」,当实体有 Write 不覆盖的列时,落地为**定向列 UPDATE**(只盖 Write 携带的列),不是整行替换。
2. **事件写幂等 = 单写者下 `SELECT by dedupe_key` 再 `INSERT`,不用方言相关的 `ON CONFLICT`**:`PersistWriter` 是全进程唯一 DB 写者 ⇒ 无并发 INSERT 竞争 ⇒ 先查 `dedupe_key` 在不在、不在才插,race-free 且**跨方言**(sqlite/postgres 同一份代码,免 `dialects.sqlite/postgresql` 二分)。`dedupe_key` 的 unique 索引仍是兜底(真撞 → IntegrityError → 整批回滚,下批 SELECT 见到即跳)。db.md 原写 `INSERT ... ON CONFLICT DO NOTHING` → 改述为「单写者下 SELECT-then-INSERT;ON CONFLICT 是要 DB 层强制时的等价替代」。
3. **手牌 + 参与者同一短事务**:`HandRecord` 先 INSERT/flush 取自增 `id`,再用该 `id` INSERT 各 `HandParticipant`(FK);整单一事务,失败整批回滚(`PersistWriter` 回灌重试,幂等安全)。
4. **`end_time` 由 shell 在 dispatch 盖墙钟**(db.md 既有设计,本篇兑现):core 不读钟 ⇒ `HandRecordWrite.end_time` 由 core 留 `None`;`HandRecord.end_time` 是 NOT NULL,语义是「手结束时刻」≈ Persist 产出时刻,**不是 flush 时刻**(flush 可能滞后/重试)。故在 `dispatch`(Persist 产出的单点)盖 `now()`,不在 `OrmPersister.flush`。给 `HandRecordWrite` 加 `end_time: datetime | None = None`;`Dispatcher` 收可注入 `now` 时钟(默认真钟,便于测试定值)。
5. **async driver = `aiosqlite`(dev/测试 sqlite)**;postgres async 走现有 `psycopg`(v3,`postgresql+psycopg://`),无需 asyncpg。`DATABASE_URL` 从 `os.environ` 读(同 alembic),缺省 `sqlite+aiosqlite:///./poker.db`。
6. **`to_orm`/`OrmPersister`/engine 落 `app/db/`,不落 `shell/persist.py`**:README §3 提案把 `to_orm` 挂 `shell/persist.py`,但那样 SQLAlchemy 会渗进 `persist.py`(`WriteBuffer`/`PersistWriter` 现状纯 asyncio、脱 DB 可测,正是 0025 抽 `Persister` 协议的目的)。把 ORM 实现放 `app/db/`(挨着它映射的模型)使 `persist.py` 保持 SQLAlchemy-free;`OrmPersister` 靠结构化协议(duck typing)满足 `Persister`,**不 import shell**(只 import `core.records` + `db.models` + sqlalchemy)。→ 同步改 README §3 / db.md 的落点描述。

## 实际改了什么

**新增:**

- **`app/db/engine.py`**:`database_url()`(读 `os.environ`,缺省 `sqlite+aiosqlite:///./poker.db`)、`make_engine(url, **kw)`(sqlite 装 `PRAGMA foreign_keys=ON` 连接监听器,使 FK 与 postgres 一致强制)、`make_sessionmaker(engine)`(`expire_on_commit=False`)、`create_all(engine)`(测试/无 Alembic dev 引导建表;生产用迁移)。
- **`app/db/orm_persister.py`**:`OrmPersister`(`async flush(dirty, appends)`:`async with sessionmaker() / session.begin()` 一批一短事务)。状态写 `_apply_state_write`:`PointsWrite` → `UPDATE user SET points WHERE id=uid`(定向列,保 nickname)。事件写 `_insert_hand_record`:`SELECT HandRecord.id WHERE dedupe_key` 在则跳;否则校验 `end_time` 非 None(契约)→ INSERT `HandRecord`、`flush()` 取自增 id → INSERT 各 `HandParticipant`(FK)。不 import shell(结构化协议满足 `Persister`)。
- **`tests/shell/test_orm_persister.py`**:aiosqlite 内存库(StaticPool)穷举 11 测试。

**改:**

- **`app/core/records.py`**:`HandRecordWrite` 加 `end_time: datetime | None = None`(core 留 None;字段含义注释指明由 shell 在 dispatch 盖)。
- **`app/shell/dispatch.py`**:`Dispatcher` 加可注入 `now` 时钟(默认 `datetime.now(timezone.utc)`);`Persist(HandRecordWrite)` 派发时若 `end_time is None` 则 `dataclasses.replace(p, end_time=self._now())` 再 `put`(手结束墙钟 ≈ 产出时刻,非 flush 时刻)。
- **`tests/shell/test_dispatch.py`**:加 `end_time` 盖戳测试 + 非手牌记录原样入缓冲断言。
- **`pyproject.toml` / `poetry.lock`**:加 `aiosqlite ^0.22.1`(async sqlite driver,dev/测试/dev-shell;postgres 走现有 psycopg)。
- **文档同步**:`db.md`(两类写表 state-write 改「定向 UPDATE」/ 主循环伪码注释 / 事件写幂等 SELECT-then-INSERT / 新增「0028 落地」段点明落点+语义)、`refactor/README.md` §3(`persist.py` 注释:to_orm/OrmPersister/engine 落 `db/`)、`TODO.md`(P4 三之二写路径 0028 + 0029 余项)。

## 测试 / 验证

全量 **266 passed**(254 → +12:test_orm_persister 11 + dispatch 盖戳 1)。新测覆盖:

- 状态写定向 UPDATE 只盖 points、**保住 nickname**;给最新值即落最新;**user 行不存在 → 0 命中无害**(载入一次保证生产期行已存在)。
- 事件写 record+participants 同事务 INSERT、字段/FK 对齐;**dedupe_key 幂等**(重放只一行);**end_time None → ValueError 契约**;**批内原子**(状态写+中途抛错事件写 → 整批回滚,points 不变、无 record)。
- **FK 强制**(参与者 uid 无对应 user → IntegrityError + 整批回滚)——sqlite 经 `make_engine` 的 `PRAGMA foreign_keys=ON` 生效。
- **PersistWriter 端到端**:`flush_once` 经 OrmPersister 落库 + 清空;**失败回灌**(FK 坏 → 回灌、缓冲非空待重试)。
- 分层复验:`grep` `app/db` **不 import shell**(OrmPersister 靠结构化协议);`core/records.py` 无 sqlalchemy/async/db。
- **sqlite tz 注意**:`DateTime(timezone=True)` 在 sqlite 读回是 naive(UTC 墙值保留、tz 标签丢失;postgres 保留)——测试比较墙值,已在测试注释 + 本文标注。

## 自 review(push 前对抗式 7 维)

> 方法:3 维度 finder(正确性/钱·分层/测试·文档)× 各 finding 逐条独立 verifier「默认反驳」(含 python/pytest repro 复现)。候选 24,**确认真问题去重后 5 条可行动(全已修,无 CRITICAL/MAJOR 代码缺陷)**;其余为正向确认(verifier 跑 repro 实证代码正确)。**SAFE-TO-PUSH**。

**对抗式抓到 + 已修:**

- **(③)db.md `Persist接口` 的 `end_time: datetime` 与代码 `datetime | None` 矛盾**(「`datetime` 非空」+「core 留空」自相矛盾)→ 改 db.md 为 `datetime | None`(core 产出 None、shell dispatch 盖、落库前必非 None)。
- **(⑤)`HandRecordWrite.end_time` 注释独占一行,与邻里字段「行内注释」不一致** → 改回行内简短注释。
- **(⑥)测试计数漂移**:加了第 11 个 ORM 测试(批内 dedupe)后实为 **266**(orm 11 + dispatch 1),记录/TODO 原写 265/10 → 订正。
- **(⑥)缺「同批同 dedupe_key 两份」覆盖** → 加 `test_hand_record_dedupe_within_single_batch`(验批内 SELECT 见到刚 `flush` 的行而跳过,只一行)。
- **(②/⑦)0026 记录的 P4 三之二「UPSERT/merge + ON CONFLICT」前瞻已被本篇修正** → 在 0026 待办加后续落地修正指针(最终设计以 0028 / db.md 为准)。

**逐维核(verifier repro 实证):**

- **① 分层/不变量**:`grep` 复验 `app/db` 不 import shell(`OrmPersister` 靠结构化协议满足 `Persister`,签名 `flush(self, dirty, appends)` 与 `persist.py` 协议一致;`StateKey` 内联 `tuple[str,...]` 免 import shell);`core/records.py` 无 sqlalchemy/async/db(`end_time` 只是 metadata 字段);`dispatch` 盖 `end_time` 无 `await`、`dataclasses.replace` 产**新** frozen 实例不改原 event payload(守不变量 7);墙钟读在 shell(dispatch)非 core。✓
- **② 代码↔文档**:db.md 两类写表/主循环伪码/事件写幂等/「0028 落地」段 + README §3 落点 + records 注释,verifier 逐条对代码核**一致**(end_time 类型订正后)。✓
- **③ 文档↔文档**:storage.md/core.md 的 end_time 故事与新代码一致;db.md 内部无残留「merge/ON CONFLICT 为唯一法」的矛盾(已标 SELECT-then-INSERT 为落地、ON CONFLICT 为等价替代)。✓
- **④ 数据模型正确性**(repro 实证):状态写定向 UPDATE **保住 nickname**(整行 merge 会写 NULL,已规避);`User.id<-uid` 映射正确;事件写 record→`flush()`取id→participants 同事务、FK 正确;**幂等**(跨批 + 批内)、**批内原子回滚**(中途抛错状态写也回滚)、**FK 强制**(sqlite PRAGMA 生效,verifier repro 确认 IntegrityError)。✓
- **⑤ 规范**:新字段行内注释、`flush` 参数补类型标注(`dict[StateKey, PersistPayload]`/`list[PersistPayload]`,只 import core 不破分层)、无魔法数/死代码;`aiosqlite` 经 poetry 加(pyproject + lock,无删项)。✓
- **⑥ 测试充分**:266 全绿;覆盖守恒无关(写路径不算钱)+ **隐私**(记录无 hole_cards/deck,verifier 确认)+ 边界(空 user UPDATE 无害、批内/跨批 dedupe、end_time 契约、批内原子、FK、PersistWriter 端到端 + 失败回灌)。`PointsWrite` 0 命中 = 设计内(载入一次保证生产期行已存在),已测。✓
- **⑦ 流程账本**:记录开工「打算」↔ 收工「实际」对照(含 0028 拆分 + 落点偏 README §3 的论证);TODO 勾项;提交全英文引用 0028。✓

**正向确认(verifier 判 REFUTED / 实证正确)**:`OrmPersister` 状态/事件写语义正确且不 import shell(CRITICAL 级确认)、core 纯度保持、隐私不变量保持、CancelledError 经 flush 传播由 PersistWriter 回灌(脱 DB 的 PersistWriter 测试已覆盖该控制流)、end_time 盖在 dispatch(非 flush)是正确设计、sqlite FK hook 实证生效(非「fragile」)。

## 待办 / 下一步

- **P4 三之二-b(0029)**:lifespan async engine + `OrmPersister` 替 `NullPersister`、种子 dev 用户进 DB(FK/积分 UPDATE 才有命中)、Receiver 读 DB 富化 `JoinRoom`(`uid`/`loaded`)、`create_all` 或 Alembic 初始化 dev 库、端到端冒烟(命令穿 reduce → Persist → OrmPersister → DB 行)。
- **P8 配置收编**:`DATABASE_URL` 进新建的 `app/config.py`(`pydantic-settings`),`OrmPersister`/engine/alembic 统一从那读,不再各自 `os.environ`。
