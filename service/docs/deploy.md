# 部署与运维手册(Ubuntu 24.04 + PostgreSQL)

## 一句话定位

从一台干净的 Ubuntu 24.04 到「前后端常驻运行、数据在 PostgreSQL、有备份有轮换有升级流程」的完整路径。**数据库层(建库/迁移/验收)的权威是 [db-migrations.md](db-migrations.md) §0,本篇不复述、只按编号引用**;日常开发环境见 [dev.md](dev.md),零配置快跑见 [QUICKSTART](../QUICKSTART.md)。

先把**适用范围**再说一遍(出自 [architecture.md](architecture.md),部署决策全部以此为前提):

- **内网自用**,在线 ≤ 20,房间极少。**无 TLS 是设计而不是疏漏**:密码与全部流量走 SM4 + HMAC-SM3 自建信道([auth.md](auth.md)),唯一明文入口是 `POST /user/login`(其 body 本身是密文 blob)。**不要把 8000 端口暴露到公网**——威胁模型是内网。
- **单进程、不可水平扩展**;崩溃/重启丢进行中的手牌与未 flush 的积分变更(积分不是货币,设计接受)。
- **筹码是积分**。所有「最终一致 / 崩溃窗口」的取舍都建立在这一条上。

---

## §1 系统准备(Ubuntu 24.04)

24.04 自带 Python 3.12,正好是本仓要求的版本;其余照装:

```bash
sudo apt update
sudo apt install -y git python3.12-venv pipx postgresql-16 libpq5
pipx install poetry            # 或官方 curl 安装法,见 README
pipx ensurepath && exec $SHELL

# Node(只为构建/运行前端与冒烟脚本;后端不需要 node)
# 本仓开发机用的是 Node 24 装在 ~/.local/node;NodeSource / nvm / 手动解包均可,>= 20 即可
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
```

> **`libpq5` 那行别省**:pyproject 锁的是纯 Python 版 psycopg3,需要系统 libpq,缺了 `import psycopg` 直接失败。细节与替代方案(`psycopg[binary]`)见 [db-migrations.md](db-migrations.md) **§0.1**——那一步有自检命令,过了才继续。

```bash
git clone <repo-url> poker_platform && cd poker_platform
```

## §2 数据库(全文照 db-migrations §0 做,这里只列顺序)

1. **§0.1** 驱动自检(`import psycopg` 要过)。
2. **§0.2** 建角色与库(**迁移账号必须是库 owner**,pg 15+ 的 `public` schema 权限坑写在那节)。
3. **§0.3** `service/.env` 写一行 `DATABASE_URL=postgresql+psycopg://poker:…@localhost:5432/poker`。**读完那节的「静默回落 sqlite」警告再动手**——URL 写错不报错,只是所有数据悄悄进了本地 sqlite 文件。
4. **§0.4** 先 `alembic upgrade head` 再起服务,**顺序是硬的**(反了库就废,救法也写在那节)。
5. **§0.5** 验收四连(`current` / `check` / `\dt` 六张表 / 首行日志 `PostgresqlImpl`)。

## §3 后端部署

```bash
cd service
poetry config virtualenvs.in-project true
poetry env use python3.12
poetry install
# §2 的迁移做完之后:
.venv/bin/uvicorn app.shell.lifespan:app --host 127.0.0.1 --port 8000   # 先手动起一次验活,再转 systemd(§5)
```

### 3.1 生产 `poker.env`(游戏与运维参数)

`service/app/poker.env.example` 是**提交在 git 里的加载基线**(dev 真值),生产覆盖写进 `service/app/poker.env`(gitignored,只写要改的键)。每个键的含义在 example 文件里逐行注释,这里只点**部署必改**的:

```bash
# service/app/poker.env —— 生产覆盖(示例)
LOG_FORMAT=json          # 结构化日志,配 jq / 采集;dev 默认 console
LOG_LEVEL=INFO
LOG_FILE=                # 留空 = 只写 stderr → systemd journal 接管(推荐,免 logrotate)
DEV_PASSWORD=<换成强随机>   # ★ 见 3.2,首次启动前必改
DEV_KUSER=<32 位 hex 强随机> # ★ 同上;openssl rand -hex 16
```

其余(超时/盲注上下限/限速/flush 周期/会话 TTL/轮换周期)按运营口味调,不改也能跑——example 里的就是 canonical 值。

