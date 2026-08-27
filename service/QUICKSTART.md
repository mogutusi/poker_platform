# QUICKSTART:git pull 之后把后端跑起来

> 目标读者:在另一台机器上 `git pull` 完想立刻把服务跑起来的人。
> 设计上这个仓库是**零配置可跑**的——不需要手工创建任何 `.env`(原因见 §5),首跑只有两步。

## 1. 首次跑(新机器)

**前置**:Python ≥ 3.12、Poetry(没装的话见 [README.md](README.md),一条 curl 命令)。

```bash
cd service
poetry config virtualenvs.in-project true   # venv 建在 service/.venv(只需一次)
poetry env use python3.12                   # 指定解释器(只需一次)
poetry install                              # 装依赖(按 poetry.lock 精确复现)
.venv/bin/uvicorn app.shell.lifespan:app --host 0.0.0.0   # 起服务,默认端口 8000
```

起来之后服务**自动**完成:建本地 SQLite 库(`service/poker.db`)→ 建表 → 种好 **10 个** dev 用户(各 1000 积分):`alice`/`bob`/`carol`/`dave`/`eve`/`frank` 手动联调用,`smoke1`/`smoke2`/`smoke3` 归冒烟脚本、`gina` 归浏览器用例**专用**——别混用(局中离房要等手牌打完才驱逐,复用账号会让下一个用例进不去房,见 frontend/docs/dev.md)。不需要跑迁移、不需要建号。

> **数据库定位别误会**:设计目标库是 **PostgreSQL**([docs/architecture.md](docs/architecture.md) 技术栈固定,驱动 psycopg3 已随 `poetry install` 装好);"什么都不配就用本地 SQLite"只是 0045 定的**开发便利**(新检出零配置能跑测试/快速联调),不是换了设计。真跑 PostgreSQL 见 §2。

## 2. 用 PostgreSQL 跑(设计目标库;生产/正式联调用这个)

```bash
# ① 建库(一次):在你的 pg 实例上 createdb poker(或任意库名)
# ② 告诉服务连哪:建 service/.env 写一行(同名环境变量可覆盖它,优先级更高)
echo 'DATABASE_URL=postgresql+psycopg://user:pass@host:5432/poker' > .env
# ③ 先迁移建表(pg 库必须走 Alembic,顺序别反,见下):
.venv/bin/alembic upgrade head
# ④ 起服务(建表已就绪则幂等跳过;仍会种 dev 用户——当前入口是 dev shell):
.venv/bin/uvicorn app.shell.lifespan:app --host 0.0.0.0
```

- 同一个 `postgresql+psycopg://` URL 同步/异步双栖(Alembic 同步用、运行时异步用),**不用配两份**。
- **顺序别反**:空的 pg 库要**先 `alembic upgrade head` 再起服务**。直接起服务也能跑(dev 引导 `create_all` 会建表),但那样建的表**没有迁移历史**,以后 `alembic upgrade` 会撞表——`create_all` 与 Alembic 别在同一个库混用([docs/db-migrations.md](docs/db-migrations.md))。
- 之后每次拉到带新迁移的提交:跑一次 `alembic upgrade head` 即可(§4 那条"删 SQLite 重来"在 pg 上不需要、也别用)。

## 3. 验证它活着(三连)

```bash
# 注意:0094 之后**没有明文端点了**,上面这两个 curl 一律回 405。要手工打 REST,
# 用 frontend/scripts/smoke-client.mjs 的 restCall()(它带信封);验活最省事的是直接跑冒烟。
cd service && .venv/bin/pytest -q          # 可选:全量测试(约 6 秒)
```

ws 最快验法:**没有明文捷径了**(`/dev/ws?nick=` 已随 0086 退役,它无鉴权)。ws 一律是
`登录换 sid → ws://127.0.0.1:8000/ws?sid=<sid>` 且逐帧加密,手搓一条 console 语句已经做不到。
现成的跑法是前端仓库里的冒烟脚本,它用前端自己的加密实现打真后端:

```bash
cd frontend && npm run smoke        # 一手牌全程
npm run smoke:raise                 # 加注 / min-raise / 三人边池
```

## 4. 每次 git pull 之后

| 动作 | 什么时候需要 |
|---|---|
| `cd service && poetry install` | `poetry.lock` 变了(不确定就跑,无变化时是空操作) |
| **pg**:`.venv/bin/alembic upgrade head` | 拉到带新迁移的提交(正道,保数据) |
| **本地 SQLite**:`rm service/poker.db` | **服务起不来 / 报 `no such column` 之类**:dev 库是启动时 `create_all` 建的,它**不做结构迁移**——拉到改表结构的提交后旧库文件就过时了。dev 数据可丢,删掉重启自动重建+重种(想保数据也可改跑 alembic) |
| 什么都不用做 | 前端类型 `frontend/src/types/wire.gen.ts` 是提交进 git 的生成产物,pull 即最新 |

## 5. 配置:为什么不用建 .env

配置分两轨,**两轨都自带能跑的基线**:

| 轨 | 文件 | 说明 |
|---|---|---|
| 游戏参数(盲注/超时/dev 用户…) | `service/app/poker.env.example` | **它本身就是被加载的基线**(提交在 git 里),不是要你拷贝的模板。想本地改参数才需要:复制为 `service/app/poker.env`(gitignored)改值,它逐项覆盖 example |
| 基础设施(数据库地址) | `service/.env` | **连 PostgreSQL(设计目标库)在这里配**,一行 `DATABASE_URL=postgresql+psycopg://…`(见 §2);不配则退到本地 SQLite(开发便利的缺省,非设计变更) |

`poker.env` / `.env` 都不进 git;改了配置项记得同步对应 `*.example`(见 [docs/config.md](docs/config.md))。

## 6. 前端联调要知道的

- **只有加密一条路**(登录 + `/ws?sid=` + 需身份的 REST):dev 用户可真登录,共享口令和密钥在 `service/app/poker.env.example` 的 `DEV_PASSWORD` / `DEV_KUSER`(仅开发值)。明文 dev 端点已随 0086 退役。
- 协议与加密的完整说明:[../frontend/BACKEND_GUIDE.md](../frontend/BACKEND_GUIDE.md);国密实现的已知答案测试向量:[../frontend/crypto-test-vectors.json](../frontend/crypto-test-vectors.json)。

## 7. 真部署(内网生产)与 dev 的三个不同

1. **建库走迁移不走 create_all**:见 §2(`alembic upgrade head`;[docs/db-migrations.md](docs/db-migrations.md))。
2. **用户凭证用管理员 CLI 发放**,不用 dev 种子:`.venv/bin/python scripts/kuser_admin.py issue --name xxx`(生成随机口令 + K_user,带外发给用户;每周轮换见 [docs/dev.md](docs/dev.md)「K_user 管理」)。
3. ~~明文 `/dev/ws` 端点应移除~~ —— **已于 0086 移除**,dev 与生产在这一点上不再有差别。

## 常用命令速查

```bash
cd service
.venv/bin/uvicorn app.shell.lifespan:app --host 0.0.0.0   # 起服务
.venv/bin/pytest -q                                        # 全量测试
.venv/bin/alembic upgrade head                             # 迁移(pg / 保数据时)
.venv/bin/python scripts/kuser_admin.py list               # 密钥记账
.venv/bin/python scripts/gen_wire_ts.py                    # 重生成前端 ws 类型(改协议后)
.venv/bin/python scripts/gen_crypto_vectors.py             # 重生成加密测试向量(改加密层后)
```
