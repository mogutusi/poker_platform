# 0027 · 原型拆除(删 legacy `app/` 原型模块/入口 + 文档去链/历史化)

日期:2026-06-24 · 范围:删除 `app/pokertable`/`app/user`/`app/auth`/`app/handrecord`/`app/database` 五个原型包 + `app/main.py`/`app/app_route.py`/`app/init.py` 三个原型入口 + `app/config.py`(原型期基础设施配置)+ `app/docs/docs_generator.py`(原型 wsm 文档生成器)+ `app/extensibility.md`(原型房间扩展便签);同步把所有指向被删文件的设计文档引用历史化/去链。

## 背景 / 打算改什么

[0026](0026-p4-db-models-alembic.md) 落地 `app/db/` 新架构 ORM 模型时,**明确把「原型拆除」列为 P4 三之二(`OrmPersister`)的前置**:原型 `app/user/models.py`/`app/handrecord/models.py` 与新 `app/db/models.py` 把**同名表**(`user`/`handrecord`)挂在**同一全局 `SQLModel.metadata`** 上,任何同进程同时 import 两套都会 `InvalidRequestError: Table 'handrecord' is already defined`。`OrmPersister` 一旦 `import app.db.models` 并起真 engine,就踩这颗雷。本篇拆掉原型,**解除该 collision + 兑现「不留死代码」**,把 P4 三之二从「被阻塞」降级为「只差加一个 async driver 依赖」。

### 解耦已独立核实(不是只信 0026 的话)

- 新代码(`app/core`/`app/shell`/`app/wire`/`app/db`/`scripts`/`tests`)**零** import 原型五包及三入口(双向 grep 均空)。
- 原型**零** import 新代码。
- 全量测试(254)**零** import 原型;`tests/` 不依赖任何被删文件。
- dev shell 启动入口是 `app.shell.lifespan:create_app`(自带 `app`),**与原型 `app/main.py` 无关**;只依赖 `app.gameconfig` + `app.core`/`app.shell`。
- 前端用本地 mock(见 [0017](0017-wire-first-batch.md) 决策 8),**未连**原型 REST/ws;`scripts/gen_wire_ts.py` 只读 `app/wire`,从不碰 `pokertable/wsm_schemas`。
- 原型 `/auth/refresh` 路由建了但**从未注册**进 `app_route.py`(原型自身就是死路由),`app/init.py` import 时 `asyncio.run` 跑 9 人种子(原型遗毒),都属该删。

> 结论:拆除自包含、低风险,**不会**破坏 dev shell、测试、前端 codegen。

### 删 / 留清单(开工前定)

**删(原型,git rm 保历史):**

- `app/pokertable/`(enums/exceptions/gameconfig/gamelogic/models/routes/services/websocket/wsm_schemas)——单房硬编码 `room1`、IO 混状态、零鉴权、一堆 runtime bug(见 README §2);其规则已被 `core/rules/` + `reduce.py` 取代,wsm 已被 `app/wire/` 取代,enums 已迁 `core/enums.py`。
- `app/user/`、`app/auth/`(含 `services.py.bak`)、`app/handrecord/`、`app/database/`——登录/JWT/手牌查询/session 原型;新架构由 P5 国密信道 + `app/db` + shell PersistWriter + P7 REST 取代。
- `app/main.py`/`app/app_route.py`/`app/init.py`——原型 FastAPI 入口/路由汇总/种子脚本。
- `app/docs/docs_generator.py`——import `app.pokertable.wsm_schemas`,拆后必 ImportError;已被 `scripts/gen_wire_ts.py` 取代。删后 `app/docs/` 空目录自然消失。
- `app/extensibility.md`——原型「多房/座位」便签,引 `pokertable/websocket.py`/`models.py`;房间设计已归 [lobby.md](../lobby.md)。
- `app/config.py`——原型期基础设施配置(`DATABASE_URL`/`JWT_SECRET`/refresh pool 字段),import 即 `os.getenv("DATABASE_URL").strip()`、无 `.env` 即崩。拆后**零 import** = 死代码 + import 即崩的雷。**自 review 改判**(开工前曾计划保留):按「不留死代码」一并删——README §3 的 `config.py` 是**目标槽位**(提案),不要求保留原型实现;真基础设施配置由 **P8 配置收编**用 `pydantic-settings` 新建干净版(`DATABASE_URL` 归位、去原型 JWT 字段)。当前 alembic 直读 `os.environ` 的 `DATABASE_URL`,无人依赖该文件。

