# 0069 · 根目录 QUICKSTART + 国密已知答案测试向量(给前端)

日期:2026-07-13 · 范围:`QUICKSTART.md`(新,仓库根)、`service/scripts/gen_crypto_vectors.py`(新)、`frontend/crypto-test-vectors.json`(新,生成产物)、`service/tests/crypto/test_vectors_uptodate.py`(新,漂移守门)、`frontend/BACKEND_GUIDE.md`(§4.1/§7 互链)。用户三问引出:① dev 明文端点是否真实现/有记录(核实:`lifespan.py:210` 真,connection.md/dev.md/指南 §9 三处有记);② 换机器 pull 后怎么跑,env 要不要补(要一份教程 + 自动化/优雅做法由我定);③ 要不要加密兼容性测试接口(尤其填充)。

## 关键决策

1. **「pull 即跑」不需要任何 bootstrap 脚本或 env 拷贝——这是 0042/0045 配置设计的既有红利,教程照实写**:`poker.env.example` 是提交进 git 的**实际加载基线**(不是要拷贝的模板),`DATABASE_URL` 有 sqlite 安全默认,dev shell 启动自动 `create_all` 建表 + 种 dev 用户。所以首跑 = `poetry install` + `uvicorn`,零配置文件手工步骤;脚本包三条命令反而藏错误。教程重点放在**别人容易卡住的地方**:pull 后 dev 库结构变了要删 `poker.db`(create_all 不做迁移)、Poetry 首装、验证三连、两轨配置想改时怎么改。
2. **加密兼容性不做测试接口,做「离线已知答案向量」**:静态 `frontend/crypto-test-vectors.json` 由 `gen_crypto_vectors.py` 从后端同一套原语(ttxsgm + channel.py)确定性生成——前端拿它写单测,逐字节对上再连真服务器;端到端兼容性天然由「用 DEV_KUSER 真登录 dev 服务器」验证(登录一来一回就把 SM4/JSON/hex/填充全走了),无需新端点(新端点是要退役的攻击面/维护面)。**填充坑显式覆盖**:SM4 用例含长度 0/15/16/17/33(16 的整数倍也要再补一整块,正是用户点名的坑);HMAC 用例含超块长 key(触发 SM3 收缩);四个 KDF 域(0x01–0x04)、ws 帧(seq=1)与 REST 信封、登录 blob 全套成帧示例。
3. **向量文件是生成产物,漂移守门骑 pytest**(同 `wire.gen.ts` 的治理):改了原语/信封格式不重生成 → `test_vectors_uptodate.py` 红;`--check` 供 pre-commit。生成器**零随机**(固定 key/iv/seq/明文),可复现。

## 实际改了什么

与「关键决策」一致落地:

- `QUICKSTART.md`(仓库根):首跑两步(poetry install + uvicorn,零配置)→ 验证三连(curl 两端点 + 浏览器 console 连明文 ws)→ **每次 pull 之后**表(lock 变了 install / 结构变了删 `poker.db` 或 alembic / wire.gen.ts 免动)→ 两轨配置何时才需要动 → 前端联调要点 → 生产与 dev 三不同 → 命令速查。
- `scripts/gen_crypto_vectors.py` + `frontend/crypto-test-vectors.json`:六节向量(sm3 含国标 "abc" 已知答案 `66c7f0f4…`,生成值吻合 = ttxsgm 是标准 SM3 的旁证;sm4_cbc 长度 0/15/16/17/33;hmac 含 64B/100B key;kdf 四域;ws 帧 seq=1;REST 信封;登录 blob 请求+响应双向),`--check` 模式同 gen_wire_ts。
- `tests/crypto/test_vectors_uptodate.py` 4 测:字节漂移守门 + PKCS#7 整块补块形制 + KDF 域与 channel.py 派生互证 + **向量帧过服务器真 `open_envelope` 往返**(杀「生成器成帧自立门户」)。688→**692** 全绿。
- `frontend/BACKEND_GUIDE.md`:§4.1 指向向量文件(先跑绿向量再连真服务器的顺序建议)、§7 指向 QUICKSTART。
- **`.gitignore` 修理(落地时撞出)**:原型期遗留的一揽子 `*.json` 把向量文件静默挡在 git 外(add 直接被拒)——移除之,换定向 `.claude/settings.local.json`(`.vscode/` 本有规则;删规则只会暴露这两个本地配置,已核);顺删「`.gitignore` 忽略自己」的迷惑行(文件已被追踪,该行是无效项,但若日后 untrack 会真丢忽略规则)。留注释防回潮。

## 自 review

- **① 分层**:生成器只 import ttxsgm + `app.auth.channel`(shell 侧),无越层;向量文件是 frontend 侧产物(同 wire.gen.ts 治理)。
- **② 代码↔文档**:QUICKSTART 每条命令/路径对照仓库现状核过(poetry 流程对 service/README、`--host 0.0.0.0`、dev 用户名单对 poker.env.example、删 poker.db 的条件对 engine.create_all 行为);dev 端点「真实现 + 三处文档记录」经 grep 核实(lifespan.py:210 / connection.md / dev.md / 指南 §9)。
- **③ 文档↔文档**:QUICKSTART ↔ BACKEND_GUIDE ↔ dev.md/db-migrations.md 互链无环矛盾;QUICKSTART 不复述协议(链走)。
- **⑤ 规范**:生成器零随机(固定素材具名常量)、样例密钥显式注明「非真实密钥」;向量文件头 `_readme` 自述用途与字节约定。
- **⑥ 测试**:4 新测中「真 `open_envelope` 往返」是关键——向量若与服务器成帧规则漂移必红;`--check` 进 pytest 覆盖(同 codegen)。
- **⑦ 账本**:本记录;TODO 无涉(教程/向量是持续项「协议增量交付」的配套,不新开砖)。
- **对抗自问**:「向量会不会本身生成错了还守门成'正确'?」——已用两道独立锚破解:国标 "abc" 已知答案(外部锚)+ 服务器 `open_envelope` 往返(实现锚);两锚都过,向量与后端与国标三方一致。0 未处置发现。

## 问答补记(2026-07-14)

**问**:QUICKSTART 写"缺省 SQLite、要连 Postgres 才需要建 .env"——我的设计是 pgsql,设计被改了吗?——**答:没有**。生产目标库一直是 PostgreSQL(architecture.md 技术栈固定,psycopg3 是一等依赖);SQLite 缺省是 **0045** 定的**开发便利**("安全 dev 默认",零配置可跑测试/快速联调),不是设计变更。问题在本篇初版把叙述重心放错:sqlite 写成主路径、pg 写成附注,读起来像换了库。→ **QUICKSTART 修正**:§1 补"数据库定位别误会"注;新增 §2「用 PostgreSQL 跑」一等章节(建库 → `.env` → **先** `alembic upgrade head` **再**起服务[空 pg 库先起服务会让 create_all 建出无迁移历史的表,与 Alembic 混用撞表]→ uvicorn;URL 同步/异步双栖);§4 pull 后表补 pg 行(alembic 是正道,"删库重来"仅限本地 sqlite);§5 基础设施行改口径。另:用户已将 QUICKSTART.md 移入 `service/`(根目录 → service/),相对链接随新位置全部修正,BACKEND_GUIDE §7 的回链同步。
