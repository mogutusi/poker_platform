# 开发环境:依赖(Poetry)与迁移(Alembic)

本文是项目专属的开发/运维约定,补充根目录 [README.md](../README.md) 的基础命令,重点讲本仓库 env.py 与配置的接线和坑。

环境:Python ≥ 3.12、PostgreSQL、psycopg3、SQLModel、Alembic、Poetry。

## 两套配置文件,别混

一套装基础设施(`.env`),一套装游戏可调参数(`poker.env`),读它们的代码也是两套。

| 文件 | 谁读 | 装什么 |
|---|---|---|
| `service/.env`(模板 `.env.example`) | [app/config.py](../app/config.py) 的 `Settings`(0045);[app/db/engine.py](../app/db/engine.py) 与 [alembic/env.py](../alembic/env.py) 也经它读 | `DATABASE_URL`(缺省 sqlite,所以没有 `.env` 也能跑)、未来 JWT 等基础设施/密钥(随 P5 加入,无默认值) |
| `service/app/poker.env`(本地覆盖,可选)+ `service/app/poker.env.example`(提交基线) | [app/gameconfig.py](../app/gameconfig.py) 的 `GameConfig(BaseSettings)`;读 `env_file=(poker.env.example, poker.env)` 两层,后者覆盖前者(0042) | 盲注/买入/超时/队列等游戏可调参数(见 [config.md](config.md));字段没有代码默认值,用 `Field` 定边界 |

两个纪律:`.env` / `poker.env` 都不进 git,`*.example` 提交;`poker.env.example` 不只是模板,还是 gameconfig 的实际加载基线(0042),带 canonical 真值——这里的真值是游戏参数,不是密钥,改字段要同步回它。

Alembic 怎么拿到 `DATABASE_URL`(0045):经 [app/config.py](../app/config.py) 的 `settings` 读,优先级 os.environ > `.env` > 默认 sqlite(`sqlite:///./poker.db`),因此没有 `.env` 也能跑迁移;生产写法 `DATABASE_URL=… alembic upgrade head`。约束:P5 给 `Settings` 加必填密钥时,须给默认值或拆成独立类,保证 alembic 无密钥仍能跑迁移(headless,见 [app/config.py](../app/config.py) 注)。完整用法见 [db-migrations.md](db-migrations.md)。

## Poetry

本项目 `package-mode = false`,因为它是应用不是库:不 `poetry build`、不安装自身。依赖位置:主依赖在 `pyproject.toml` 的 `[project].dependencies`(PEP 621);本地包 `ttxsgm` 走 `[tool.poetry.dependencies]` 的 path 依赖。

```bash
# 首次:装依赖
poetry config virtualenvs.in-project true   # .venv 建在项目内
poetry env use python3.12
poetry install

# 加依赖(同时更新 pyproject 和 poetry.lock)
poetry add <pkg>
poetry add --group dev pytest pytest-asyncio   # 开发依赖(见 testing.md)

# 跑东西(工作目录 service/)。原型入口 app.main 已于 0027 删;当前可跑的是明文 dev shell。
poetry run uvicorn app.shell.lifespan:app   # 推荐;→ ws://127.0.0.1:8000/dev/ws?nick=alice
poetry env activate                          # 或先激活、再裸跑命令
.venv/bin/uvicorn app.shell.lifespan:app     # 或直接点名 venv 可执行(等价)
```

venv 与命令:

- venv 固定在 `service/.venv`(Python 3.12):解释器 `service/.venv/bin/python`,IDE 的 Python interpreter 指到它;`pytest`/`alembic` 等可执行文件也都在 `service/.venv/bin/` 下。
- 系统裸 `python`/`pytest` 没装项目依赖,会 `command not found` 或缺包;所以命令一律 `poetry run <cmd>`,或先 `poetry env activate`,或直接调 `service/.venv/bin/<cmd>`。

lock 文件:`poetry.lock` 要提交,别人 `poetry install` 才能复现同样版本;改依赖时连 lock 一起提交。

### dev 登录(P5,changes/0060/0063)

接口 `POST /user/login`,body `{name, iv, blob}`。

