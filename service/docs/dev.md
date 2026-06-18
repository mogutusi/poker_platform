# 开发环境:依赖(Poetry)与迁移(Alembic)

本文是项目专属的开发/运维约定,补充根目录 [README.md](../README.md) 的基础命令——重点讲**本仓库的 env.py / 配置怎么连在一起、有哪些坑**。环境:Python ≥ 3.12、PostgreSQL、psycopg3、SQLModel、Alembic、Poetry。

## 两套配置文件,别混

| 文件 | 谁读 | 装什么 |
|---|---|---|
| `service/.env` | [app/config.py](../app/config.py) 的 `Settings`(`load_dotenv`) | `DATABASE_URL`、`JWT_SECRET`、token 过期等**基础设施** |
| `service/app/pokertable/poker.env` | [gameconfig.py](../app/pokertable/gameconfig.py) 的 `GameConfig` | 盲注/买入/超时/delayDB/日志等**游戏可调参数**(见 [config.md](config.md)) |

- `.env` / `poker.env` **都不进 git**(含密钥);各配一个 `*.example` 提交。
- **Alembic 也读 `.env` 的 `DATABASE_URL`**:[alembic/env.py](../alembic/env.py) 用 `settings.DATABASE_URL` 覆盖 `alembic.ini` 里的占位值(`sqlalchemy.url = inenvpy` 是假值,被代码覆盖)。所以迁移连的库 = 应用连的库,统一在 `.env`。

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

# 跑东西(三选一,工作目录 service/)
poetry run python -m app.main        # 不激活、直接在 venv 里跑(推荐)
poetry env activate                   # 或先激活、再裸跑命令
.venv/bin/python -m app.main          # 或直接点名 venv 解释器(等价)
```

- **venv 路径固定在 `service/.venv`**(`virtualenvs.in-project true` 的结果,Python 3.12):解释器是 `service/.venv/bin/python`,`pytest`/`alembic` 等可执行文件都在 `service/.venv/bin/` 下。IDE/编辑器把 Python interpreter 指到 `service/.venv/bin/python` 即可。
- **裸 `python`/`pytest` 多半不存在或缺依赖**(系统环境既无项目依赖、也不在 venv 里,常见 `command not found`)——所有命令(`python`、`pytest`、`alembic`)一律走 `poetry run <cmd>`、先 `poetry env activate`,或直接调 `service/.venv/bin/<cmd>`。
- **`poetry.lock` 要提交**:别人 `poetry install` 复现同样版本。改了依赖记得连 lock 一起提交。

## Alembic

### 这个仓库的 env.py 做了三件特殊事(必须知道)

1. **自动导入所有 `*models*.py`**:`env.py` 递归 `os.walk(app/)`,import 每个文件名含 `models` 的模块,从而把它们的 SQLModel 表注册进 `SQLModel.metadata`(=`target_metadata`)。
   > **坑:新建一张表,模型必须放在文件名带 `models` 的文件里**(如 `app/xxx/models.py`),否则 autogenerate **看不到它**、不会生成迁移。
2. **URL 从 `settings.DATABASE_URL` 取**(上面说过),不是 `alembic.ini`。
3. **跳过外键约束**:`render_item` 对 `foreign_key` 返回 `None` ⇒ autogen **不输出 FK 约束**。即表间关系**不在 DB 层强制**(应用层自己保证)。要 DB 级 FK 就得改这个 hook;现状是有意为之。

> 同步 vs 异步:应用用 `create_async_engine`(psycopg3 异步),Alembic 用同步 `engine_from_config`。同一个 `postgresql+psycopg://...` URL **psycopg3 既支持同步也支持异步**,所以迁移不用另配同步驱动。

### 日常流程

```bash
# 1. 改/加 SQLModel 模型(放在 *models*.py 里)
# 2. 生成迁移(对比模型与库,产出 diff)
poetry run alembic revision --autogenerate -m "0.1.x"
# 3. 【必做】人工 review 生成的 alembic/versions/<hash>_0_1_x.py
#    autogen 不完美:FK 被跳过、枚举/类型/server_default、改列名会被当成删+加,都要核对
# 4. 应用到最新
poetry run alembic upgrade head
```

回滚 / 查看:

```bash
poetry run alembic downgrade -1          # 回退一步(README 里的 "downgrade head" 是无效写法)
poetry run alembic downgrade <revision>  # 回到指定版本
poetry run alembic current               # 当前库在哪个版本
poetry run alembic history               # 迁移历史
```

### 版本命名约定

现有迁移文件名是 `<hash>_0_1_X.py`(slug = `0_1_x`),即用 `-m "0.1.x"` 给每次迁移一个 `0.1.x` 语义版本,**逐次递增**(现状到 `0.1.3`)。新迁移沿用,别用随口 message。

### 重构会涉及的迁移(预告)

- **密码哈希改 `salt$rounds$digest`**(见 [auth.md](auth.md)):需要一次迁移 + 数据迁移脚本。
- **`K_user` 双钥 + 版本/宽限字段**(见 [auth.md](auth.md) 轮换)。
- **手牌记录对齐 `HandRecordWrite`**(见 [db.md](db.md) / [rest.md](rest.md))。
- 新增表都记得放 `*models*.py`,否则 autogen 漏。

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

1. **新表/新列改完模型 → autogenerate → 人工 review → upgrade**;迁移文件和 `poetry.lock` 都进 git。
2. **模型放 `*models*.py`**,否则 Alembic 看不见。
3. **`.env`/`poker.env` 不进 git**;改配置项时连 `*.example` 一起更新(同 [config.md](config.md))。
4. 命令一律在 venv 内(`poetry run` / 激活)。
5. **提交信息全英文**,引用 `changes/NNNN`;秘密绝不进 git。
