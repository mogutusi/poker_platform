# 开发环境:依赖(Poetry)与迁移(Alembic)

本文是项目专属的开发/运维约定,补充根目录 [README.md](../README.md) 的基础命令——重点讲**本仓库的 env.py / 配置怎么连在一起、有哪些坑**。环境:Python ≥ 3.12、PostgreSQL、psycopg3、SQLModel、Alembic、Poetry。

## 两套配置文件,别混

| 文件 | 谁读 | 装什么 |
|---|---|---|
| `service/.env`(+ `.env.example` 模板) | [app/config.py](../app/config.py) `Settings`(0045)→ [app/db/engine.py](../app/db/engine.py) + [alembic/env.py](../alembic/env.py) 都经它读 | `DATABASE_URL`、未来 JWT 等**基础设施/密钥**。`DATABASE_URL` 有安全 dev 默认(缺省 sqlite,免 `.env` 也能跑);密钥随 P5(无默认)|
| `service/app/poker.env`(本地覆盖,可选)+ `service/app/poker.env.example`(提交基线) | [app/gameconfig.py](../app/gameconfig.py)(`GameConfig(BaseSettings)`) | 盲注/买入/超时/队列等**游戏可调参数**(见 [config.md](config.md))。**已落地(0042)**:`gameconfig` 读 `env_file=(poker.env.example, poker.env)` 两层(后者覆盖)、字段无代码默认 + `Field` 边界。`poker.env.example` 提交作 canonical 基线(新检出即可跑);本地调参复制为 `poker.env`(gitignored)改值。(原型 `app/pokertable/gameconfig.py` 已于 0027 拆除)|

- `.env` / `poker.env` **都不进 git**(含密钥 / 本地覆盖);`*.example` 提交。**注**:`poker.env.example` 不止是模板,还是 gameconfig 的**实际加载基线**(0042),所以它带 canonical 真值(游戏参数非密钥),改字段同步回它。
- **Alembic 的 `DATABASE_URL` 经 [app/config.py](../app/config.py) `settings` 读**(0045;此前直读 `os.environ`):`settings.DATABASE_URL`(env > `.env`)缺省本地 sqlite(`sqlite:///./poker.db`)——**仍免 `.env` 也能跑迁移**(`settings.DATABASE_URL` 有默认、缺 `.env` 不崩,达成原「不依赖会崩的 Settings」之意图)。生产把库 URL 给 alembic:`DATABASE_URL=… alembic upgrade head`(os.environ 优先,覆盖 `.env`/默认)。**约束**:P5 给 `Settings` 加必填密钥须给默认或拆独立类,保 alembic 无密钥仍能跑迁移(headless,见 [app/config.py](../app/config.py) 注)。完整用法见 **[db-migrations.md](db-migrations.md)**。

## Poetry

本项目 `package-mode = false`(是应用不是库),所以**不 `poetry build`、不安装自身**。依赖在 `pyproject.toml` 的 `[project].dependencies`(PEP 621);本地包 `ttxsgm` 走 `[tool.poetry.dependencies]` 的 path 依赖。

```bash
# 首次:装依赖(README 已有)
poetry config virtualenvs.in-project true   # .venv 建在项目内
poetry env use python3.12
poetry install                              # 按 pyproject + poetry.lock 装

# 加依赖(会同时更新 pyproject 和 poetry.lock)
poetry add <pkg>
poetry add --group dev pytest pytest-asyncio   # 开发依赖(测试见 testing.md)

# 跑东西(工作目录 service/)。原型入口 app.main 已于 0027 删;当前可跑的是明文 dev shell。
poetry run uvicorn app.shell.lifespan:app   # 不激活、直接在 venv 里跑(推荐);→ ws://127.0.0.1:8000/dev/ws?nick=alice
poetry env activate                          # 或先激活、再裸跑命令
.venv/bin/uvicorn app.shell.lifespan:app     # 或直接点名 venv 可执行(等价)
```

