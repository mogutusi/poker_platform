# QUICKSTART:git pull 之后把后端跑起来

> 目标读者:在另一台机器上 `git pull` 完想立刻把服务跑起来的人。
> 设计上这个仓库是**零配置可跑**的——不需要手工创建任何 `.env`(原因见 §4),首跑只有两步。

## 1. 首次跑(新机器)

**前置**:Python ≥ 3.12、Poetry(没装的话见 [service/README.md](service/README.md),一条 curl 命令)。

```bash
cd service
poetry config virtualenvs.in-project true   # venv 建在 service/.venv(只需一次)
poetry env use python3.12                   # 指定解释器(只需一次)
poetry install                              # 装依赖(按 poetry.lock 精确复现)
.venv/bin/uvicorn app.shell.lifespan:app --host 0.0.0.0   # 起服务,默认端口 8000
```

起来之后服务**自动**完成:建本地 SQLite 库(`service/poker.db`)→ 建表 → 种好 6 个 dev 用户(`alice`/`bob`/`carol`/`dave`/`eve`/`frank`,各 1000 积分)。不需要跑迁移、不需要建号。

## 2. 验证它活着(三连)

```bash
curl http://127.0.0.1:8000/lobby/rooms     # → [](空数组,房间是动态建的)
curl http://127.0.0.1:8000/leaderboard    # → dev 用户的积分列表
cd service && .venv/bin/pytest -q          # 可选:全量测试(约 6 秒)
```

ws 最快验法(浏览器 console):

```js
const ws = new WebSocket("ws://127.0.0.1:8000/dev/ws?nick=alice");   // 明文 dev 端点
ws.onmessage = e => console.log(JSON.parse(e.data));
ws.onopen = () => ws.send(JSON.stringify({ type: "join_room", room: "test" }));
// 应收到 user_joined + state_snapshot —— 房间 "test" 被自动创建了
```

## 3. 每次 git pull 之后

| 动作 | 什么时候需要 |
|---|---|
| `cd service && poetry install` | `poetry.lock` 变了(不确定就跑,无变化时是空操作) |
| `rm service/poker.db` | **服务起不来 / 报 `no such column` 之类**:dev 库是启动时 `create_all` 建的,它**不做结构迁移**——拉到改了表结构的提交后,旧库文件就过时了。dev 数据可丢,删掉重启会自动重建+重种;想保数据则改跑 `.venv/bin/alembic upgrade head` |
| 什么都不用做 | 前端类型 `frontend/src/types/wire.gen.ts` 是提交进 git 的生成产物,pull 即最新 |

## 4. 配置:为什么不用建 .env

配置分两轨,**两轨都自带能跑的基线**:

| 轨 | 文件 | 说明 |
|---|---|---|
| 游戏参数(盲注/超时/dev 用户…) | `service/app/poker.env.example` | **它本身就是被加载的基线**(提交在 git 里),不是要你拷贝的模板。想本地改参数才需要:复制为 `service/app/poker.env`(gitignored)改值,它逐项覆盖 example |
| 基础设施(数据库地址) | `service/.env`(可选) | 缺省用本地 SQLite。要连 Postgres 才需要:建 `service/.env` 写一行 `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/poker`(或直接用环境变量,优先级更高) |

`poker.env` / `.env` 都不进 git;改了配置项记得同步对应 `*.example`(见 [service/docs/config.md](service/docs/config.md))。

## 5. 前端联调要知道的

- **明文 dev ws 端点**:`ws://<host>:8000/dev/ws?nick=<dev用户名>`——不用登录、不用加密,文本帧直接收发 JSON。前端 UI 开发全程用它即可。
- **加密路**(登录 + `/ws?sid=` + 需身份的 REST):dev 用户可真登录,共享口令和密钥在 `service/app/poker.env.example` 的 `DEV_PASSWORD` / `DEV_KUSER`(仅开发值)。
- 协议与加密的完整说明:[frontend/BACKEND_GUIDE.md](frontend/BACKEND_GUIDE.md);国密实现的已知答案测试向量:[frontend/crypto-test-vectors.json](frontend/crypto-test-vectors.json)。

## 6. 真部署(内网生产)与 dev 的三个不同

1. **建库走迁移不走 create_all**:`DATABASE_URL=… .venv/bin/alembic upgrade head`(见 [service/docs/db-migrations.md](service/docs/db-migrations.md))。
2. **用户凭证用管理员 CLI 发放**,不用 dev 种子:`.venv/bin/python scripts/kuser_admin.py issue --name xxx`(生成随机口令 + K_user,带外发给用户;每周轮换见 [service/docs/dev.md](service/docs/dev.md)「K_user 管理」)。
3. **明文 `/dev/ws` 端点在前端切加密后应移除**(目前并存是为了联调)。

## 常用命令速查

```bash
cd service
.venv/bin/uvicorn app.shell.lifespan:app --host 0.0.0.0   # 起服务
.venv/bin/pytest -q                                        # 全量测试
.venv/bin/alembic upgrade head                             # 迁移(生产/保数据时)
.venv/bin/python scripts/kuser_admin.py list               # 密钥记账
.venv/bin/python scripts/gen_wire_ts.py                    # 重生成前端 ws 类型(改协议后)
.venv/bin/python scripts/gen_crypto_vectors.py             # 重生成加密测试向量(改加密层后)
```
