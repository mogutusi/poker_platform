# 数据库迁移(Alembic 用法)

## 一句话定位

**表结构的事实源是 [`app/db/models.py`](../app/db/models.py)(SQLModel);Alembic 把「模型的改动」生成为版本化的迁移脚本,`upgrade`/`downgrade` 在真库上增量执行。** 改了模型就生成一支迁移、审一遍、再 `upgrade`——别手改库、别让模型与库漂移。

> 需要 py 脚本吗?**不需要写自定义脚本。** Alembic 是**命令行驱动**(`alembic <cmd>`):配置在 [`alembic.ini`](../alembic.ini) + [`alembic/env.py`](../alembic/env.py),迁移文件在 `alembic/versions/`。`env.py` 和迁移文件本身是 Python,但 `env.py` 一次配好不常动,迁移文件**由 `--autogenerate` 生成**(你只审、不手写整支)。下文所有操作都是敲 `alembic` 命令。

## 本仓的接线(已配好,了解即可)

- `alembic.ini`:`script_location = alembic`、`prepend_sys_path = .`(使 `app` 可导入)。`sqlalchemy.url` 是占位,真 URL 由 `env.py` 覆盖。
- `alembic/env.py`:① `import app.db.models` 把新架构表注册到 `SQLModel.metadata`(`target_metadata`);**只导这一个**(单一事实源;0026 时还需借此避开原型同名表冲突,原型已于 0027 拆除);② `DATABASE_URL` 从**环境变量**读、缺省本地 sqlite(`sqlite:///./poker.db`),所以**无 `.env` 也能跑迁移**;③ 不跳过外键、`render_as_batch=True`(sqlite 也能 ALTER)。
- `alembic/script.py.mako`:迁移模板里**硬带 `import sqlmodel`**——autogen 会引用 `sqlmodel.sql.sqltypes.AutoString` 等列类型,不带这行升级时会 `NameError`(本仓踩过,见 [changes/0026](refactor/changes/0026-p4-db-models-alembic.md))。

## 跑命令前:在哪、连哪个库

- **工作目录**:`cd service`(`alembic` 在 `service/.venv/bin/alembic`,从 `service/` 根跑)。
- **目标库**:用 `DATABASE_URL` 环境变量选。
  - **本地试**:缺省即本地 sqlite 文件 `service/poker.db`(已 gitignore);或显式 `DATABASE_URL="sqlite:///./poker.db"`。
  - **生产 / 联调 Postgres**:`DATABASE_URL="postgresql+psycopg://user:pass@host:5432/poker"`(驱动用 `psycopg`,见 [pyproject](../pyproject.toml))。

```bash
cd service
# 本地:不设 DATABASE_URL 即用缺省 sqlite ./poker.db
.venv/bin/alembic upgrade head
# 生产:显式指定
DATABASE_URL="postgresql+psycopg://u:p@host/poker" .venv/bin/alembic upgrade head
```

## 常用命令

| 目的 | 命令 |
|---|---|
| 升到最新 | `.venv/bin/alembic upgrade head` |
| 回退一步 / 到底 | `.venv/bin/alembic downgrade -1` · `.venv/bin/alembic downgrade base` |
| 当前库在哪个版本 | `.venv/bin/alembic current` |
| 迁移历史 | `.venv/bin/alembic history --verbose` |
| 看将执行的 SQL(不连库) | `.venv/bin/alembic upgrade head --sql`(离线模式) |

## 改了模型 → 出一支迁移(主流程)

1. 改 [`app/db/models.py`](../app/db/models.py)(加表/加列/改约束)。
2. **自动生成**迁移(Alembic 比对「模型 metadata ↔ 当前库」出差异):
   ```bash
   cd service
   DATABASE_URL="sqlite:///./poker.db" .venv/bin/alembic revision --autogenerate -m "add user salt rounds"
   ```
   - autogenerate **要求库已是当前 head**(否则报 "Target database is not up to date":先 `upgrade head` 把库追平,再生成)。
