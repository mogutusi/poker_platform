# 0095 · 把 db-migrations.md 改成一份真能照着部署的指南

日期:2026-08-25 · 性质:**文档改实 + 缺口补齐(纯文档)**· 触发:用户原话「我不会在这台机器上部署,到时候需要一个指南来迁移,你确保 db-migrations.md 内容正确完备就好」。

## 判据

不是「读起来对不对」,而是:**换一台干净机器、目标 PostgreSQL,照着这一篇能不能把库建起来并跑起来。**

## 怎么审的

三路并行只读审计(迁移链本身 / 文档每条断言是否属实 / 当部署指南照做会在哪卡住),然后对抗式核实合并。**审计过程真的起了一个 PostgreSQL 16 实测**——这批的价值一大半来自那里:很多结论在 sqlite 上根本看不见。

31 条候选 → **17 条确认、3 条驳回**。驳回的里有一条是我给审计的**前提就错了**(我说 `alembic/versions/` 有 7 个迁移,实际 6 个——我把 `__pycache__` 数进去了),agent 拿 `alembic history` 驳回,如实记下。

**顺带把 pg 侧的空白填了**:此前本仓的迁移**从未在 pg 上跑过**(没有 `.env`,`DATABASE_URL=None`,一切都落在 sqlite)。现在链本身已在 pg16 上验过:6 支 `upgrade head` 全通、`alembic check` 无漂移、无 pg 不兼容写法。这条结论直接写进了文档。

## 确认的问题(按「照做会不会翻车」排序)

### blocker(照做走不下去)

1. **驱动 import 不了**。pyproject 声明的是裸 `psycopg`,不带 extra,lock 锁的是纯 Python 包,需要系统 libpq。干净机器上 `import psycopg` 直接 ImportError——**本机现在就是这个状态**(我复验过)。文档却把它当「`poetry install` 就有了」。
2. **没有「目标库准备」这一步**。不说 pg 版本、不说 `createdb`、不说建什么 role。Alembic 只建表,不建 database 也不建 role。实测:库不存在 → `OperationalError`;迁移账号非 owner → pg15+ 的 `permission denied for schema public`。
3. **静默回落 sqlite,两侧都咬**。`DATABASE_URL` 缺省是 `None`,alembic 与运行时**各套各的方言默认**且都不报错。最致命的形态:你把 pg 迁移好了,起服务时 env 没生效 → 回落 sqlite → `create_all` 自己建表 + 种 dev 用户 → **接口全 200,pg 库一行数据也没有**。
4. **`create_all` 那条铁律在代码里无路径可守**(= BUG-12 的 N-e11)。文档写「生产绝不靠 create_all」,而唯一的 ASGI 入口在 `setup()` 里**无条件** `create_all`,没有开关。实测反序:先起服务再迁移 → `DuplicateTable`,且库里没有 `alembic_version`,**再也升不上去**。而「先迁移后起服务」这个救命顺序,文档一个字没写。
5. **生产库的 dev 种子没人管**。启动无条件种 10 个账号,用的是提交在 git 里的共享口令与共享 SM4 密钥;更隐蔽的是**显式 id 插入不推进 pg 的 SERIAL 序列**,导致 `kuser_admin.py issue` 发第一个正式账号就撞主键。**这条在 sqlite 上完全看不见**(rowid 取 max+1),是纯 pg 独有的坑。种子还关不掉(`DEV_USERS` 是 `min_length=1`)。

### wrong(说法与事实不符)

6. L18/L56 说缺省是 `sqlite:///./poker.db`(= BUG-12 的 N-e10):那只是 alembic 侧兜底,真正的缺省是 `None`,运行时套的是异步形。把 L56 那行照抄进 `.env`,应用启动立刻崩。
7. `upgrade head --sql` 那一行两处错:**仅 pg 可用**(sqlite 走到 `b8ca88a687af` 的 batch 重命名要连库反射);不带 `<from>:` 是从 base 生成**全量建库** SQL,不能用来预审增量。
8. 「唯一 DB 写者」漏了例外:鉴权列走 `app/db/user_writes.py` 同步直写,`db.md` 里本来写对了,这篇没同步。
9. `render_as_batch` 位置与语义都错:它在 `env.py` 不在模板;而且只影响 **autogenerate 的渲染**,对已写好的迁移执行无作用——**手写**迁移要自己包 batch。

### missing / nit