**留:**

- `app/gameconfig.py`(新游戏参数)、`app/core`/`app/shell`/`app/wire`/`app/db`、`scripts/scripts.py`(通用 .py 转储工具,不 import 原型)、`alembic/`、`tests/`。
- `lib/ttxsgm`(国密库)pyproject 依赖——P5 国密信道要用,**不动**;`treys`/`psycopg`/`sqlmodel` 同理(新栈/迁移在用)。
- `pyjwt` pyproject 依赖——原型 `app/auth/jwt.py`(已删)的实现没了,但 **P7 REST 鉴权走 JWT Bearer**(见 [rest.md](../rest.md) / [connection.md](../connection.md)「REST 另走现有 JWT」),属前瞻依赖,**不动**(同 `ttxsgm` 处理);P5/P7 落地时整合新 JWT 方案。

### 文档同步(被删文件的引用一律历史化/去链)

设计文档大量把原型文件当「现状/反例/起点」热链。拆后这些链全断,按 [keep-docs-in-sync] + review.md 维度③,**同次改**:

- `docs/refactor/README.md` §2「现状代码结构」整表:原型已删,改为「**历史**问题清单(代码见 git history),留作 reduce/shell 设计的 bug 备忘」+ 去掉死链。
- `docs/coding_principle.md`(`services.py.bak` 反例)、`architecture.md`(旧 services.py 行锁)、`auth.md`(旧 routes.py 零鉴权)、`config.md` + `dev.md`(旧 pokertable/gameconfig.py)、`core.md`(gamelogic/services bug 备忘)、`models.md` + `rest.md`(原型 handrecord)、`wire.md`(wsm_schemas)、`log.md`(原型 `field_serializer` 脱敏 → 改述为新 wire DTO 的**结构性缺位**脱敏)、`TODO.md`(迁移来源/参考链)——逐处去链 + 改述为「已于 0027 拆除,见 git history」。
- `docs/db-migrations.md` §12/§59 + `dev.md` 的 metadata-collision「过渡期铁律」:collision **已由本篇解除**,改为历史化(保留「一进程别导两套」的通用告诫,但去掉「P4 三之二前必做拆除」这条已兑现的前置)。
- `service/README.md`「Run the Application」:`python -m app.main` → dev shell `uvicorn app.shell.lifespan:app`。

### 验证门槛

1. 全量测试仍绿(254,新栈不依赖原型)。
2. `import app.shell.lifespan; create_app()` 不崩(dev shell 可装配)。
3. `python scripts/gen_wire_ts.py --check` 通过(前端 codegen 不受影响)。
4. `grep` 复验:`docs/`(除 changes/ 历史档)无残留指向被删文件的死链。

## 实际改了什么

**删除(27 文件,`git rm` 保历史):**

- `app/pokertable/`:enums / exceptions / gameconfig / gamelogic / models / routes / services / websocket / wsm_schemas。
- `app/user/`(models/routes/services)、`app/auth/`(jwt/models/routes/services + `services.py.bak`)、`app/handrecord/`(models/routes/services)、`app/database/core.py`。
- `app/main.py`、`app/app_route.py`、`app/init.py`。
- `app/config.py`(**自 review 改判一并删**:原型期 `DATABASE_URL`/`JWT_SECRET`/refresh pool 配置,零 import + import 即崩;见下「自 review」②/⑤)。
- `app/docs/docs_generator.py`(import 已删的 `pokertable.wsm_schemas`;`app/docs/` 随之空)、`app/extensibility.md`。

