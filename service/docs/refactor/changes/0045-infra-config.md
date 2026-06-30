# 0045 · 基础设施配置收编:app/config.py(DATABASE_URL → .env)

日期:2026-06-30 · 范围:`app/config.py`(新建 `Settings`)、`service/.env.example`(新建,提交)、`app/db/engine.py`(改用 `settings`)、`alembic/env.py`(改用 `settings`)、`docs/config.md`/`dev.md`、`tests/test_config.py`。完成「两套配置文件」(config.md / dev.md)的基础设施那一轨——承接 0042/0043/0044 的游戏参数轨(gameconfig)。

## 背景 / 为什么

README §3 / dev.md「两套配置文件」:**游戏可调参数**走 `app/poker.env`+`gameconfig`(已收编,0042);**基础设施**(`DATABASE_URL`/未来 JWT)走 `service/.env`+`app/config.py`。后者一直没建——`DATABASE_URL` 被 `engine.py` 与 `alembic/env.py` **各自 `os.environ.get` 直读**(无 `.env` 加载、散两处)。本批新建 `app/config.py` 作基础设施配置的单一典型化入口,并接 `.env`。

## 关键设计决策(批判性,与 dev.md 对齐)

1. **`DATABASE_URL` 有安全 dev 默认(`None` → 消费方套本地 sqlite),与 gameconfig「无默认」相反——这是有意区别**:dev.md 明定「免 `.env` 也能跑迁移/dev」。`DATABASE_URL` 缺省 = 本地 sqlite(非密钥),故可有默认;**未来密钥(JWT_SECRET,P5)应无默认(fail-closed)**。`config.md` 同步记此区别(基础设施可有 dev 默认 / 密钥不可)。
2. **同步/异步 sqlite 默认的方言分叉留在消费方**:`engine.py`(运行时,异步)`settings.DATABASE_URL or "sqlite+aiosqlite:///./poker.db"`;`alembic/env.py`(同步)`or "sqlite:///./poker.db"`。生产 `DATABASE_URL=postgresql+psycopg://…` 对两者通用(psycopg3 同步+异步同 URL,dev.md)。**单一事实源 = `DATABASE_URL` 这个 env 变量**;方言默认是消费方各自的事,天然不同。
3. **alembic 也走 `settings`(消除 `.env` 与运行时不一致的脚枪)**:若只给 `engine.py` 接 `.env`、alembic 仍只读 `os.environ`,则「`.env` 里写了 `DATABASE_URL=postgres`、跑 app 连 postgres 但 `alembic` 落 sqlite」会静默错配。两者都经 `settings` → 一致。**alembic 仍能 headless 跑**:`Settings` 不会因缺 `.env` 崩(`DATABASE_URL` 有默认、缺文件静默跳过),正是原 env.py「不依赖会崩的 Settings」之**意图**,只是换实现达成;`DATABASE_URL=… alembic upgrade head` 经 os.environ 优先仍覆盖。**约束(写进 config.py + dev.md)**:P5 给 `Settings` 加必填密钥时,必须给默认或拆独立 settings,**保 alembic import `settings` 仍能无密钥跑迁移**。
4. **`env_file` 锚定 `service/`(`Path(__file__).parent.parent`),不依赖 CWD**(同 gameconfig)。`.env` 在 `service/`(dev.md),gitignored;`.env.example` 提交、**secret-free**(只示 postgres URL 格式 + sqlite 缺省说明,无真值)——与 `poker.env.example` 不同:后者是**被加载的基线**(gameconfig 无默认),前者纯文档(`DATABASE_URL` 有默认,免 `.env` 也跑)。
5. **本批只收编 `DATABASE_URL`**(YAGNI):JWT 等随 P5 加。`extra="ignore"` 容 `.env` 里未来/其它键。

## 打算改什么(开工前)

- `app/config.py`:`Settings(BaseSettings)`(`env_file=service/.env`、`extra="ignore"`、`case_sensitive=False`)+ `DATABASE_URL: str | None = None` + 单例 `settings` + P5 密钥约束注释。
- `service/.env.example`:secret-free 模板(`DATABASE_URL=` 空 + postgres 示例注释)。
- `app/db/engine.py`:`database_url()` → `settings.DATABASE_URL or DEFAULT_DATABASE_URL`;删 `import os`。
- `alembic/env.py`:`settings.DATABASE_URL or "sqlite:///./poker.db"`;删 `import os`;更新头注释(改用 settings 的理由 + headless 仍成立)。
- `docs/config.md`(两套配置:基础设施轨落地 + 默认哲学区别)、`dev.md`(表行:.env 由 app/config.py 读)。
- `tests/test_config.py`:Settings 缺省 None / env 覆盖 / `engine.database_url()` 回落异步 sqlite / 用配置值 / 空串回落。
- 验证:全量测试 + `alembic upgrade head`/`downgrade base`(sqlite)无回归。

## 实际改了什么

