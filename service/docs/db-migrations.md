# 数据库迁移与部署(Alembic 用法)

## 一句话定位

表结构的事实源是 [`app/db/models.py`](../app/db/models.py)(SQLModel)。Alembic 把模型改动生成为版本化的迁移脚本,`upgrade`/`downgrade` 在真库上增量执行。

工作流:改了模型就生成一支迁移、审一遍、再 `upgrade`。别手改库。

> 本篇兼两用:**日常改模型**看 §3 起;**换台机器部署到 Postgres**从 §0 按编号顺着做。
>
> §0 的编号顺序是硬的。跳步的后果不是报错,是**看起来成功了但库是错的**——最常见的两种见 §0.3 与 §0.4。

---

## §0 部署到 Postgres:从零到能跑

> 本节的每一条都在 **PostgreSQL 16** 上实跑验证过(0095 审计)。在此之前本仓的迁移**只在 sqlite 上跑过**,pg 侧是纸面推理——现在不是了:6 支迁移在空 pg 库上 `upgrade head` 全通,`alembic check` 无漂移,链本身没有 pg 不兼容的写法。

### 0.1 前置:驱动能不能 import(过不了就别往下走)

**`poetry install` 之后 `import psycopg` 很可能失败。** [pyproject](../pyproject.toml) 声明的是裸 `psycopg (>=3.2.10,<4.0.0)`,不带 extra;lock 锁的是纯 Python 包,它需要**系统 libpq**。干净的 Linux 机器上没有:

```
ImportError: no pq wrapper available.
- couldn't import psycopg 'binary' implementation: No module named 'psycopg_binary'
- couldn't import psycopg 'python' implementation: libpq library not found
```

这不是理论风险——**本仓开发机现在就是这个状态**。二选一:

```bash
# ① 推荐:换成自带 libpq 的 binary 轮子(零系统依赖),改 pyproject 并重锁提交
poetry add "psycopg[binary]"

# ② 或:目标机装系统 libpq
sudo apt install libpq5            # Debian/Ubuntu 运行时
# 编译路线:apt install libpq-dev + poetry add "psycopg[c]"
```

**自检,过了才继续**:

```bash
cd service && .venv/bin/python -c "import psycopg; print(psycopg.__version__)"
```

### 0.2 目标库的一次性准备(Alembic 不建库、不建角色)

Alembic 只在**已存在的库**里建表。库和角色要你先建:

- **版本**:≥ 14;本仓实测 16 全通。
- 建角色与库,**迁移账号必须是库的 owner**:

```bash
createuser --pwprompt poker
createdb -O poker poker
# 等价 SQL:CREATE ROLE poker LOGIN PASSWORD '…'; CREATE DATABASE poker OWNER poker;
```

> **为什么必须是 owner**:pg 15 起 `public` schema 不再默认给 PUBLIC 授 `CREATE`。用一个只 `GRANT CONNECT` 的角色跑迁移,第一支就 `permission denied for schema public`。非 owner 方案要显式 `GRANT CREATE, USAGE ON SCHEMA public TO <role>`。

迁移只在默认 schema(`public`)建表,不建 database、不改 `search_path`——所以 URL 里的库名必须**已经存在**。

### 0.3 配置:一个 URL,两个消费者

**写进 `.env`,不要用行内前缀。**

```bash
cd service
cp .env.example .env
# 编辑 .env:
DATABASE_URL=postgresql+psycopg://poker:pass@host:5432/poker
```

alembic 与运行时读的是**同一处**([app/config.py](../app/config.py) 的 `settings`)。pg 下同步/异步共用一份 URL,psycopg3 双栖,**不用配两份**。

> ⚠️ **最致命的失败模式:静默回落 sqlite。**
>
> `settings.DATABASE_URL` 缺省是 `None`([app/config.py](../app/config.py)),两个消费方各套自己的方言默认:
> alembic → `sqlite:///./poker.db`(同步),运行时 → `sqlite+aiosqlite:///./poker.db`(异步),指同一个 `service/poker.db`。
>
> **两处都不报错**。URL 拼错、忘了 `export`、或行内前缀只对单条命令生效时:
> - `alembic upgrade head` 会去建/升本地 sqlite 文件,**退出码 0、日志一切正常**;
> - 更糟的是运行时——你把 pg 迁移好了,然后 `uvicorn` 起服务,env 没了 → 回落 sqlite → `create_all` 自己建表 + 种 dev 用户 → **接口全 200,而 pg 库一行数据也没有**。
>
> **两条强制核对**:① alembic 第一行日志必须是 `Context impl PostgresqlImpl`,看到 `SQLiteImpl` **立刻停手**;② 起服务后 `service/poker.db` **不应**被新建。

