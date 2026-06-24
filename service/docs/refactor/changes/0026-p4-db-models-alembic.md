# 0026 · P4(三之一):`app/db/` ORM 模型 + Alembic 重定向 + 基线迁移 + 用法文档

日期:2026-06-24 · 范围:`app/db/`(新 SQLModel 模型)、`alembic/env.py`(重定向到 `app/db`)、`alembic/versions/`(删 4 个原型迁移 + 1 个新基线)、`docs/db-migrations.md`(新,Alembic 用法)、文档同步(db.md / dev.md / TODO)。

## 背景 / 打算改什么

0025 落地 `PersistWriter` + `Persister` 协议(脱真 DB,`NullPersister` 占位)。P4(三)接**真落库**:`db/` ORM 模型 + `to_orm` + `OrmPersister` + Alembic 迁移 + 载入。本篇按 [README §0](../README.md) 拆出**最干净的子缝 = 模式与运行时分离**:

- **本篇(三之一):模式基础** —— `app/db/` SQLModel 模型(对齐 [core/records.py](../../../app/core/records.py) 的 Write 载荷)+ Alembic 重定向到新模型 + 基线迁移 + **Alembic 用法文档**(用户明确要)。模型是纯表定义、不需 async/真 DB;迁移用同步 sqlite 即可本地验证。
- **三之二(下一篇):运行时** —— `to_orm`(Write→ORM 映射)+ `OrmPersister`(`Persister` 实现:async session + `merge`(UPSERT)/`add`(INSERT)+ `ON CONFLICT(dedupe_key) DO NOTHING`)+ async engine(psycopg/aiosqlite)+ lifespan 接真 session 替 `NullPersister` + 载入(Receiver 读 DB 富化 `JoinRoom` 的 uid/loaded)+ 测试(aiosqlite)。

### 设计决策(开工前定)

1. **`app/db/` 是新架构持久化模式的事实源**(README §3 的 `db/`)。原型模型(`app/user/models.py`/`app/handrecord/models.py`)是被取代物——本篇**不删原型模型/路由**(它们仍被原型 `app/main.py` 引用,删除级联大,属后续「原型拆除」),但 **Alembic 不再追踪它们**(env.py 只 import `app.db`)。
2. **重置迁移历史**:原型 4 个迁移(`0_1_0`..`0_1_3`)建的是原型 schema、新架构不用;原型从未上线(重构期、无生产 DB)。故**删 4 旧迁移 + 写 1 个新基线**(down_revision=None),给新 `db/` 一条干净历史。这也是「不留死代码」。
3. **`env.py` 重写**:① **不 import `app.config`**(它读 `.env`、无则崩)——`DATABASE_URL` 改从 `os.environ` 读、缺省本地 sqlite,免 `.env` 也能跑迁移;② 只 `import app.db.models`(显式,不再 os.walk 全仓 `*models*`,避免把原型模型注册进 `SQLModel.metadata` 造表名冲突);③ **删跳过外键的 `render_item` hack**——新架构要真 FK(参与者 → 手牌 / 用户,db.md「同事务」);④ `render_as_batch=True`(sqlite ALTER 友好,postgres 无害)。
4. **User 列精简到 P4 所需**:`id`(uid 主键)/`nickname`(唯一)/`points`(PointsWrite 状态写覆盖此列)。**国密列(salt/rounds/hash/K_user)随 P5 以新迁移加**——正好示范文档的「改模型 → 新迁移」工作流,不提前堆未用列。
5. **datetime 用 `timezone=True`**:Write 载荷带 tz-aware 墙钟(shell 盖),列存 `TIMESTAMP WITH TIME ZONE` 免 tz 丢失。
6. **HandParticipant 复合主键 `(hand_id, uid)`**:一手内一人一行;FK → `handrecord.id` / `user.id`。本篇不挂 ORM `Relationship`(查询期才需,P7 REST 再加),保持模式精简。

## 实际改了什么

