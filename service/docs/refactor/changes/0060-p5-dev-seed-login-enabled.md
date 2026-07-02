# 0060 · P5 dev 种子 login-enabled(DEV_USERS 可真登录)

日期:2026-07-02 · 范围:`app/gameconfig.py`(加 `DEV_PASSWORD`/`DEV_KUSER`)、`app/poker.env.example`、`tests/test_gameconfig.py`、`app/shell/lifespan.py`(`seed_dev_users` 写鉴权列 + 缓存 dev 哈希)、`tests/rest/test_login.py`(补 dev 端到端登录)、`docs/dev.md`/`docs/auth.md`/`docs/refactor/TODO.md`。让 [0059](0059-p5-login-endpoint.md) 的 `/user/login` 在 dev 真跑通。

## 背景 / 为什么

0059 落地登录端点,但 `seed_dev_users` 只写 `nickname`/`points`,dev 用户的 `name`/`hash_password`/`k_user` 均 NULL → **未启用登录**,`/user/login` 对 dev 用户一律 401。本砖给 dev 用户补齐鉴权列,使 DEV_USERS 能用 dev 口令 + dev K_user 真登录(curl / 前端 / 端到端测)。**纯 dev 脚手架**:dev 口令/K_user 是明示的 dev 值(非生产密钥),生产走管理员带外发放的每用户 K_user + 各自密码。

## 关键设计决策

1. **dev 共享口令 + 共享 K_user**(dev-only 简化):`DEV_PASSWORD`(所有 dev 用户同口令)+ `DEV_KUSER`(所有 dev 用户同 SM4 密钥,32 hex=16B)。auth.md 生产原则是**每用户一把** K_user(挡内部互解),此处 dev 明示放宽(dev 客户端要知道 K_user 才能加密登录 blob;全局一把最省事、够 dev)。均进 gameconfig dev 段 + poker.env.example,`DEV_KUSER` 用 `pattern` 校验 16B hex。
2. **`name = nickname`**(dev 简化):dev 登录账号 = 昵称(DEV_USERS 短、唯一、≤15,满足 `User.name` 约束)。生产 name/nickname 分离(auth.md),dev 合一够用。
3. **dev 口令哈希缓存**(测试提速):`hash_password(DEV_PASSWORD, PWD_HASH_ROUNDS)` 在 100k 轮下 ≈0.16s;`seed_dev_users` 每 setup 调一次、多个 dev 测各 setup → 累积。**用 `lru_cache` 缓存**(进程内算一次,所有 dev 用户/所有 setup 复用同一 `salt$rounds$digest`——dev 共享口令下共享哈希无害)。
4. **幂等 + 回填**:`seed_dev_users` 既 INSERT 新 dev 用户(带鉴权列),也**回填** pre-P5 已存在但 `name=NULL` 的 dev 行(补 name/hash/k_user、**不重置 points/nickname**,承接 OrmPersister 落库的积分)。仍不重置已启用行(name 非 NULL 即跳过)。

## 打算改什么

- `app/gameconfig.py`:dev 段加 `DEV_PASSWORD: str = Field(min_length=1)`、`DEV_KUSER: str = Field(pattern=r"^[0-9a-f]{32}$")`。
- `app/poker.env.example`:`DEV_PASSWORD=devpass123`、`DEV_KUSER=<32 hex>`(dev 段,标 dev-only)。
- `tests/test_gameconfig.py`:`_valid_kwargs` 补两字段 + `DEV_KUSER` 非法 hex 拒。
- `app/shell/lifespan.py`:`seed_dev_users` 写 `name`/`hash_password`(缓存)/`k_user`;新增 `_dev_password_hash()`(lru_cache)。
- `tests/rest/test_login.py`:补 `test_dev_seeded_user_can_login`(seed → 用 DEV_PASSWORD+DEV_KUSER 登录 DEV_USERS[0] → 响应解密得会话)。
- docs:dev.md(dev 用户现可登录 + dev 口令/K_user 位置)、auth.md(dev 种子 login-enabled 注)、TODO。

## 自 review

对照 review.md 逐维(本砖 = **dev 脚手架、dev-only 低危** → 人工对抗式复审,review.md 允许;非跑 workflow)。**0 真 bug**,自查补 1 测(回填路径):

- **① 分层 / 不变量**:`seed_dev_users`/`_dev_password_hash` 是 shell(lifespan),读 gameconfig、写 DB;不碰 world/reduce/core。`lru_cache` 是进程级 shell 缓存。
- **② 代码↔文档同步**:gameconfig `DEV_PASSWORD`/`DEV_KUSER` ↔ env ↔ auth.md/dev.md/TODO 注一致;seed 的 INSERT+回填行为与 changes/0060 述一致。
- **③ 文档↔文档一致**:0060 ↔ auth.md ↔ dev.md ↔ TODO 一致;计数 565;链解析。
- **④ 数据模型**:`DEV_KUSER` `pattern=^[0-9a-f]{32}$`(16B hex);`name`=昵称(DEV_USERS ≤15、唯一,满足 `User.name` unique/≤15);回填只写鉴权列、保 points/nickname。
- **⑤ 规范合规**:配置具名 + 注释讲「为什么」(dev-only 放宽 / 共享哈希无害 / lru_cache 提速);删了不再用的 `select` import(无死码);中文注释。**脱敏**:DEV_PASSWORD/DEV_KUSER 是 dev 明示值(非生产密钥),不打日志。
- **⑥ 测试充分**:DEV_KUSER 非法 hex 拒 + dev 端到端登录(seed→login→解密会话)+ **回填**(pre-P5 NULL-name 行→补鉴权列、不重置 points)。
- **⑦ 流程账本**:打算↔实际一致;TODO 更新 565;提交引用 0060。

**对抗核实**:①「回填会重置 dev 积分?」→ elif 分支只写 name/hash/k_user,points/nickname 不动(测断言 points=777 存活),驳回。②「lru_cache 缓存跨 setup 复用同一 salt$hash 有问题?」→ dev 用户共享同一口令,共享哈希功能等价、且仍加盐(一把随机盐/进程),dev 可接受,非 bug。③「共享 DEV_KUSER 破 auth.md 每用户一把?」→ dev-only 明示放宽(已在 config/docs 标注),生产走每用户带外,非缺陷。

## 待办 / 下一步

- **Receiver/Sender 信道接线**(大件):ws `?sid=` → SessionStore 查 → `SecureChannel` → 收发帧走 `open`/`seal`(替 dev `?nick=` 明文)。
- **client_nonce 重放守卫** / **REST 信封中间件** / **K_user 每周轮换**。