- **venv 路径固定在 `service/.venv`**(`virtualenvs.in-project true` 的结果,Python 3.12):解释器是 `service/.venv/bin/python`,`pytest`/`alembic` 等可执行文件都在 `service/.venv/bin/` 下。IDE/编辑器把 Python interpreter 指到 `service/.venv/bin/python` 即可。
- **裸 `python`/`pytest` 多半不存在或缺依赖**(系统环境既无项目依赖、也不在 venv 里,常见 `command not found`)——所有命令(`python`、`pytest`、`alembic`)一律走 `poetry run <cmd>`、先 `poetry env activate`,或直接调 `service/.venv/bin/<cmd>`。
- **`poetry.lock` 要提交**:别人 `poetry install` 复现同样版本。改了依赖记得连 lock 一起提交。

## Alembic

> **完整用法(命令、改模型→出迁移、铁律)见 [db-migrations.md](db-migrations.md)。** 这里只记本仓 `env.py` 的接线要点与坑。

### 这个仓库的 env.py(0026 重定向后)

- **只 `import app.db.models`**(显式),把新架构表注册进 `SQLModel.metadata`(=`target_metadata`)——**不再** `os.walk` 全仓 `*models*`(0026 改;原型 `app/user`/`app/handrecord` 旧模型当时会被一并注册造表名冲突,现已随 0027 拆除)。**新表加进 [app/db/models.py](../app/db/models.py)**(单一事实源)。
- **`DATABASE_URL` 经 `app.config.settings` 读**(env > `.env`,0045)、缺省本地 sqlite(上面说过),不用 `alembic.ini` 占位;`settings` 有默认故缺 `.env` 不崩(headless)。
- **真外键**:不再跳过 FK(原型 env 的 `render_item` hack 已删)——表间关系在 DB 层强制(参与者→手牌/用户,见 [db.md](db.md))。
- **`render_as_batch=True`**:sqlite 也能 ALTER(走 batch 重建);postgres 无害。
- **`script.py.mako` 硬带 `import sqlmodel`**:autogen 引用 `sqlmodel.sql.sqltypes.AutoString` 等,不带则升级 `NameError`(见 [changes/0026](refactor/changes/0026-p4-db-models-alembic.md))。

> 同步 vs 异步:应用(运行时落库,P4 三之二)用 `create_async_engine`(psycopg3 异步);Alembic 用同步 `engine_from_config`。同一个 `postgresql+psycopg://...` URL **psycopg3 既支持同步也支持异步**,迁移不用另配同步驱动。本地验证可用同步 sqlite(`sqlite:///`)。

### 迁移历史(0026 重置)

原型 4 支迁移(`0_1_0`..`0_1_3`,建原型 schema)已删;新架构从一支**基线**(`down_revision=None`,建 `user`/`handrecord`/`handparticipant`)重新起历史。之后改模型 → autogenerate 增量出新版本(`-m "简述本次结构改动"`,如 `add user salt rounds`)。

### 重构会涉及的迁移(预告)

- **密码哈希 `salt$rounds$digest` + `K_user` 双钥/版本**(见 [auth.md](auth.md)):P5 给 `app/db/models.py` 的 `User` 加列 → 一支新迁移(+ 数据迁移脚本)。
- **运行时落库(P4 三之二)**:`OrmPersister` + `to_orm` 把 Write 载荷映射到 `app/db/` 表(见 [db.md](db.md) / [db-migrations.md](db-migrations.md))。

## Git 使用

### 分支模型(单人项目,从简)

- **主干 = `develop`**(本仓库的主分支就是 `develop`,不是 `main`;目前**只有这一条分支**)。远端 `origin/develop`。
- **默认直接提交到 `develop`**——单人开发、改动线性,不强制开功能分支(否则徒增合并开销)。最近的提交历史也都是直接落在 `develop`。
- **只在这两种情况才开分支**:① 一次大/有风险的重构,想先隔离试错、不污染 `develop`;② 想留个 PR 做自我评审。命名 `refactor/<阶段>-<简述>`,完成 `git switch develop && git merge` 合回。
  ```bash
  git switch -c refactor/p1-betting-rules   # 仅当需要隔离时
  # …干活、提交…
  git switch develop && git merge --no-ff refactor/p1-betting-rules
  ```
- 拉取保持最新:`git pull`(develop 跟踪 origin/develop)。

### 提交(commit)