- `name` 是昵称;`blob` 是 `SM4(DEV_KUSER, iv, {password: DEV_PASSWORD, client_nonce, ts})` 的 hex,响应用 `DEV_KUSER` 解密得会话。
- `ts` 为当前 epoch 秒,须落在 `LOGIN_REPLAY_WINDOW_SECONDS` 窗内;`client_nonce` 每次新随机(0063 重放守卫)。
- `DEV_PASSWORD`/`DEV_KUSER` 放在 `poker.env`,仅 dev 用,不是生产密钥。

ws 双端点并存(0061):`?sid=` 是加密端点,登录后使用;`?nick=` 是明文端点,dev 脚手架,前端切到加密后退役。

### K_user 管理(P5,changes/0066)

生产用户密钥的首发与每周轮换,走管理员 CLI:

```
.venv/bin/python scripts/kuser_admin.py issue|rotate|list
```

它直连 `DATABASE_URL` 指向的库。

排程与幂等:`rotate` 挂系统 cron 每周跑,它是幂等的,只轮换到期账号;dev 种子钥不排程,cron 不会轮换 `DEV_KUSER`。

```
# 管理员 crontab 示例:每周日 03:00 轮换到期密钥,输出落管理员私有文件(chmod 600,发完密钥即清)
0 3 * * 0  cd <repo>/service && .venv/bin/python scripts/kuser_admin.py rotate >> ~/kuser-rotate.out 2>&1
```

发钥纪律:

- 新钥/新口令只打到管理员终端 stdout,由管理员带外私发给用户;不要重定向进会进 git 或被日志采集的文件。
- 换钥半自动、发钥永远手动:cron 只完成「DB 里换上新钥」,新钥必须带外私发——自动经信道下发会被旧钥持有者链式解出,见 auth.md「别用信道自动下发」决策。
- 详见 [auth.md](auth.md) §K_user 每周轮换。

忘跑 cron 不会锁人:`k_cur_until` 只是排程,登录不查它;换钥后旧钥有 `KUSER_GRACE_DAYS` 宽限,宽限期内登录响应带 `rotate=true` 提示。

## Alembic

> 完整用法(命令、改模型→出迁移、铁律)见 [db-migrations.md](db-migrations.md)。这里只记本仓 `env.py` 的接线要点与坑。

### 这个仓库的 env.py(0026 重定向后)

- **只显式 `import app.db.models`** 注册进 `SQLModel.metadata`,即 `target_metadata`。不再 `os.walk` 全仓找 `*models*`:原做法会把原型旧模型一并注册,造成表名冲突(0026)。新表加进 [app/db/models.py](../app/db/models.py),它是单一事实源。
- **`DATABASE_URL` 经 `app.config.settings` 读**(见上),不用 `alembic.ini` 占位。
- **真外键**:原型 env 里跳过 FK 的 `render_item` hack 已删。表间关系在 DB 层强制(参与者→手牌/用户,见 [db.md](db.md))。
- **`render_as_batch=True`**:sqlite 也能 ALTER,走 batch 重建;对 postgres 无害。
- **`script.py.mako` 硬带 `import sqlmodel`**:autogen 会引用 `sqlmodel.sql.sqltypes.AutoString` 等,不带则升级时 `NameError`(见 [changes/0026](refactor/changes/0026-p4-db-models-alembic.md))。

> 同步 vs 异步:应用运行时落库(P4 三之二)用 `create_async_engine`(psycopg3 异步);Alembic 用同步 `engine_from_config`。同一个 `postgresql+psycopg://...` URL 两者都支持,迁移不用另配同步驱动。本地验证可用同步 sqlite(`sqlite:///`)。

### 迁移历史(0026 重置)

- 原型的 4 支迁移(`0_1_0`..`0_1_3`)已删。
- 新架构从一支基线迁移重新起历史:`down_revision=None`,建 `user`/`handrecord`/`handparticipant`。
- 之后改模型 → autogenerate 增量出新版本,`-m "简述本次结构改动"`。

### 重构会涉及的迁移(预告)

- 密码哈希 `salt$rounds$digest` + `K_user` 双钥/版本(见 [auth.md](auth.md)):P5 给 `app/db/models.py` 的 `User` 加列 → 一支新迁移,外加数据迁移脚本。
- 运行时落库(P4 三之二):`OrmPersister` + `to_orm` 把 Write 载荷映射到 `app/db/` 表(见 [db.md](db.md) / [db-migrations.md](db-migrations.md))。