**保留(理由见上「删/留清单」):** `app/gameconfig.py`、`app/core`/`app/shell`/`app/wire`/`app/db`、`scripts/`、`alembic/`、`tests/`、`lib/ttxsgm`(P5 依赖)、`pyjwt`(P7 REST 依赖)。

**文档同步(14 处,全部去链 + 历史化):**

- `docs/refactor/README.md` §2:标题改「历史:被取代的原型代码(已于 0027 拆除)」,表格去掉所有原型文件热链(改纯文本),新增「已被谁取代」列把每个原型模块映到现栈(enums→`core/enums.py`、gamelogic→`core/rules/`+`deck.py`、services→`reduce.py`、websocket→`shell/*`、wsm_schemas→`wire/*`、models→`core/domain`+`wire`+`db`、handrecord→`db/models.py`+P7);前端行保留(仍存在)。
- 散链历史化:`coding_principle.md`(.bak 反例)、`architecture.md`(旧 services 行锁)、`auth.md`(旧 routes 零鉴权)、`config.md`+`dev.md`(旧 pokertable/gameconfig.py + env.py os.walk 注)、`core.md`(gamelogic/services bug 备忘)、`models.md`+`rest.md`(原型 handrecord)、`wire.md`(wsm_schemas ×2)、`log.md`(原型 `field_serializer` → 改述为 wire DTO **结构性缺位**脱敏,并指 `wire/server.py`)、`TODO.md`(enums 迁移源 + wsm_schemas 参考)。
- `docs/db-migrations.md` §接线 + §铁律:metadata collision 由本篇解除——「过渡期铁律 + P4 三之二前必做拆除」改为通用告诫(别在一进程把同名表注册两次)+ 标注冲突源已消除、`OrmPersister` 可放心 import。
- `service/README.md`「Run the Application」:`python -m app.main` → `uvicorn app.shell.lifespan:app`(dev shell,标注 0027 删了 legacy 入口 + dev 用户清单)。
- `docs/refactor/TODO.md`:新增「原型拆除 — 0027」完成项 + 更新 P4 三之二备注(collision 已解,只差 async driver)。

## 测试 / 验证

无新增测试(纯删除 + 文档,无运行时逻辑)。三道门槛全过:

1. **全量测试 254 passed**(删原型五包/三入口/`config.py` 前后同数 → 没有任何测试依赖原型;collection 无变化)。
2. **dev shell 可装配**:`import app.shell.lifespan; create_app()` OK,`/dev/ws` 路由在(删 `config.py` 后复测仍 OK)。
3. **前端 codegen 不受影响**:`python scripts/gen_wire_ts.py --check` OK(只读 `app/wire`,从不碰 `wsm_schemas`)。
4. **去链复验**:`grep` 全 `docs/`(除 changes/ 历史档)无残留指向被删文件的 markdown 死链;`app/scripts/alembic/tests` 无 `import app.(pokertable|user|auth|handrecord|database|main|init|app_route|config)`(仅 `alembic/env.py` 注释提及 `app.config` 已同步去除);`frontend/src` 无任何对原型后端(`pokertable`/`/room`/REST)的引用;`pyproject.toml` 无 `app.main` 入口。

## 自 review(push 前对抗式 7 维)

> 方法:多 agent 对抗式 7 维复审(3 维度 finder × 各自 finding,逐条由独立 verifier「默认反驳」核实)。候选 17、确认真问题去重后 **3 条可行动**(全已修)+ 一片正向确认(去链/映射/隐私准确)。**SAFE-TO-PUSH**,无 critical/major 残留缺陷。

**对抗式抓到 + 已修(本轮 review 驱动的额外改动):**