**sqlite 下不能把 URL 写进 `.env`**:两边方言形不同(同步 vs 异步),共用一份必崩其中一个。pg 无此坑。本地开发就别设 `DATABASE_URL`,让两边各用各的缺省。

### 0.4 首次建库:顺序是硬的

```bash
cd service
# ① 库已建好(§0.2)、.env 已写好(§0.3)
.venv/bin/alembic upgrade head          # ② 先迁移
.venv/bin/uvicorn app.shell.lifespan:app --host 0.0.0.0 --port 8000   # ③ 再起服务
```

> **为什么顺序不能反。** 本仓只有一个 ASGI 入口([app/shell/lifespan.py](../app/shell/lifespan.py)),它在 `setup()` 里**无条件**跑 `create_all`——没有 dev/prod 开关。
>
> - **正序**:表已由 Alembic 建好,`create_all` 的 `checkfirst` 让它是幂等 no-op。✅
> - **反序**:`create_all` 先建出一套**没有 `alembic_version` 的表**,之后 `alembic upgrade head` 撞表失败(`DuplicateTable: relation "handrecord" already exists`),且没有迁移历史可续。**库等于废了。**
>
> 已经反序建过的库怎么救:drop 重来;或在**确认 schema 恰好等于 head** 之后 `alembic stamp head`(只写版本号、不动表)。

### 0.5 上线前的库级验收(逐条勾)

```bash
cd service
.venv/bin/alembic current               # 期望:b8ca88a687af (head)
.venv/bin/alembic check                 # 期望:No new upgrade operations detected
psql -d poker -c '\dt'                  # 期望:6 张(5 业务表 + alembic_version)
psql -d poker -c '\d "user"'            # 抽查列
```

- `alembic` 首行日志是 `Context impl PostgresqlImpl` —— 不是 `SQLiteImpl`
- `alembic check` 是唯一能证明「链跑到 head 之后的库 == `models.py`」的命令
- 6 张表:`user` / `handrecord` / `handparticipant` / `dmmessage` / `dmreadcursor` + `alembic_version`

### 0.6 生产首次上线的数据善后(**必做**)

服务启动会**无条件**执行 `seed_dev_users()`,按 `gameconfig.DEV_USERS` 种 10 个账号,用的是**提交在 git 里**的共享明文口令与共享 SM4 密钥(见 [app/poker.env.example](../app/poker.env.example))。两个后果,pg 上都得手工善后:

```sql
-- ① 删掉 dev 账号:生产库上线即有 10 个共享弱口令账号
DELETE FROM "user" WHERE name IN ('alice','bob','carol','dave','eve','frank','smoke1','smoke2','smoke3','gina');

-- ② 推进主键序列:seed 用**显式 id** 插入,不推进 pg 的 SERIAL 序列
SELECT setval('user_id_seq', (SELECT COALESCE(max(id), 1) FROM "user"));
```

> **不做 ② 会怎样**:`scripts/kuser_admin.py issue` 发第一个正式账号时
> `UniqueViolation: duplicate key value violates unique constraint "user_pkey" DETAIL: Key (id)=(1) already exists`。
>
> 这条坑**在 sqlite 上完全看不见**(rowid 取 max+1),是纯 pg 独有的。
>
> **目前种子关不掉**:`gameconfig.DEV_USERS` 是 `Field(min_length=1)`,设空数组直接 `ValidationError`。要真正关掉需代码改造(给 lifespan 加 `SEED_DEV_USERS` 开关 + 放宽 `min_length`),尚未做。

### 0.7 部署形态:只能单进程单 worker

本服务是**内存权威 + 全进程唯一 DB 写者**(`PersistWriter` 无行锁、无 `with_for_update`)。

