# 0059 · P5 登录端点(POST /user/login,K_user 加密下发会话)

日期:2026-07-01 · 范围:`app/rest/login.py`(新,`LoginRequest`/`LoginResponse`/`make_login_router`)、`app/shell/lifespan.py`(DevShell 加 `SessionStore` + 挂 login 路由)、`tests/rest/test_login.py`(新)、`docs/auth.md`(§登录握手 端点落地)、`docs/refactor/TODO.md`。串起 0055(会话表)/0056(authenticate+查询)成登录闭环。

## 背景 / 为什么

auth.md §登录握手:`/user/login` 收 `{name, iv, blob=SM4(K_user,iv,{password,client_nonce})}` → 按 name 取 K_user 解 blob 验密码 → 铸会话 → **响应用 K_user 加密下发 `{session_id, session_token, exp}`**(token 只此出现一次、被 K_user 护住)。0055/0056 已把会话表 + authenticate + 查询备齐,本砖落端点把它们串起来。**无 JWT**(0057 定案:身份从会话密钥解密得出)。

## 关键设计决策

1. **端点走 REST 路由工厂**(同 `make_lobby_router`/`make_leaderboard_router`):`make_login_router(get_sessionmaker, session_store, now=time.time)`,`create_app` 挂 `make_login_router(lambda: shell.sessionmaker, shell.session_store)`。`now` 可注入(测试确定 exp)。`SessionStore` 成 DevShell 单例(`__init__` 建,`SESSION_TTL_SECONDS`)。
2. **fail-closed 统一 401**:未知账号 / `name=NULL` 老行 / 未启用 / 密码错 / blob 坏 / iv·blob 非 hex —— **一律 `401「login failed」`,不泄具体原因**(不区分「无此账号」与「密码错」,减少枚举/侧信道)。**时序侧信道**(未知账号比密码错返回快)本规模接受(内网、DoS 不在范围),见待办。
3. **响应 K_user 加密**:`{session_id, session_token(hex), exp}` → `SM4(K_user, iv2, …)`;`session_token` 32B 只在此加密下发一次、绝不明文上线(auth.md 铁律)。`k_user` 取自 `load_user_for_login`,authenticate 已验其为合法 16B hex,故响应加密处 `bytes.fromhex` 安全。
4. **请求/响应体 hex**:`iv`/`blob` 用 hex 串(JSON 友好,免二进制体);登录端点本身是 HTTP JSON(登录前无会话密钥,故此端点不套 0058 会话信封——它是**引导**这条信道的入口,auth.md「登录握手 HTTP」)。
5. **client_nonce 重放守卫本砖不做**(authenticate 已透出 `proof.client_nonce` 备用):登录重放**低危**——攻击者无 K_user 解不了响应、只能凭空造会话(自己也读不到);会话表增长属 DoS(不在威胁模型)。且严谨的重放窗需 blob 内带时间戳(auth.md blob 现只 `{password, client_nonce}`),属设计细化,留待办。

## 打算改什么

- `app/rest/login.py`(新):`LoginRequest{name, iv, blob}` / `LoginResponse{iv, blob}` / `make_login_router`:hex 解 iv·blob → `load_user_for_login(name)` → `authenticate` → `SessionStore.create` → K_user 加密响应。全程 fail-closed 401。
- `app/shell/lifespan.py`:`DevShell.__init__` 加 `self.session_store = SessionStore(gameconfig.SESSION_TTL_SECONDS)`;`create_app` 挂 `make_login_router(...)`。
- `tests/rest/test_login.py`(新,async + StaticPool 内存库,同 test_login_query):正路(响应 K_user 解密得 session_id/token/exp、会话登记 token 一致 name/nickname)、错密码 401 无会话、未知账号 401、legacy(name=NULL)401、错 K_user blob 401、坏 iv hex 401、create_app 挂路由。
- docs:auth.md §登录握手 标端点落地;TODO 登录握手项去「端点」余。

## 自 review

对照 review.md 逐维 + **对抗式多智能体复审(2 lens finder × 反驳验证者)**:**1 confirmed(已修)+ 1 refuted**。

- **① 分层 / 不变量**:`login.py` 是 shell HTTP 端点(可 import auth/db,不违分层——core 不 import 这些);不碰 world/reduce;`SessionStore` 是 shell 单例。
- **② 代码↔文档同步**:auth.md §登录握手 标端点落地(签名/流程)、TODO 计数 562;与代码一致。
- **③ 文档↔文档一致**:0059 ↔ auth.md ↔ TODO ↔ 0055/0056/0057 一致;链解析。
- **④ 数据模型**:`LoginRequest{name,iv,blob}`/`LoginResponse{iv,blob}` 字段注释齐;hex 串传输。
- **⑤ 规范合规**:具名常量 `_RESP_IV_BYTES`;中文注释讲「为什么」(响应 K_user 加密 / fail-closed / DB 错归 401);无死代码;**脱敏红线**:session_token 只在 K_user 加密 blob 内下发、不打日志;DB 错日志无密码/密钥(load 查询不含)。
- **⑥ 测试充分**:8 测——正路(响应 K_user 解密得 session_id/token/exp、会话登记 token/name/nickname 一致)/ 错密码 401 无会话 / 未知账号 401 / legacy NULL-name 401 / 错 K_user blob 401 / 坏 iv hex 401 / **DB 错归 401**(自 review 补)/ create_app 挂路由。
- **⑦ 流程账本**:打算↔实际一致;TODO 更新;提交引用 0059。

**confirmed(major,已修)**:`load_user_for_login` DB 调用原未 try/except → DB 故障(连接/超时)会冒成 **500** 而非统一 401,破 fail-closed 且泄「基础设施错 vs 认证失败」之别。**修**:包 try/except → `log.exception` 记真因(供运维;查询无密码/密钥,不触红线)+ 对外统一 `401「login failed」`;补测 `test_db_error_returns_uniform_401`(注入抛异常的 sessionmaker → 401、不铸会话)。DB 错系统级(非按账号)⇒ 归 401 不泄账号级信息。

**refuted(误报)**:复审称「测数应为 523」——**实测 `pytest --co` = 562 collected / 562 passed**(0058 后 554 + 7 登录 + 1 自 review 补 DB 测 = 562)。finder 的 516 基数错(实为 554),驳回。

**对抗核实**:逐路径确认——**所有失败**(坏 hex / 未知账号 / NULL name / 未启用 / 密码错 / blob 坏 / DB 错)**一律 401「login failed」**、无 200/500/崩;`session_token` 仅在 K_user 加密 blob 内、绝不明文/日志;会话仅在 `proof!=None` 后铸;`bytes.fromhex(user.k_user)` 在 authenticate 已验 16B hex 后安全。0 残留真 bug。

## 待办 / 下一步

- **client_nonce/exp 重放守卫**:blob 内带时间戳 + 服务器短窗去重(设计细化)。
- **dev 种子带 name/密码/K_user**:让 DEV_USERS 可真登录(现 dev 种子只 nickname/points → 未启用登录;端点单测用自种 login-enabled 用户)。
- **接线砖**:Receiver 剥 selector → SessionStore 查 → `SecureChannel.open`;Sender `seal`;REST 信封中间件。