### 3.2 dev 种子账号:生产上**关不掉**,先把密钥换掉再首跑

启动会**无条件**执行 `seed_dev_users()`:按 `DEV_USERS` 把 10 个账号种进 DB,口令 = `DEV_PASSWORD`、密钥 = `DEV_KUSER`。三件事必须知道:

1. **`poker.env.example` 里的 dev 口令与密钥是提交在 git 里的公开值**。生产 `poker.env` 不覆盖它们就首跑,等于库里躺 10 个人人可登录的账号。**首跑之前**按 3.1 换成强随机值。
2. **删了也会回来**:种子是幂等 INSERT(按 id 1–10 查不到就插),[db-migrations.md](db-migrations.md) §0.6 的 `DELETE` 在**下一次重启时会被原样种回**(用当时 `poker.env` 里的口令/密钥)。所以现实的姿势是「换强密钥后留着」,而不是「删干净」——真正关掉种子需要代码改造(加开关),在未竟清单上([changes/0110](refactor/changes/0110-the-unfinished-ledger-and-a-deploy-manual.md))。
3. **§0.6 的 `setval` 序列推进无论如何都要做**(种子用显式 id,不推进 pg 序列;不做的话 `issue` 第一个正式账号就撞主键)。删不删账号都要做这一步。

### 3.3 正式账号与 K_user 运维

正式用户不走种子,走管理员 CLI(直连 `DATABASE_URL` 指向的库):

```bash
cd service
.venv/bin/python scripts/kuser_admin.py issue --name <账号> [--nickname 昵称] [--points 初始积分]
.venv/bin/python scripts/kuser_admin.py list      # 看版本与到期排程,不打印密钥
.venv/bin/python scripts/kuser_admin.py rotate    # 轮换所有到期账号(幂等)
```

- 新钥/新口令只打到 stdout,**带外私发**给用户;不落会进 git 或被日志采集的文件。为什么不能经信道自动下发,见 [auth.md](auth.md)「别用信道自动下发」。
- **每周轮换挂 cron**(幂等,只动到期账号;dev 种子钥 `k_cur_until=NULL` 不受轮换影响):

```cron
0 3 * * 0  cd /opt/poker_platform/service && .venv/bin/python scripts/kuser_admin.py rotate >> /root/kuser-rotate.out 2>&1
# 输出文件 chmod 600,发完钥即清
```

- 忘跑不锁人:旧钥有 `KUSER_GRACE_DAYS` 宽限,宽限期内登录带 `rotate=true` 提示。
- **K_user 泄露的即时处置 = 重启服务**(会话表在内存,重启即全体下线;`issue --reset` 换钥是独立进程,够不到已建立的会话)——出处 [auth.md](auth.md) §吊销。

## §4 前端部署

```bash
cd frontend
npm ci                        # 按 package-lock 精确复现
NEXT_PUBLIC_API_URL=http://<后端可达地址>:8000 npm run build
npm run start                 # next start,默认 3000 端口;转 systemd 见 §5
```

两个必须知道的事实:

1. **`NEXT_PUBLIC_API_URL` 是构建期烤进产物的**(`next.config.js` 的 `env` 段),不是运行时读的。**换后端地址 = 重新 build**。ws 地址由它派生(`http→ws` 同源替换),不单独配。
2. **跨源要配对**:前端跑 3000、后端跑 8000 就是跨源,后端 `service/.env` 里 `CORS_ORIGINS` 必须列上前端的真实 origin(默认只放行 `localhost:3000` 与 `127.0.0.1:3000` 两种本机写法;换了机器名/IP 要加)。**同源反代则留空 CORS**,见下。

### 4.1 可选:nginx 同源反代(推荐形态)

把前端与后端收到同一个 origin,CORS 整个消失,用户只记一个地址:

```nginx
server {
    listen 80;
    server_name poker.internal;

    location /ws {                       # ws 要升级头,单列
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;        # 长连接;保活语义在应用层,别让 nginx 60 秒掐了
    }
    location ~ ^/(user|lobby|leaderboard|hands)($|/) {
        proxy_pass http://127.0.0.1:8000;   # REST(全部走加密信封,POST)
    }
    location / {
        proxy_pass http://127.0.0.1:3000;   # 其余给 Next
    }
}
```