**`uvicorn --workers >1` 会破坏这个前提**:各 worker 各持一份 `world` 与各自一个 `PersistWriter`,状态与积分互相覆盖。见 [architecture.md](architecture.md)。

---

## §1 本仓的接线(已配好,了解即可)

**`alembic.ini`**:`script_location = alembic`;`prepend_sys_path = .` 使 `app` 可导入;`sqlalchemy.url` 只是占位,真 URL 由 `env.py` 覆盖。

**`alembic/env.py`**

- 只 `import app.db.models`,把表注册到 `SQLModel.metadata`(即 `target_metadata`),保证单一事实源。
- `DATABASE_URL` 经 [app/config.py](../app/config.py) 的 `settings` 读取(优先级 env > `.env`,0045)。**缺省是 `None`**,此时 env.py 自己套 `sqlite:///./poker.db`——注意这只是 alembic 侧的兜底,运行时套的是异步形,见 §0.3。
- 不跳过外键;`run_migrations_online()` 里开了 `render_as_batch=True`。

> **`render_as_batch` 的语义容易理解错**:它只影响 **autogenerate 生成脚本时的渲染**(把 ALTER 渲染成 `with op.batch_alter_table(...)`),对**已写好**的迁移执行没有任何作用。**手写**迁移里的 ALTER 要自己包 batch,否则 sqlite 上会失败。

**`alembic/script.py.mako`**

模板硬带一行 `import sqlmodel`。autogen 会引用 `sqlmodel.sql.sqltypes.AutoString` 等列类型,缺这行升级时会 `NameError`(见 [changes/0026](refactor/changes/0026-p4-db-models-alembic.md))。

## §2 常用命令

工作目录 `cd service`,可执行文件 `service/.venv/bin/alembic`。

> **pg 上任何 upgrade / downgrade 之前先备份**:`pg_dump -Fc -f poker-$(date +%F).dump poker`

| 目的 | 命令 |
|---|---|
| 升到最新 | `.venv/bin/alembic upgrade head` |
| 当前库在哪个版本 | `.venv/bin/alembic current` |
| **校验链与模型无漂移** | `.venv/bin/alembic check` |
| 迁移历史 | `.venv/bin/alembic history --verbose` |
| 回退一步 / 到底 | `.venv/bin/alembic downgrade -1` · `.venv/bin/alembic downgrade base`(先读 §5 的数据后果表) |
| 离线预演本次将执行的 SQL | `.venv/bin/alembic upgrade <当前版本>:head --sql` |

> **`--sql` 两处要注意**:① **仅 pg 可用**——sqlite 下走到 `b8ca88a687af` 会失败,那支的 batch 重命名需要连库反射表结构,离线做不到。② 不带 `<from>:` 时它是从 **base** 生成**全量建库** SQL(输出以 `CREATE TABLE alembic_version` 开头),对一个已在跑的库毫无意义;要预审本次增量,必须写成 `<当前版本>:head`。这是部署前唯一能离线预演 pg DDL 的手段,值得当验收步骤用。

## §3 改了模型 → 出一支迁移(主流程)

1. 改 [`app/db/models.py`](../app/db/models.py):加表 / 加列 / 改约束。

2. 自动生成迁移。autogenerate 比对的是「模型 metadata ↔ 当前库」:

   ```bash
   cd service
   .venv/bin/alembic revision --autogenerate -m "add user salt rounds"
   ```

   它要求库已经是当前 head,否则报 "Target database is not up to date";先 `upgrade head` 再生成。

3. 审生成的迁移(`alembic/versions/<hash>_*.py`)。autogen 不完美,务必逐行读 `upgrade`/`downgrade`:
   - 列改名会被当成「删一列 + 加一列」,会丢数据。手改成 batch 形式(与现行案例 `b8ca88a687af` 一致):

     ```python
     with op.batch_alter_table("user") as batch_op:
         batch_op.alter_column("old", new_column_name="new")
     ```
   - 数据回填、`server_default`、复杂约束抓不全,按需手补。
   - sqlite 上 ALTER 必须走 `batch_alter_table`(手写迁移要自己包,见 §1)。

4. 应用:`.venv/bin/alembic upgrade head`。

