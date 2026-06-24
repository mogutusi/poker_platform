# 0029 · P4(三之二-b):DB-backed dev shell —— 接真 `OrmPersister` + 种子/载入 dev 用户 + 端到端

日期:2026-06-24 · 范围:`app/shell/lifespan.py`(DevShell 接真 async engine + `create_all` + 种子 dev 用户 + 从 DB 载入积分建 world + `OrmPersister` 替 `NullPersister` + 关闭 dispose engine)、`tests/shell/`(DB 端到端冒烟)、文档同步(connection.md / db-migrations.md / storage.md / TODO)。

## 背景 / 打算改什么

[0028](0028-p4-orm-persister.md) 落地 `OrmPersister` 写路径 + 测试就绪,但**未接进运行的 shell**(dev 仍 `NullPersister`,丢弃)。本篇把它接活:dev shell 用真 async DB,使「命令 → reduce → `Persist` → `OrmPersister` → 真 DB 行」端到端跑通,并把全局积分**从 DB 载入内存**(兑现「内存权威 + 载入一次」)。

### 为什么需要「种子 + 载入」(0028 自 review 已点明)

`OrmPersister` 要落 `HandParticipant`(FK→`user.id`)+ `PointsWrite` UPDATE,**dev 用户必须先在 DB 有行**,否则 FK 解析不到 / UPDATE 空命中。原型注册(P5)未建,故本篇在 dev 启动**幂等种子** dev 用户进 DB,再**从 DB 载入**其积分建 `world`(而非用 `DEV_START_POINTS` 常量直建)——这样 world 的积分真来自 DB,重启后承接上次落库的变更。

### 拆分(本篇做启动期载入,per-join wire-load 留 0030)

架构的「载入一次」发生在 **`JoinRoom`**(用户进房时 shell 读 DB → `JoinRoom(room, uid, loaded)` → reduce 装入)。`JoinRoom` 命令 + reduce `_join_room` 已就绪([0022](0022-join-room-state-snapshot.md)),**缺的是 wire `join_room` 报文 + Receiver 读 DB 富化**。dev shell 现用「预置用户在房 WATCHING」绕开 `JoinRoom`([0018](0018-d-dev-shell.md)),故本篇用**启动期整体载入**(`build_dev_world` 从 DB 读)作 dev 的载入实现;**per-join 的 wire-load(client `join_room` + Receiver DB 读)留 [0030]**——那是 wire + receiver 的活,与本篇的 lifespan 装配正交。

### 设计决策(开工前定)

1. **DevShell 异步启动**:DB 建表/种子/载入是 IO,须 `await`。`__init__` 只建脱 IO 的部件(engine/sessionmaker/inbox/conns/persist/timer/persistwriter);新增 `async setup()` 做 `create_all`+种子+载入+建 world+建 dispatcher/gameloop;lifespan 在 `yield` 前 `await shell.setup()`。
2. **engine 可注入**:`DevShell(engine=None)` 缺省 `make_engine()`(`DATABASE_URL` 或 `sqlite+aiosqlite:///./poker.db` 文件,dev 持久);测试注入 in-memory(StaticPool)。
3. **dev 用 `create_all` 引导建表,不跑 Alembic**:dev 脚手架免迁移工具链;生产用 Alembic(见 db-migrations.md)。`create_all` `checkfirst` 幂等,与已有表无冲突。
4. **种子幂等**:按 `DEV_USERS` 顺序 `id=i+1`、`nickname`、`points=DEV_START_POINTS`,仅当该 `id` 不在 DB 才 INSERT(重启不重置积分)。
5. **关闭 dispose engine**:`stop()` 在 drain 后 `await engine.dispose()` 释放连接池(顺关闭序,见 connection.md/db.md)。

## 实际改了什么

**`app/shell/lifespan.py`**(重构):

- `DevShell.__init__(engine=None)`:只建脱 IO 部件(`engine`=注入或 `make_engine()`、`sessionmaker`、`inbox`、`conns`、`persist`、`timer`、`persistwriter=PersistWriter(persist, OrmPersister(sessionmaker))`);`world`/`dispatcher`/`gameloop` 置 None 待 setup。
- `async setup()`:`create_all`(dev 建表)→ `seed_dev_users`(幂等)→ `load_dev_users`(从 DB 读 `{nick:(uid,points)}`)→ `build_dev_world(loaded)`(world 积分来自 DB)→ 建 `dispatcher`/`gameloop`。
- `start()`:起三协程(断言已 setup);`stop()`:cancel + `drain()` + **`await engine.dispose()`**。
- 模块函数:`seed_dev_users`(仅当 `id` 不在才 INSERT,`id=序号+1`)、`load_dev_users`、`build_dev_world(loaded)`(签名变:从常量 → DB 载入值)。
- `lifespan` ctx 在 `yield` 前 `await shell.setup()`;ws 端点不变。

**`tests/shell/test_dev_db_e2e.py`**(新,4 测试):setup 种子+载入 + OrmPersister 接上、种子幂等(重启保积分)、买入 `PointsWrite` 落 DB、一手牌 `HandRecord`+participants 落 DB(经 dispatch 盖 `end_time`)。

**文档同步**:`connection.md`(lifespan 段加 dev shell DB-backed 落地注:engine/create_all/seed/load/OrmPersister/dispose + per-join 载入 0030)、`db-migrations.md`(dev 用 `create_all`、生产用 Alembic 的边界)、`TODO.md`(0028/0029 勾完 + 0030 余项)。

## 测试 / 验证

全量 **270 passed**(266 → +4 e2e)。