3. **审生成的迁移**(`alembic/versions/<hash>_*.py`)——autogen **不完美**,务必读 `upgrade`/`downgrade`:
   - 列改名会被它当「删一列 + 加一列」(丢数据)→ 手改成 `op.alter_column(..., new_column_name=...)`。
   - 数据回填、`server_default`、复杂约束 autogen 抓不全,按需手补。
   - sqlite 上 ALTER 走 `batch_alter_table`(模板已开 `render_as_batch`)。
4. **应用**:`.venv/bin/alembic upgrade head`。提交时**迁移文件随模型改动一起 commit**(同一变更单元)。

> 第一支(基线)迁移是 `revision --autogenerate` 在**空库**上生成的「建全表」(`down_revision=None`),已在 0026 落地;之后每次改模型都接在它后面增量出新版本。

## 铁律 / 注意点

- **同名表别在一个进程里注册两次到同一 `SQLModel.metadata`**(通用告诫):否则 `InvalidRequestError: Table 'X' is already defined`。**历史**:原型 `app/user/models.py`/`app/handrecord/models.py` 曾与 `app/db/models.py` 定义同名表(`user`/`handrecord`),这条冲突一度是 **P4 三之二接 `OrmPersister` 的前置**(同进程导两套即崩);**[0027](refactor/changes/0027-prototype-teardown.md) 拆除原型五包后该冲突源已消除**,`OrmPersister` 可放心 `import app.db.models` 起真 engine。`alembic/env.py`、新 runtime、测试都只导 `app/db.models` 这一处;**新增表只加进 `app/db/models.py`**,别在别处另起同名定义。
- **模型是源,库是投影**:只改模型 + 出迁移;**绝不手 `ALTER TABLE` 改库**(会与迁移历史漂移)。
- **必审 autogen 产物**:尤其改名/删列/数据迁移,autogen 可能丢数据;一支迁移聚焦一个逻辑改动,便于回滚。
- **`import sqlmodel` 必须在迁移里**(模板已带);新建模板别删那行。
- **dev shell 用 `create_all` 引导建表、不跑 Alembic**(见 [app/db/engine.py](../app/db/engine.py) `create_all` + [changes/0029](refactor/changes/0029-p4-db-backed-dev-shell.md)):dev 脚手架免迁移工具链(`checkfirst` 幂等,与已有表无冲突);**生产/集成用 Alembic 迁移建表**,绝不靠 `create_all`(不留迁移历史)。两者别在同一库混用。
- **新架构对齐**:`app/db/` 的表对齐 delayDB Write 载荷(`User.points`←`PointsWrite`、`HandRecord`+`HandParticipant`←`HandRecordWrite`/`ParticipantWrite`,载荷在 [core/records.py](../app/core/records.py);`DMMessage`←`DMWrite`,载荷在 [app/db/dm_records.py](../app/db/dm_records.py),私信迁移 `79d1fd60fc7f`,见 [changes/0038](refactor/changes/0038-dm-send-deliver.md));落库由 shell 的 `PersistWriter`/`OrmPersister` 经此(见 [db.md](db.md))。**隐私**:表只存结果(uid + 初/末筹码 + 池额 / 私信正文无底牌牌堆),绝不含底牌/牌堆。
- **唯一 DB 写者**:运行时落库只经 `PersistWriter`,**无行锁 / `with_for_update`**(见 [db.md](db.md));读路径(REST 查询)各自请求级 session,实时判定一律以内存为准。
- **本地 sqlite ≠ 生产 Postgres**:类型/方言有差异(如 `TIMESTAMP WITH TIME ZONE`)。本地 sqlite 适合快速验证迁移能跑通;**最终在 Postgres 上 `upgrade head` 验收**。

## 与其它文档

- 写通道机制(写缓冲、双缓冲、重试、drain):[db.md](db.md)。
- 存储模型(内存权威 + 载入一次 + 回滚):[storage.md](storage.md)。
- 工程环境(Poetry/venv):[dev.md](dev.md)。