- **`app/config.py`**(新建):`Settings(BaseSettings)`(`env_file=_ROOT/".env"`、`extra="ignore"`、`case_sensitive=False`;`_ROOT = Path(__file__).parent.parent = service/`)+ `DATABASE_URL: str | None = None` + 单例 `settings`。头注释写明默认哲学(基础设施可有 dev 默认 / 密钥须无默认)+ headless 约束(P5 加必填密钥须给默认或拆类,保 alembic 免密钥跑迁移)。
- **`service/.env.example`**(新建,提交,secret-free):`DATABASE_URL=` 空 + postgres 示例注释 + 「缺省 sqlite、免 .env」说明。`git check-ignore` 验:`.env.example` 可提交、`service/.env` 被忽略。
- **`app/db/engine.py`**:`database_url()` 由 `os.environ.get(...)` 改 `settings.DATABASE_URL or DEFAULT_DATABASE_URL`(异步 sqlite 默认保留);删 `import os`;更新头注释。
- **`alembic/env.py`**:`set_main_option("sqlalchemy.url", settings.DATABASE_URL or "sqlite:///./poker.db")`(同步 sqlite 默认保留);删 `import os` + 加 `from app.config import settings`;头注释改写(改用 settings 的理由 + headless 仍成立 + os.environ 优先覆盖)。
- **`tests/test_config.py`**(新建,7):`Settings(_env_file=None)` 缺 env → `DATABASE_URL is None` / env 覆盖 / `engine.database_url()` 缺省回落**异步** sqlite(`+aiosqlite`,区别于 alembic 同步默认)/ 用配置值 / 空串回落(`or` 兜 None 与 "")+ **自 review 补**:写临时 `.env` 验「无 env 变量读文件值」+「os.environ 优先于文件」(钉死不变量 d)。
- **文档**:`config.md`(基础设施轨落地 0045 + 默认哲学区别 + 修我在 0042 引入的死链「下文两套配置」→ dev.md)、`dev.md`(.env 表行 + alembic DATABASE_URL 经 settings 读 ×2 处 + headless 约束)、`db-migrations.md`(env.py ② DATABASE_URL 经 settings 读)、`TODO.md`(0042 余项 / 0026 尾 / 0027 标 0045 落地、配置两轨齐全)。

426 全绿(419→426,+7);core 无越层 import(本批不碰 core);`alembic upgrade head`→`downgrade base`(scratch sqlite,经 `DATABASE_URL=… ` 覆盖)round-trip 通,确认 alembic import `app.config` 无回归。

## 自 review

方法:对照 [review.md](../../review.md) 跑 **3 维 compact 对抗 review 子代理工作流**(config/消费方正确性 · 测试/gitignore/密钥 · 文档/账本;每代理对候选自反驳后才报)。**3 agent、7 确认(0 真 code bug)**:config 正确性维经 agent **实跑**(`engine.database_url()` 输出 + scratch alembic round-trip + 造真 `.env` 验隔离)确认 —— `_ROOT` 锚 service/、None/空串 `or` 兜底、异步/同步 sqlite 默认分叉保留、os.environ 优先、删 `import os` 无残留、无 import 环、alembic headless 不崩,全 clean。逐维:

- **① config / 消费方正确性**:`settings.DATABASE_URL or DEFAULT` 在 engine(异步 sqlite)/ alembic(同步 sqlite)各自方言默认**未混淆/未交换**;os.environ > `.env` > 默认优先级成立(agent 造冲突 `.env`+env 实证 env 胜);删 `import os` 后无 `os.*` 残留;app.config 不 import app → 无环;alembic import app.config **headless 不崩**(DATABASE_URL 有默认)——agent scratch `alembic upgrade/downgrade` 通。`findings:[]`。
- **② 测试 / gitignore / 密钥**:`.env` gitignored、`.env.example` 可提交且 secret-free(agent `git check-ignore` 实证);**测试隔离 clean**——agent **造真 `service/.env`(含假 prod URL)跑全套仍绿**(`_env_file=None`/`monkeypatch.setattr` 旁路单例),dev 本地 `.env` 不漏进断言。**抓 1 test-gap**:env-override 测用 `_env_file=None`,只证「无文件时 env 生效」、未证「env 压过在场文件」(不变量 d)——补 2 测(写临时 `.env`:无 env 读文件值 / env 压文件)实跑钉死。
- **②③ 文档 / 账本**:**抓 2 doc 漏改**——`db-migrations.md:12` 仍写 alembic「DATABASE_URL 从环境变量读」(已改经 settings)、`TODO.md:77`(0027 行)未标 0045 落地(已补「已于 0045 落地」,与 73/82 一致)。0026/0027 changes 记录不改(历史)。
- **⑤ 规范**:`app/config.py` 注释讲默认哲学(基础设施可有 dev 默认 / 密钥须无默认)+ headless 约束(P5 必填密钥须给默认或拆类);`.env.example` secret-free。
- **⑦ 账本**:打算↔实际一致;测 419→426;**提交须 `git add -A`**——review 抓到 `app/config.py`/`.env.example`/`test_config.py` 是**未跟踪新文件**,`git commit -a` 会漏掉 `app/config.py`(engine.py/alembic import 它)致 ModuleNotFoundError;`git add -A` 全staged,提交后「提交」声明即真。提交引用 0045、全英文。

**对抗核实存活 / 采纳 / 驳回**:7 候选全 survives——采纳 3(① test-gap 补 env>file 优先级测、② db-migrations.md 漏改、③ TODO:77 一致),其余 4 是「pre-commit 未跟踪/隔离 clean/gitignore clean」的状态确认(commit-hygiene 用 `git add -A` 解、隔离与 ignore 经 agent 实证无缺陷,记为已核)。0 真 bug;review 兑现「绿测 ≠ 可提交」——抓出「优先级不变量无回归护栏」+ 两处我漏改的文档 + 提交陷阱(`-a` 漏未跟踪文件)。

## 待办 / 下一步

- P5:`Settings` 加 `JWT_SECRET` 等(无默认 / 或独立 settings,守决策 3 的 headless 约束)。
- 配置两轨至此齐全:游戏参数(`poker.env`+gameconfig)/ 基础设施(`.env`+config)。