- **DevShell.setup**(in-memory aiosqlite):world 6 用户、积分=种子值且**来自 DB**、`uid=序号+1`、`room=dev`;DB 真有 6 行;`persistwriter._persister` 是 `OrmPersister`(非 Null)。
- **种子幂等**:同 engine 改 DB alice→42 后再 `setup`,world 载入 42(**没被种子重置**回 `DEV_START_POINTS`)。
- **端到端状态写**:dev shell gameloop 收 `SitDown`+`BuyIn` → `PointsWrite` → `flush_once` → DB `User.points = 1000-100`。
- **端到端事件写**:seated heads-up 经 gameloop `StartHand`+`FOLD` → 手结束 `HandRecordWrite`(`end_time` 由 dispatch 盖)→ `flush_once` → DB `HandRecord`(`end_time` 非 None、`dedupe_key=r1:1`)+ 两 `HandParticipant`(uid 1/2,FK 解析到种子用户)。
- **真文件 DB 冒烟**(scratch,手验非自动):启动建表+种子+载入(alice 1000);改 DB→333 + dispose + 二次 `setup`(重启)→ 载入 333(**承接落库、不重置**),6 用户。
- dev shell `create_app()` 仍 boot;`gen_wire_ts.py --check` OK;core 纯度 / `app/db ⊥ shell` 不变。

## 自 review(push 前对抗式 7 维)

> 方法:3 维度 finder(生命周期/并发 · 测试/规范 · 文档/架构)× 各 finding 逐条独立 verifier「默认反驳」(含 async repro)。候选 33,确认真问题去重后 **6 条可行动(全已修)**,其余为正向确认(verifier 实证正确)。**SAFE-TO-PUSH**,无 CRITICAL/无 MAJOR 产品缺陷(唯一 MAJOR 是 dev 启动期边界 KeyError,已转明确报错)。

**对抗式抓到 + 已修:**

- **(①)`build_dev_world` 在 dev 用户缺失时裸 `KeyError`**:`DEV_USERS` 改名后撞旧 dev 库同 id 行(seed 按 id 跳过)→ load 拿不到新名 → `loaded[nick]` 崩。已在 `setup()` 加**明确报错**(指向「删 dev 库重启」),把 cryptic KeyError 换成可操作信息。
- **(⑥)端到端事件写测试的 uid 耦合脆弱**:原手写 `world.users[*].uid=1/2` 对齐独立的 DB 种子(`make_table` enumerate 自 0 / 种子自 1),二者可悄悄漂移。改为 **DB 种子从 `world` 派生**(`_seed_users_from_world`,uid 同源)+ 断言用 `world` 的 uid——单一事实源,不会漂。
- **(⑤)测试种子裸字面量 `points=1000`** → 用 `gameconfig.DEV_START_POINTS`(随上一条重构一并消除)。
- **(⑥)测试访问私有 `persistwriter._persister` 做接线断言**(与行为测试冗余)→ 删;OrmPersister 接上由「买入/手牌落 DB」的行为测试证明(若仍 Null 则不落库)。
- **(③)`lobby.md` 残留「dev 无 DB」** → 更新为 dev 已 DB-backed(0029,启动整载)+ per-join 真载入留 0030。
- **(②/③)`storage.md` §① 启动期载入未明文许可** → 加「启动初始化例外」:内存空时 lifespan 直读 DB 初始化 world 是允许的(无既有状态可重载);运行期载入仍走命令+reduce。

**逐维核(verifier repro 实证):**

- **① 分层/生命周期/并发**:`setup()` 先建 world 后建 gameloop、`start()` 断言已 setup;关闭序 cancel → drain → `engine.dispose()` 正确(repro:关闭前缓冲非空的买入,drain **真落库**,重启读到 750 非 1000);`flush_once` 取消时回灌再 re-raise(不丢);双 dispose 安全;模块导入零 DB IO;`OrmPersister` 在 `__init__` 建好、setup 后才被 world 路径用。✓
- **② 代码↔文档**:connection.md dev shell 落地注、db-migrations.md create_all 边界、TODO 与代码一致。✓
- **③ 文档↔文档**:lobby/storage 的 dev-DB / 载入故事已对齐(去除上述两处陈旧/缺口);无残留死链。✓
- **④ 数据模型**:无模型改动;world 积分来自 DB、用户 WATCHING 无座 ⇒ 无积分双计 / world↔DB 启动不发散(verifier 确认)。✓
- **⑤ 规范**:新函数全类型标注;`DEV_*` 取自 `gameconfig`(非裸字面量);测试字面量已收编;无死代码(`NullPersister` import 干净移除)。✓
- **⑥ 测试充分**:270 全绿;4 e2e 非空洞(全链 gameloop→reduce→dispatch→buffer→OrmPersister→DB;end_time 经 dispatch 盖;种子幂等不会因错误原因通过);`build_dev_world` 签名变更无其他调用方。✓
- **⑦ 流程账本**:记录「打算↔实际」对照(含拆分论证);TODO 勾项 + 0030 余项;提交全英文引用 0029。✓

**正向确认(verifier 判 REFUTED / 实证正确)**:启动期整载在 storage.md 下可许;关闭 drain 序合 db.md;core 纯度 / `app/db ⊥ shell` 保持;无积分双计;种子幂等正确;OrmPersister 接线正确。

## 待办 / 下一步

- **0030(P4 三之二-c)per-join wire-load**:wire `client.py` 加 `JoinRoom`(`join_room{room}`)+ Receiver 收到时按连接 nick 读 DB(`uid`/`points`)构 `JoinRoom(room, uid, loaded)` + dev shell 改为「用户连接进大厅 → 主动 join_room」(去掉预置在房绕过);重 codegen。届时 `build_dev_world` 的启动整载退役为「真 per-join 载入」。
- **P8 配置收编**:`DATABASE_URL` 进新建 `app/config.py`;dev 的 `create_all` vs 生产 Alembic 的边界写进 config/dev 文档。