5. **合并前必须在 pg 上复验一遍。** 第 2 步是对着本地 sqlite 生成的,而 sqlite 反射拿不到约束名、`server_default` 等信息——这是「pg 不兼容迁移」最常见的出生地。

   ```bash
   docker run -d --rm --name pgtmp -e POSTGRES_PASSWORD=pw -e POSTGRES_USER=poker \
     -e POSTGRES_DB=poker -p 55432:5432 postgres:16
   DATABASE_URL="postgresql+psycopg://poker:pw@127.0.0.1:55432/poker" \
     .venv/bin/alembic upgrade head
   DATABASE_URL="…" .venv/bin/alembic check              # 无漂移
   DATABASE_URL="…" .venv/bin/alembic downgrade -1 && DATABASE_URL="…" .venv/bin/alembic upgrade head
   docker rm -f pgtmp
   ```

迁移文件随模型改动一起 commit,它们是同一个变更单元。

> 第一支基线迁移是在空库上 `--autogenerate` 出的「建全表」(`down_revision=None`),0026 落地。之后每次改模型都接在它后面增量出新版本。

## §4 铁律 / 注意点

**同名表别在一个进程里注册两次到同一 `SQLModel.metadata`**

否则会 `InvalidRequestError: Table 'X' is already defined`。原型模块曾与 `app/db/models.py` 定义同名表(`user`/`handrecord`),[0027](refactor/changes/0027-prototype-teardown.md) 拆除后冲突源已消除;现在 `alembic/env.py`、runtime、测试都只导 `app/db.models` 一处,新增表只加进 `app/db/models.py`。

**模型是源,库是投影**

只改模型 + 出迁移,绝不手 `ALTER TABLE`——手改会与迁移历史漂移。

> 已知一处**真实漂移**:迁移 `010d8e8a08d7` 给 `HandRecord.room` 带了 `server_default=''`(为回填 0052 之前的历史行),而 `models.py` 没有。于是 Alembic 建的库上 `room` 有 `DEFAULT ''`、`create_all` 建的库没有;`alembic check` 默认不比对 `server_default`,永远不会报。要收敛就在模型补 `sa_column_kwargs={"server_default": ""}`,或在 env.py 打开 `compare_server_default`。

**必审 autogen 产物**

尤其是改名 / 删列 / 数据迁移。一支迁移聚焦一个逻辑改动,便于回滚。

**`import sqlmodel` 必须在迁移里**

模板已带,别删那行。

**`create_all` 与 Alembic 的真实关系(口径已改实)**

- 本仓**只有一个 app**,它启动时**总会**跑 `create_all`(`app/shell/lifespan.py` 的 `setup()`,无 dev/prod 开关)。
- 已由 Alembic 迁移过的库上,`create_all` 的 `checkfirst` 让它是**幂等 no-op**——所以「先迁移、再起服务」是安全的,见 §0.4。
- **顺序反了会建出没有迁移历史的表**,之后 `upgrade` 必撞表。
- 要真正兑现「生产绝不靠 create_all」,需给 lifespan 加环境开关(独立决策,尚未做)。

**新架构对齐**

`app/db/` 的表对齐 delayDB 的 Write 载荷。载荷定义在 [core/records.py](../app/core/records.py) 与 [app/db/dm_records.py](../app/db/dm_records.py),落库经 `PersistWriter`/`OrmPersister`,见 [db.md](db.md)。

表 ← 载荷的对应关系:`User.points` ← `PointsWrite`;`HandRecord` + `HandParticipant` ← `HandRecordWrite`/`ParticipantWrite`;`DMMessage` ← `DMWrite`,迁移 `79d1fd60fc7f`(见 [changes/0038](refactor/changes/0038-dm-send-deliver.md));`DMReadCursor` ← `DMReadCursorWrite`,迁移 `7ff9cb0a8db1`(见 [changes/0039](refactor/changes/0039-dm-read-cursor.md))。

几处需要展开的列改动:

- **`HandRecord.room`**:denormalized 列,迁移 `010d8e8a08d7`,供 `POST /hands` 的 `room` 过滤用(见 [changes/0052](refactor/changes/0052-handrecord-room-column.md);端点 0094 起走加密信封)。
- **`User` 国密鉴权列 `name`/`hash_password`/`k_user`**:迁移 `49417b108733`(见 [changes/0056](refactor/changes/0056-p5-user-auth-columns-authenticate.md))。三列均 nullable,因为 `name` 是唯一列、不能用常量 `server_default` 回填,可空才是安全的增量做法。
- **K_user 双钥轮换**:迁移 `b8ca88a687af`(见 [changes/0066](refactor/changes/0066-p5-kuser-rotation.md))。`k_user` **重命名**为 `k_cur`,并扩列 `k_cur_ver`/`k_cur_until`/`k_prev`/`k_prev_ver`/`k_prev_until`。重命名必须手改成 batch `alter_column`——autogen 误判成删+加会丢已发密钥。这是「必审 autogen 产物」的现行案例。

隐私:表只存结果——uid + 初/末筹码 + 池额;私信正文里也没有底牌牌堆。绝不含底牌 / 牌堆。

**唯一 DB 写者(一条例外)**

- delayDB 的写只经 `PersistWriter`,无行锁 / `with_for_update`(见 [db.md](db.md))。
- **例外**:鉴权列(`hash_password` / `nickname` / `k_*`)走 [app/db/user_writes.py](../app/db/user_writes.py) **同步直写**(消费者:`rest/profile.py`、`auth/kuser.py`、`scripts/kuser_admin.py`),启动时的 `seed_dev_users` 同理。它们与 PersistWriter 的列**不相交**(`SET k_*/hash_password` vs `SET points`),所以仍然无需行锁。口径同 [db.md](db.md)。
- 读路径(REST 查询)用各自的请求级 session;实时判定以内存为准。
- ⇒ 只能**单进程单 worker** 部署,见 §0.7。

## §5 迁移链与回滚的数据后果

当前链(`alembic history`,单一线性,head = `b8ca88a687af`):

| 迁移 | 内容 | **downgrade 的数据后果** |
|---|---|---|
| `d07cf4b8828c` | 基线:`user` / `handrecord` / `handparticipant` | **drop 全部三张表** |
| `79d1fd60fc7f` | `dmmessage` | drop 表 = 丢全部私信 |
| `7ff9cb0a8db1` | `dmreadcursor` | drop 表 = 丢全部已读游标 |
| `010d8e8a08d7` | `handrecord.room` 列 | 丢该列(手牌历史的房名过滤失效) |
| `49417b108733` | `user` 的 `name`/`hash_password`/`k_user` | **抹掉全部登录凭证**——所有人登不上 |
| `b8ca88a687af` | `k_user` → `k_cur` + 双钥扩列 | 丢 `k_prev*` 宽限信息 |

**`downgrade` 不是「撤销一步」那么轻。** 备份先做,见 §2。

## §6 常见报错 → 病因 → 处置

| 报错 | 病因 | 处置 |
|---|---|---|
| `ImportError: no pq wrapper available` | 驱动没装全(裸 psycopg 缺 libpq) | §0.1 |
| `database "x" does not exist` | 库没建;Alembic 不建库 | §0.2 |
| `permission denied for schema public` | 迁移账号不是库 owner(pg15+) | §0.2 |
| alembic 日志出现 `Context impl SQLiteImpl` | `DATABASE_URL` 没生效,**正在改错库** | 立刻停手,§0.3 |
| `relation "handrecord" already exists` | 顺序反了:先起了服务、`create_all` 建过表 | §0.4 的自救路径 |
| `Target database is not up to date` | 库不在 head,不能 autogenerate | 先 `upgrade head` |
| `UniqueViolation … user_pkey … Key (id)=(1)` | dev 种子用显式 id,没推进 pg 序列 | §0.6 的 `setval` |
| `NameError: sqlmodel` | 迁移里删了 `import sqlmodel` | 加回去,§1 |

## §7 与其它文档

- **首次部署 / 从零起一套 pg**:[../QUICKSTART.md](../QUICKSTART.md) §2(与本篇 §0 互为详略)。
- 写通道机制(写缓冲、双缓冲、重试、drain):[db.md](db.md)。
- 存储模型(内存权威 + 载入一次 + 回滚):[storage.md](storage.md)。
- 工程环境(Poetry/venv):[dev.md](dev.md)。