10. 没有验收清单、没有 `alembic check`、没有备份要求、没有 downgrade 的数据后果、没回链 QUICKSTART.md(仓里真正写对 pg 部署的那篇)、新迁移没有「在 pg 上复验」的闭环、`--workers >1` 会破坏「唯一写者」却全仓无警告、`HandRecord.room` 有一处真实的 `server_default` 漂移(Alembic 建的库有 `DEFAULT ''`、`create_all` 建的没有,而 `alembic check` 默认不比对它、永远不报)。

## 实际改了什么

整篇重写,从 121 行扩到 ~290 行,结构改成「**§0 部署 / §1-4 日常 / §5-6 应急**」:

- **新增 §0「部署到 Postgres:从零到能跑」**,编号顺序是硬的:0.1 驱动自检 → 0.2 建库建角色 → 0.3 配置(一个 URL 两个消费者 + 静默回落的两条强制核对)→ 0.4 首次建库顺序 + 反序的自救 → 0.5 验收清单 → 0.6 **生产首次上线的数据善后**(删 dev 账号 + `setval` 推序列)→ 0.7 只能单进程单 worker。
- **新增 §5 迁移链与回滚的数据后果表**:逐支标注 downgrade 会丢什么,尤其 `49417b108733` = **抹掉全部登录凭证**。
- **新增 §6 常见报错速查**:把实测到的 8 种失败形态列成「报错 → 病因 → 处置」。
- **口径改实**:`create_all` 那条从「绝不靠」改成「本仓总会跑,已迁移的库上是幂等 no-op,顺序反了会废库」;缺省 URL、`render_as_batch`、`--sql`、唯一写者例外逐条改对。
- §3 补第 5 步「合并前在空 pg 库上复验」,带一行 docker 起临时 pg 的命令。
- §7 回链 `../QUICKSTART.md`。

## 验证

- 全文相对链接脚本扫过:**0 条死链**。
- `alembic history` 复核:6 支单一线性,head = `b8ca88a687af`——文档里的链表与之逐条对齐。
- `alembic check` → `No new upgrade operations detected`。
- `import psycopg` 失败、`DEV_USERS` 的 `min_length=1`、lifespan 无条件 `create_all` + `seed_dev_users`——四条 blocker 依据我都亲自复验过,不是只信 agent。
- 后端 pytest 760 passed(未动代码)。

## 自 review

按 [review.md](../../review.md) 七维,本篇只有 ②③⑥⑦ 实质相关。

- **① 分层 / 不变量**:零代码改动。**但文档里新写进了两条此前没被文档化的不变量**:「只能单进程单 worker」(内存权威 + 唯一 DB 写者的直接推论)与「先迁移后起服务」。两条都是既有代码性质的如实描述,不是新约束。
- **② 代码↔文档同步**:本篇的正题。九处不实口径逐条改实,每条都有实测依据。
- **③ 文档↔文档一致**:回链 QUICKSTART.md(此前链接是单向的);「唯一 DB 写者」的例外与 [db.md](../../db.md) 对齐;[BUGS.md](../BUGS.md) 划掉 BUG-12 并**更正它的摘要**——原摘要说「违反配置铁律」,而 0072 原文说的是 create_all-vs-Alembic 铁律,config.md 的配置铁律这篇一条都没违反(照抄旧摘要去改就会改错地方)。
- **⑥ 测试充分**:文档没有自动化守门,**如实记为缺口**。能自动化的只有链接扫描(本次手工跑了)。另一条更值得记的:**文档里所有 pg 相关断言的有效期,取决于没人再改迁移链**——一旦有新迁移,§0.5 的验收和 §3 第 5 步的 pg 复验就得重跑,否则这篇又会退回「纸面推理」。
- **⑦ 流程账本**:本篇即账本。审计用了并行 agent,**驳回项如实记**(含我自己给错的前提)。

### 未做,留档(**都是代码改动,不在「改文档」的范围内**)

用户这次的要求明确是「确保 db-migrations.md 正确完备」,所以下面三条**只写进文档当操作步骤,没有改代码**:

1. **`psycopg[binary]`**:目前要靠目标机自己有 libpq。改 pyproject + 重锁是一行的事,但那是依赖变更,应当单独一批并在真 pg 上验。
2. **`SEED_DEV_USERS` 开关**:生产库现在只能靠上线后手工 `DELETE` + `setval` 善后。要根治得给 lifespan 加开关并放宽 `DEV_USERS` 的 `min_length`。
3. **`server_default` 漂移收敛**:`HandRecord.room` 那处,或补进模型、或打开 `compare_server_default`。