此时前端以 `NEXT_PUBLIC_API_URL=http://poker.internal` 构建,后端 `CORS_ORIGINS=`(留空)。要上 TLS 也在这一层加(与 SM4 信道正交,`https→wss` 由同一行派生逻辑自动跟上)——但记住威胁模型是内网,TLS 不是本设计的依赖。

## §5 systemd 常驻

```ini
# /etc/systemd/system/poker-backend.service
[Unit]
Description=poker backend (uvicorn, single worker by design)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
User=poker
WorkingDirectory=/opt/poker_platform/service
ExecStart=/opt/poker_platform/service/.venv/bin/uvicorn app.shell.lifespan:app --host 127.0.0.1 --port 8000
Restart=on-failure
# 优雅关闭要给 drain 留时间(DB_DRAIN_TIMEOUT_MS + 余量):
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/poker-frontend.service
[Unit]
Description=poker frontend (next start)
After=network.target

[Service]
User=poker
WorkingDirectory=/opt/poker_platform/frontend
ExecStart=/usr/bin/npm run start
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now poker-backend poker-frontend
```

三条铁律:

1. **绝不 `--workers >1`,绝不起第二个实例**:内存权威 + 唯一 DB 写者,多 worker 互相覆盖状态与积分([db-migrations.md](db-migrations.md) §0.7)。
2. **重启必须「先停净再起」**:端口没让出来时新进程打一行 `[Errno 98] address already in use` 就退,**旧进程继续服务**,curl 照样 200——你以为重启了,其实在跑旧代码([dev.md](dev.md) 的房规)。systemd 的 stop→start 天然满足;手工操作就走「杀 pid → 确认进程没了且端口释放 → 起 → grep 日志无 errno 98」四步。
3. **重启 = 全体会话失效**(会话表在内存):用户要重新登录;进行中的手牌丢弃(设计接受)。挑没人打牌的时候重启。

## §6 日常运维

### 6.1 验活

```bash
systemctl status poker-backend poker-frontend
journalctl -u poker-backend -n 20      # 期望看到 "dev shell up: room=… users=…"
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/hands   # 期望 405
```

**405 就是活的**——0094 之后没有明文端点,一切 REST 走 `POST` + 加密信封,GET 被拒恰好证明服务在收请求。要做**真正的端到端**验活,用前端仓的冒烟(它带完整加密实现打真后端):

```bash
cd frontend && NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npm run smoke
```

> 冒烟用 `smoke1/2/3` 账号打真牌局、动真积分——在生产库上跑等于往正式数据里写手牌记录。生产上要么接受这点噪声,要么只用 405 验活。

### 6.2 日志与告警

`LOG_FORMAT=json` + `LOG_FILE=`(空)时日志进 journald,一行一条 JSON:

```bash
journalctl -u poker-backend -o cat | jq 'select(.level=="WARNING" or .level=="ERROR" or .level=="CRITICAL")'
```

**值得挂告警的是 CRITICAL**,它只在几种「进程还活着但已经坏了」的场景出现([log.md](log.md)):常驻协程非取消而死(watchdog,0083)、`inbox` 满、落库连续失败达 `DB_WRITE_MAX_RETRY`(毒丸丢批)、优雅关闭 drain 超时。**CRITICAL 的正确响应基本都是「重启 + 查日志」**。

### 6.3 备份与恢复

库里躺着的是:账号与鉴权列(`user`)、全局积分(`user.points`)、手牌历史(`handrecord`/`handparticipant`)、私信(`dmmessage`/`dmreadcursor`)。**不在库里的**:进行中的手牌、桌上筹码、会话——全是内存态,备份天然不含、恢复也不需要。

```cron
30 2 * * *  pg_dump -Fc -f /var/backups/poker/poker-$(date +\%F).dump poker && find /var/backups/poker -mtime +30 -delete
```

恢复:`pg_restore -d poker --clean --if-exists poker-<date>.dump`,起服务即可。**恢复点之后、崩溃之前的积分变更丢失窗口 ≤ `DB_FLUSH_INTERVAL_MS`+备份间隔**——积分不是货币,设计接受;要缩小就缩 flush 周期与备份间隔。

**任何 `alembic upgrade/downgrade` 之前先 `pg_dump`**([db-migrations.md](db-migrations.md) §2 的规矩)。

### 6.4 升级(拉新代码)