- **(维度②/⑤)`app/config.py` 死代码**:开工前计划「保留」,review 抓出它拆后**零 import + import 即 `os.getenv(...).strip()` 崩**,与「不留死代码」硬规则冲突;adversarial panel 对「保留」一项 split(一方判 REAL、一方判可接受的 P8 deferral)。**裁决:删**——README §3 的 `config.py` 是目标槽位(提案),不要求保留原型实现(原型期 JWT 字段也本就要弃);真基础设施配置 P8 用 `pydantic-settings` 新建。已删 + 改判记入「删/留清单」与「实际改了什么」。
- **(维度②/③)`dev.md` 残留 `python -m app.main`**:README 的运行命令改了,但 `dev.md` 的「跑东西」段还留着 3 条 `python -m app.main`(原型入口),review 抓出 → 改为 `uvicorn app.shell.lifespan:app`;连带把 `dev.md`/`alembic/env.py` 里「不 import `app.config`」的注释/行(指向已删模块)同步去除。
- **(维度③)`db-migrations.md` collision 改写丢了历史关系**:把「P4 三之二前置」重写成纯通用告诫后,读者看不出「这曾是前置、现已由 0027 解除」。已补回历史框架(前置→已解除)。

**逐维核(本次 diff = 27 删 + 14 文档改,纯删除 + 文档,无运行时逻辑):**

- **① 分层/不变量**:`grep` 复验 `app/(core|shell|wire|db)`、`tests`、`scripts`、`alembic` 均**零** import 被删五包/三入口/`config`;删除不碰任何不变量(无 reduce/core 改动)。dev shell 仍只依赖 `gameconfig`+`core`+`shell`。✓
- **② 代码↔文档同步**:被删文件的每处文档引用都同次改(14 处);`alembic/env.py` 注释里指向 `app.config` 的描述同步去除。无「文档≠代码」残留。✓
- **③ 文档↔文档一致**:`grep` 全 `docs/`(除 changes/)**无残留死链**指向被删文件;README §2「已被谁取代」映射逐条对真栈核实(enums→`core/enums.py`、wsm_schemas→`wire/`、gamelogic→`core/rules`+`deck`、websocket→`shell/*`、handrecord→`db/models.py`)——verifier 判**准确**。✓
- **④ 数据模型**:无模型改动(`app/db/models.py` 未动)。✓
- **⑤ 规范合规(无死代码)**:`services.py.bak` + 全部原型 + `config.py`(死 + crash-on-import)均删尽;保留项(`ttxsgm`/`pyjwt`/`treys`/`psycopg`)都是**有据前瞻依赖**(P5/P7),已在「留」清单逐条点名理由。✓
- **⑥ 测试充分**:无新逻辑 → 无新测试;254 全绿、collection 数删前后不变(证零测试依赖原型)。✓
- **⑦ 流程账本**:变更记录开工前「打算」↔ 收工「实际」对照齐(含 config.py 改判差异);TODO 勾项 + P4 备注更新;提交信息将全英文 + 引用 0027。✓

**正向确认(verifier 判 REFUTED / 非缺陷)**:`log.md` 结构性脱敏改写**准确**(`wire/server.py` 仅 `HoleCards`/`ShowdownReveal` 携底牌,广播 DTO 结构性缺位);README §2 映射准确;去链完整;无孤儿 import。

## 待办 / 下一步

- **P4 三之二**:collision 已解除,加 async driver(`aiosqlite`/`asyncpg`)依赖后即可落 `to_orm` + `OrmPersister`(async session UPSERT/INSERT + `ON CONFLICT(dedupe_key)`)+ lifespan 接真 session 替 `NullPersister` + 载入(Receiver 读 DB 富化 `JoinRoom`)。
- **P8 配置收编**:**新建** `app/config.py`(原型版已删):接 `pydantic-settings` + `.env`/`poker.env`、`DATABASE_URL` 归位、按需的鉴权字段随 P5/P7 加,并把 `app/gameconfig.py` 的具名常量改 env 驱动。
- treys 边池/dead-blind 的原型**参考实现**(`pokertable/gamelogic.py`)删后仅存 git history;`core/rules/sidepot.py`+`blinds.py` 已取代,本提交信息会点名以便回溯。