- **commit / push 前先复审(提交门槛)**:对照 [review.md](review.md) 逐维做对抗式自 review,确认项当场修(代码 + 同步文档),结论记进 [changes/](refactor/changes/) 的 `NNNN`「自 review」段。**绿测不等于可提交;无「自 review」段不 push。**
- **提交信息一律全英文**(标题 + 正文 + trailer)。便于检索、跨环境一致,同 [log.md](log.md)「日志一律英文」。**标识符(类/函数/变量名)用英文,但代码注释用中文**(同设计文档语言,见 [coding_principle.md](coding_principle.md));提交信息仍全英文。
- **提交信息引用变更记录编号**(见 [refactor/README.md](refactor/README.md) §5 的 `changes/NNNN-*.md`),让 commit ↔ 设计变更可追溯:
  ```
  P0: core domain models + enums (refactor 0002)

  - core/domain.py: World/Room/Hand/Player/Seat/UserState (with uid)
  - core/enums.py: four state machines + transition tables
  Refs: docs/refactor/changes/0002-core-domain.md
  ```
- **一次提交是一个自洽单元**:能编译/能过它该过的测试;别把「半个功能」和「无关格式化」混在一起。
- **代码与文档同提交**:若这次改了设计文档(§0 的「文档对不上就改」),把文档改动和代码放在**同一个提交或紧邻提交**,别让文档落后于代码。
- **迁移文件、`poetry.lock`、生成的 wire TS 产物都要提交**(它们是复现实现的一部分)。

### 绝不提交(已在 [.gitignore](../../.gitignore))

- `*.env` / `.env` / `poker.env`(配置含密钥)、`*.key`、`.venv/`、`__pycache__/`、`node_modules/`、`original_password.txt`。
- **秘密零容忍**:`K_user`、`session_token`、密码、`JWT_SECRET` 任何形式都不进 git(同 [log.md](log.md) 脱敏红线)。新增秘密文件先加 `.gitignore` 再说。
- 改了配置项,**提交的是 `*.example`**(不含真值),真值留在本地 `.env`/`poker.env`(同 [config.md](config.md))。

### 认证(一次性设置)

GitHub HTTPS 推送不能用账号密码,需下列之一(配一次,凭证缓存后免重复):

- **浏览器授权(本机当前用法,最省事)**:配了 `git config --global credential.helper store` 后首次 `git push`,Git Credential Manager 弹浏览器登录 GitHub 即可,凭证缓存,之后推送无需再输。
- **PAT + HTTPS**:建 fine-grained token(仅本仓库、Contents: Read and write),`git push` 时用户名填 GitHub 账号、密码粘 token。
- **SSH**:`ssh-keygen` → 公钥加到 GitHub → `git remote set-url origin git@github.com:mogutusi/poker_platform.git`。

> 秘密(token/SSH 私钥)绝不进 git、不写进文档/命令行历史明文。

### 日常流程

```bash
git status                       # 看清改了什么再提交
git add -p                       # 分块挑选,别无脑 git add .
# review                         # 提交前对照 docs/review.md 逐维自检 + 回填 changes/NNNN「自 review」段(提交门槛)
git commit -m "..."              # 信息引用 changes/NNNN
git push                         # 默认:推到 develop(本地 develop 已跟踪 origin/develop)
```

> **默认就是上面这条**——直接在 `develop` 上提交并 `git push`(见「分支模型」:主干即 `develop`、单人线性开发,0001-0010 都这么落)。**仅**当按「分支模型 ②」要隔离/留 PR 时,才改走功能分支:`git switch -c refactor/<阶段>-<简述>` → 干活提交 → `git push -u origin refactor/<阶段>-<简述>` → 阶段完成 `git switch develop && git merge --no-ff <分支>` 合回,再 `git push` 推 `develop`。

> 提交节奏:跟着 [refactor/README.md](refactor/README.md) 的「变更记录先行」走——先开 `changes/NNNN`,再写代码,完成后回填记录并提交,提交信息回指该记录。

## 约定(必须守住)

1. **新表/新列改完模型 → autogenerate → 人工 review → upgrade**;迁移文件和 `poetry.lock` 都进 git(详见 [db-migrations.md](db-migrations.md))。
2. **新架构表加进 [app/db/models.py](../app/db/models.py)**(env.py 只导它);原型 `*models*.py` 不再被 Alembic 追踪。
3. **`.env`/`poker.env` 不进 git**;改配置项时连 `*.example` 一起更新(同 [config.md](config.md))。
4. 命令一律在 venv 内(`poetry run` / 激活)。
5. **提交信息全英文**,引用 `changes/NNNN`;秘密绝不进 git。
