# 数据库迁移(Alembic 用法)

## 一句话定位

表结构的事实源是 [`app/db/models.py`](../app/db/models.py)(SQLModel)。Alembic 把模型改动生成为版本化的迁移脚本,`upgrade`/`downgrade` 在真库上增量执行。

工作流:改了模型就生成一支迁移、审一遍、再 `upgrade`。别手改库。

> 不需要写自定义脚本。Alembic 是命令行驱动(`alembic <cmd>`),配置在 [`alembic.ini`](../alembic.ini) + [`alembic/env.py`](../alembic/env.py),迁移文件在 `alembic/versions/`,由 `--autogenerate` 生成——你只审、不手写整支。

## 本仓的接线(已配好,了解即可)

**`alembic.ini`**:`script_location = alembic`;`prepend_sys_path = .` 使 `app` 可导入;`sqlalchemy.url` 只是占位,真 URL 由 `env.py` 覆盖。

**`alembic/env.py`**

- 只 `import app.db.models`,把表注册到 `SQLModel.metadata`(即 `target_metadata`),保证单一事实源。
- `DATABASE_URL` 经 [app/config.py](../app/config.py) 的 `settings` 读取(优先级 env > `.env`,0045)。缺省是本地 sqlite `sqlite:///./poker.db`,所以没有 `.env` 也能跑。
- 不跳过外键;`render_as_batch=True`,让 sqlite 也能 ALTER。

**`alembic/script.py.mako`**

模板硬带一行 `import sqlmodel`。autogen 会引用 `sqlmodel.sql.sqltypes.AutoString` 等列类型,缺这行升级时会 `NameError`(见 [changes/0026](refactor/changes/0026-p4-db-models-alembic.md))。

## 跑命令前:在哪、连哪个库

- 工作目录 `cd service`,可执行文件 `service/.venv/bin/alembic`。
- 目标库由 `DATABASE_URL` 环境变量选:本地缺省 = sqlite 文件 `service/poker.db`(已 gitignore);生产 / 联调 = Postgres,`postgresql+psycopg://user:pass@host:5432/poker`,驱动是 `psycopg`,见 [pyproject](../pyproject.toml)。

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
| 看将执行的 SQL(不连库;走 Alembic 的离线模式) | `.venv/bin/alembic upgrade head --sql` |

## 改了模型 → 出一支迁移(主流程)

1. 改 [`app/db/models.py`](../app/db/models.py):加表 / 加列 / 改约束。

2. 自动生成迁移。autogenerate 比对的是「模型 metadata ↔ 当前库」:

   ```bash
   cd service
   DATABASE_URL="sqlite:///./poker.db" .venv/bin/alembic revision --autogenerate -m "add user salt rounds"
   ```

   它要求库已经是当前 head,否则报 "Target database is not up to date";先 `upgrade head` 再生成。

3. 审生成的迁移(`alembic/versions/<hash>_*.py`)。autogen 不完美,务必逐行读 `upgrade`/`downgrade`:
   - 列改名会被当成「删一列 + 加一列」,会丢数据。手改成 `op.alter_column(..., new_column_name=...)`。
   - 数据回填、`server_default`、复杂约束抓不全,按需手补。
   - sqlite 上 ALTER 走 `batch_alter_table`(模板已开 `render_as_batch`)。

4. 应用:`.venv/bin/alembic upgrade head`。

迁移文件随模型改动一起 commit,它们是同一个变更单元。

> 第一支基线迁移是在空库上 `--autogenerate` 出的「建全表」(`down_revision=None`),0026 落地。之后每次改模型都接在它后面增量出新版本。

## 铁律 / 注意点

**同名表别在一个进程里注册两次到同一 `SQLModel.metadata`**

否则会 `InvalidRequestError: Table 'X' is already defined`。原型模块曾与 `app/db/models.py` 定义同名表(`user`/`handrecord`),[0027](refactor/changes/0027-prototype-teardown.md) 拆除后冲突源已消除;现在 `alembic/env.py`、runtime、测试都只导 `app/db.models` 一处,新增表只加进 `app/db/models.py`。

**模型是源,库是投影**

只改模型 + 出迁移,绝不手 `ALTER TABLE`——手改会与迁移历史漂移。

**必审 autogen 产物**

尤其是改名 / 删列 / 数据迁移。一支迁移聚焦一个逻辑改动,便于回滚。

**`import sqlmodel` 必须在迁移里**

模板已带,别删那行。

**dev shell 与生产的建表方式不同**

- dev shell 用 `create_all` 引导建表、不跑 Alembic,`create_all` 的 `checkfirst` 保证幂等。见 [app/db/engine.py](../app/db/engine.py) + [changes/0029](refactor/changes/0029-p4-db-backed-dev-shell.md)。
- 生产 / 集成用 Alembic 建表,绝不靠 `create_all`——那样不留迁移历史。两者别在同一个库上混用。

**新架构对齐**

`app/db/` 的表对齐 delayDB 的 Write 载荷。载荷定义在 [core/records.py](../app/core/records.py) 与 [app/db/dm_records.py](../app/db/dm_records.py),落库经 `PersistWriter`/`OrmPersister`,见 [db.md](db.md)。

表 ← 载荷的对应关系:`User.points` ← `PointsWrite`;`HandRecord` + `HandParticipant` ← `HandRecordWrite`/`ParticipantWrite`;`DMMessage` ← `DMWrite`,迁移 `79d1fd60fc7f`(见 [changes/0038](refactor/changes/0038-dm-send-deliver.md));`DMReadCursor` ← `DMReadCursorWrite`,迁移 `7ff9cb0a8db1`(见 [changes/0039](refactor/changes/0039-dm-read-cursor.md))。

几处需要展开的列改动:

- **`HandRecord.room`**:denormalized 列,迁移 `010d8e8a08d7`,供 `GET /hands?room=` 用(见 [changes/0052](refactor/changes/0052-handrecord-room-column.md))。
- **`User` 国密鉴权列 `name`/`hash_password`/`k_user`**:迁移 `49417b108733`(见 [changes/0056](refactor/changes/0056-p5-user-auth-columns-authenticate.md))。三列均 nullable,因为 `name` 是唯一列、不能用常量 `server_default` 回填,可空才是安全的增量做法。
- **K_user 双钥轮换**:迁移 `b8ca88a687af`(见 [changes/0066](refactor/changes/0066-p5-kuser-rotation.md))。`k_user` **重命名**为 `k_cur`,并扩列 `k_cur_ver`/`k_cur_until`/`k_prev`/`k_prev_ver`/`k_prev_until`。重命名必须手改成 `alter_column`——autogen 误判成删+加会丢已发密钥。这是「必审 autogen 产物」的现行案例。

隐私:表只存结果——uid + 初/末筹码 + 池额;私信正文里也没有底牌牌堆。绝不含底牌 / 牌堆。

**唯一 DB 写者**

运行时落库只经 `PersistWriter`,无行锁 / `with_for_update`(见 [db.md](db.md))。读路径(REST 查询)用各自的请求级 session;实时判定以内存为准。

**本地 sqlite ≠ 生产 Postgres**

类型 / 方言有差异,例如 `TIMESTAMP WITH TIME ZONE`。本地 sqlite 用来快速验证迁移能跑通;最终要在 Postgres 上 `upgrade head` 验收。

## 与其它文档

- 写通道机制(写缓冲、双缓冲、重试、drain):[db.md](db.md)。
- 存储模型(内存权威 + 载入一次 + 回滚):[storage.md](storage.md)。
- 工程环境(Poetry/venv):[dev.md](dev.md)。