- **`app/db/models.py`**(新)+ `app/db/__init__.py`:SQLModel 表 `User`(id=uid PK / nickname unique / points)、`HandRecord`(id PK / dedupe_key unique+index / start_time+end_time `DateTime(timezone=True)` / final_pot)、`HandParticipant`((hand_id, uid) 复合主键 + FK→handrecord.id/user.id / initial_points / final_points)。对齐 `core/records.py` 的 Write 载荷;无 ORM `Relationship`(查询期才需)。
- **`alembic/env.py`** 重写:只 `import app.db.models`(不 os.walk、不 import app.config)、`DATABASE_URL` 读 `os.environ` 缺省 sqlite、删 FK-skip `render_item`、`render_as_batch=True`、offline/online 同步 engine。
- **`alembic/script.py.mako`**:硬带 `import sqlmodel`(autogen 引用 `AutoString` 等,缺则升级 NameError)。
- **`alembic/versions/`**:删 4 原型迁移(`0_1_0`..`0_1_3`)+ 1 新基线(`d07cf4b8828c`,`down_revision=None`,建 user/handrecord/handparticipant)。
- **`docs/db-migrations.md`**(新):Alembic 用法(命令 / 改模型→出迁移 / 铁律,含「过渡期别同进程导两套模型」)。
- **`.gitignore`**:+`*.db`/`*.sqlite*`(本地 sqlite 不入库)。
- **文档同步**:`dev.md` Alembic 段重写(env.py 新行为 + 指向 db-migrations.md)、`db.md`/`coding_principle.md`/`models.md`/`rest.md` 加链/纠正指向(原型 handrecord → `app/db/`)、`TODO` P4 进度。

## 测试 / 验证

无 Python 单测(纯模式 + 迁移,无运行时逻辑)。**Alembic 实测**(sqlite):`alembic upgrade head` 建 3 表(`user`/`handrecord`/`handparticipant`,PK / 唯一索引 `ix_*_dedupe_key`/`ix_*_nickname` / FK `handparticipant.uid→user.id`、`hand_id→handrecord.id` 全对),`alembic current` 到 head,`alembic downgrade base` 干净回退(仅剩 `alembic_version`)。`import app.db.models` 无 `.env` 不崩、metadata 恰含 3 表。**全量 254 测试仍绿**(app/db 新增不影响 core/shell)。

## 自 review(push 前对抗式 7 维)

> 多 agent 对抗式 7 维复审:候选 ~12、确认 **3(全 MINOR)**、其余反驳。**SAFE-TO-COMMIT,无 critical/major/数据/迁移缺陷**——schema metadata 与基线迁移、与 `core/records.py` Write 载荷逐字段(名/类型/可空/PK/unique/FK)核对一致,sqlite upgrade↔downgrade 往返通,offline `--sql` sqlite/postgres 均干净。

确认并已修:

- **MINOR 文档漂移 ×2**:`models.md:52` / `rest.md:33` 仍指原型 `app/handrecord/` 为对齐目标 → 改指 `app/db/models.py`(事实源)+ 链 db-migrations.md。
- **MINOR 潜伏隐患(今日不触发)**:`app/db/models.py` 与原型 `app/user`/`app/handrecord` 同名表(`user`/`handrecord`)挂同一全局 `SQLModel.metadata`,同进程都导会 `InvalidRequestError`。**当前不触发**(`app/__init__.py` 缺失=命名空间包不级联、env.py 只导 app.db、测试不导任何模型集)。→ 已加 db-migrations.md「过渡期别同进程导两套模型」铁律 + 列为 **P4 三之二接 OrmPersister 的前置条件**(先原型拆除或给 app/db 独立 MetaData)。

反驳为 CLEAN:schema/payload 无漂移、`end_time NOT NULL`(core 按约定不带、shell 派发盖 tz-aware,属下一篇 to_orm 义务、已文档化)、env.py 分层与无 `.env` 不崩、FK-skip hack 已删(PRAGMA + offline SQL 验)、`render_as_batch` offline 缺省无害、迁移文件名 slug 截断(纯外观,revision id 才是身份)、`script.py.mako` 的 `import sqlmodel`(基线 `AutoString` 真用、无 linter 误报)。最高 MINOR。

## 待办 / 下一步

- **P4(三之二)运行时**:`to_orm` + `OrmPersister`(async session + UPSERT/INSERT + dedupe ON CONFLICT)+ async engine + lifespan 接真 session + 载入(Receiver 读 DB)+ aiosqlite 测试。**前置**:先做下面「原型拆除」(否则 `OrmPersister` 导 `app.db.models` 与原型模型同进程冲突,见自 review)。
- **原型拆除**(独立改,P4 三之二前必做):删 `app/user`/`app/handrecord`/`app/auth` 原型模型/路由 + `app/main.py`/`app/init.py`,统一到新 `app/db` + shell;或给 `app/db` 独立 `MetaData`/`registry` 隔离。
- 配置收编(P8):`DATABASE_URL` 进 `app/config`(接 `.env`)+ `poker.env`;本篇 env.py 暂直读 `os.environ`。