```bash
cd /opt/poker_platform && git pull
cd service && poetry install                    # lock 变了才有动作
pg_dump -Fc -f ~/pre-upgrade.dump poker         # 有新迁移时必做
.venv/bin/alembic upgrade head                  # 没新迁移时是 no-op
cd ../frontend && npm ci && NEXT_PUBLIC_API_URL=<同 §4> npm run build
sudo systemctl restart poker-backend poker-frontend
journalctl -u poker-backend -n 5                # 确认起来了、没有 errno 98
```

回滚 = `git checkout <旧提交>` + 逆序做以上;**库要回退先读 [db-migrations.md](db-migrations.md) §5 的数据后果表**——downgrade 会丢列/丢表里的数据,多数时候「代码回退、库不回退」更安全(旧代码遇到多出来的列不受影响)。

## §7 迁移

### 7.1 从这台开发机的 sqlite 把数据搬进 pg(可选)

开发机 `service/poker.db` 里只有 dev 账号的积分与联调手牌/私信,**多数情况下不值得搬**——生产从空库 + `issue` 正式账号开始最干净。真要搬(比如想留手牌历史):

1. pg 侧先走完 §2(空库、迁到 head)。
2. 逐表复制,**顺序按外键**:`user` → `handrecord` → `handparticipant` → `dmmessage` → `dmreadcursor`。最省事的通用做法:

```bash
# sqlite 导 CSV(每表一次)
sqlite3 service/poker.db -header -csv "SELECT * FROM user;" > /tmp/user.csv
# pg 侧 \copy 进(每表一次;列名以 CSV 头为准)
psql -d poker -c "\copy \"user\" FROM '/tmp/user.csv' CSV HEADER"
```

3. **时间戳列注意**:sqlite 存的是 naive 墙钟数字、pg 列是 `timestamptz`,`\copy` 会按服务器时区解读。本仓写入时全部用 UTC(见 [db.md](db.md)/0098),所以导入前 `SET timezone='UTC';` 或在 psql 会话里 `ALTER DATABASE poker SET timezone='UTC';`,否则游标/清理的时间语义会整体偏移。
4. **最后必做序列推进**(§0.6 ②):`SELECT setval('user_id_seq', (SELECT max(id) FROM "user"));`
5. 验收:行数逐表对得上;起服务后登录一个老账号、`POST /leaderboard` 里积分正确。

### 7.2 设备到设备(pg → pg)

要带走的东西一共五样:

| 东西 | 怎么搬 |
|---|---|
| 数据库 | `pg_dump -Fc` → 新机 `pg_restore`(版本 ≥ 14 即可,16 实测) |
| `service/.env` | 一行 `DATABASE_URL`,照新机的库改 |
| `service/app/poker.env` | 生产覆盖值(**含 `DEV_PASSWORD`/`DEV_KUSER` 强密钥**——丢了它,重启后种子会拿 example 的公开值补种) |
| K_user 台账 | 密钥本体在库里(`k_cur` 列),随 dump 走;管理员的带外发钥记录自行保管 |
| 代码 | `git clone` + 本篇 §1–§5 重走(venv/node_modules 不搬,重装) |

**不用搬也搬不了的**:会话(内存,重启即无)、进行中的牌局(设计丢弃)、`poker.db`(那是 dev sqlite,pg 部署用不到)。切换时序:老机停服务(优雅关闭会把写缓冲 drain 进库)→ dump → 新机 restore → 起服务 → 前端把 `NEXT_PUBLIC_API_URL` 指向新机重 build(或反代不动、只切上游)。

## §8 边界与已知事项(部署视角)

- **单进程无高可用**;进程死 = 服务死,systemd `Restart=on-failure` 是唯一兜底。重启丢进行中手牌、全体重登。
- **无 TLS**(内网设计);要出内网先在反代层加 TLS,并重新评估威胁模型(auth.md 的模型不防公网)。
- **dev 种子关不掉**(§3.2),生产靠换强密钥缓解;真开关在未竟清单上。
- **仓库无 CI**:提交规约靠人执行([dev.md](dev.md)),部署前手跑 `pytest` + 前端套件是唯一守门。
- 其余未竟事项(缺陷、覆盖缺口、待用户定案的问题)集中在 [changes/0110](refactor/changes/0110-the-unfinished-ledger-and-a-deploy-manual.md) 的总录;**活清单仍以 [refactor/TODO.md](refactor/TODO.md) 与 [refactor/BUGS.md](refactor/BUGS.md) 为准**。