## Git 使用

### 分支模型(单人项目,从简)

- 主干 = `develop`,不是 `main`,目前只有这一条分支;远端 `origin/develop`,`git pull` 保持最新。
- 默认直接提交到 `develop`:单人开发、改动线性,不强制开功能分支。
- 只在两种情况开分支:① 大或有风险的重构,想隔离试错;② 想留 PR 做自我评审。
- 分支命名 `refactor/<阶段>-<简述>`,完成后合回:
  ```bash
  git switch -c refactor/p1-betting-rules   # 仅当需要隔离时
  # …干活、提交…
  git push -u origin refactor/p1-betting-rules
  git switch develop && git merge --no-ff refactor/p1-betting-rules
  git push                                  # 合回后推 develop
  ```

### 提交(commit)

- commit / push 前先做对抗式自 review(提交门槛),做法与结论落地见 [review.md](review.md)。
- 提交信息一律全英文:标题、正文、trailer 都是,便于检索,同 [log.md](log.md)「日志一律英文」;代码注释用中文(见 [coding_principle.md](coding_principle.md))。
- 提交信息引用变更记录编号 `changes/NNNN-*.md`(见 [refactor/README.md](refactor/README.md) §5),让 commit 与设计变更互相追溯:
  ```
  P0: core domain models + enums (refactor 0002)

  - core/domain.py: World/Room/Hand/Player/Seat/UserState (with uid)
  - core/enums.py: four state machines + transition tables
  Refs: docs/refactor/changes/0002-core-domain.md
  ```
- 一次提交是一个自洽单元:能编译、能过它该过的测试。别把半个功能和无关格式化混在一起。
- 代码与文档同提交:改了设计文档就和代码放同一个或紧邻提交,别让文档落后。
- 迁移文件、`poetry.lock`、生成的 wire TS 产物都要提交,它们是复现实现的一部分。

### 绝不提交(已在 [.gitignore](../../.gitignore))

- `*.env` / `.env` / `poker.env`、`*.key`、`.venv/`、`__pycache__/`、`node_modules/`、`original_password.txt`。
- 秘密零容忍:`K_user`、`session_token`、密码、`JWT_SECRET` 任何形式都不进 git,同 [log.md](log.md) 脱敏红线;新增秘密文件先加 `.gitignore`。
- 改配置项提交的是 `*.example`,不含真值;真值留在本地 `.env`/`poker.env`(同 [config.md](config.md))。

### 认证(一次性设置)

GitHub HTTPS 推送不能用账号密码。下列三选一,配一次即可,凭证会缓存。

- 浏览器授权(本机当前用法):`git config --global credential.helper store`,之后首次 `git push` 会弹浏览器登录 GitHub。
- PAT + HTTPS:建 fine-grained token(仅本仓库、Contents: Read and write);push 时用户名填账号、密码粘 token。
- SSH:`ssh-keygen` → 公钥加到 GitHub → `git remote set-url origin git@github.com:mogutusi/poker_platform.git`。

秘密(token/SSH 私钥)不进 git,也不明文写进文档或命令行历史。

### 日常流程

```bash
git status                       # 看清改了什么再提交
git add -p                       # 分块挑选,别无脑 git add .
# review                         # 对照 docs/review.md 自检 + 回填 changes/NNNN「自 review」段
git commit -m "..."              # 信息引用 changes/NNNN
git push                         # 默认推 develop
```

> 提交节奏跟 [refactor/README.md](refactor/README.md) 的「变更记录先行」走:先开 `changes/NNNN`,再写代码,完成后回填记录并提交,提交信息回指该记录。

## 约定(必须守住)

1. 新表/新列:改完模型 → autogenerate → 人工 review → upgrade;迁移文件和 `poetry.lock` 都进 git(详见 [db-migrations.md](db-migrations.md))。
2. 新架构表加进 [app/db/models.py](../app/db/models.py),env.py 只导它;原型 `*models*.py` 不再被 Alembic 追踪。
3. 其余见上文:`.env`/`poker.env` 不进 git、命令一律在 venv 内、提交信息全英文引用 `changes/NNNN`、秘密绝不进 git。
