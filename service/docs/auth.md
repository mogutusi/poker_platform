# 鉴权与 WS 安全信道(auth 模块)

## 定位 & 现状两个洞

身份认证 + 在**无 TLS(纯 ws,不是 wss)**的前提下保护传输。要堵两个现存洞:

1. **ws 端点零鉴权**:原型 `pokertable/routes.py`(已于 0027 拆除)的 `/pokertable/room?user_nickname=Y` 把昵称当**明文 query 参数**直收——任何人可冒充任何人。本篇国密信道即为根治此洞。
2. **全程明文**:没有 wss,登录的账号密码、游戏消息在网络上裸奔,可被嗅探。

对策:**应用层用国密三件套自建一条安全信道**(SM2 不用;用手输的共享密钥引导)——登录用 `K_user` 对称加密把账号密码护住、换回**会话密钥**;**登录后每条消息(ws 与 REST 都算)按 `selector‖iv‖ct‖mac` 信封,用会话密钥 SM4 加密 + HMAC-SM3 完整 + 序号防重放,解密即认证**(见 [changes/0057](refactor/changes/0057-p5-unified-encrypted-channel-design.md))。

> 用的库:[ttxsgm](../../lib/ttxsgm/ttxsgm/__init__.py) 的 `sm4_cbc_enc/dec`、`sm3_hash`/`sm3_hash_bytes`/`KDF_sm3`。

## 威胁模型(决定了"够用"的边界)

- **环境**:内网、≤20 个**可控**用户、积分非货币。
- **要防**:嗅探读消息、冒充他人/篡改、重放、密码被嗅、ws 裸昵称冒充。
- **不防(本规模接受)**:国家级攻击;TCP RST 切连(DoS,wss 也防不住);拿到密钥的内部人;**完整**前向保密(定期轮换只给到"窗口级",见残余风险)。

## 身份模型:name vs nickname

| 字段 | 是什么 | 用在 |
|---|---|---|
| **`name`** | 登录账号(唯一,≤15),**不可变** | 登录、定位用户、选每用户密钥 |
| **`nickname`** | 游戏昵称(唯一,≤50),**可改**(change_nickname) | `world.users` 的键、座位、所有牌局逻辑 |

认证链:**手输密钥 + 账号密码 → 认证出 `name` → 查 DB 得 `nickname` → 握手后投 `Connect(nick)` 接入大厅**(连接绑 nick、不绑房间,模型 2)。进房与载入积分发生在之后的 `JoinRoom`(见 [lobby.md](lobby.md))。**登录定位/选密钥用不可变 `name`,不用可变 `nickname`。**

## 密钥层级(引导密钥 + 会话密钥)

**不分 REST/WS 两套凭证——登录后一切流量走同一把会话密钥**(见 [changes/0057](refactor/changes/0057-p5-unified-encrypted-channel-design.md));**不用 JWT**(身份从解密得出)。

| 层 | 凭证 | 干嘛 |
|---|---|---|
| **引导** | `K_user`(每用户手输 SM4 密钥,每周轮换) | 只在登录时护住账号+密码、换回会话密钥;是引导密钥,不是会话凭证 |
| **会话** | `session_token`(32B 秘密,带 `exp`)+ 公开 `session_id` | 登录下发;派生 enc/mac 密钥,**登录后 ws 与 REST 的每条消息都用它加密+认证** |

- **`K_user`**:带外发放、手输、不进前端;可轮换(每周,见下)。
- **`session_token`**:登录生成、存内存会话表(带 `exp`)、配对公开 `session_id`;`session_id` 即报文 `selector`(公开句柄,服务器据此查会话取密钥+身份)。**解密成功即认证,无需独立身份令牌。**

**会话过期与密钥轮换**(仅指 ws 的 `session_token`):每次登录生成新 `session_token` → 派生出新的一对 enc/mac 密钥,**一次登录 = 一把密钥**。为缩小密钥泄露的影响面,密钥要**定期轮换**,且对用户**无感**:

- **轮换 = 无感重连**:客户端在 `SESSION_TTL` 到期前,用本地缓存的 `K_user` **静默重跑登录握手**拿新 `session_token` → 派生新密钥 → 新连接**顶替**旧连接(见 [connection.md](connection.md)) → reduce 私发 `StateSnapshot` 对齐。整个过程用户察觉不到。
- **服务器 exp 兜底**:`session_token.exp` 到点,服务器**拒绝该会话**(该会话密钥的报文一律验不过 / 拒服务);正常情况下客户端已提前轮换,不会撞到。
- **真正掉线才需重登**:只要客户端进程还在、`K_user` 还在内存,轮换与重连都静默;若客户端彻底关闭(`K_user` 丢失)或主动登出,下次必须**用户重新手输 `K_user` 登录**。
- **`K_user` 即长期 refresh 凭证**,WS 不另设 refresh token(双重 token 在此冗余)。
- **不上中途 rekey / 完整前向保密**:靠"定期无感重连换钥"已够;在单条连接里搞棘轮换钥属过度工程,本规模不做。

> 轮换周期由 `SESSION_TTL_SECONDS` 定(见配置);客户端应在到期前留余量主动轮换。

## 密码存储:SM3 + 每用户盐 + 迭代

现状是裸 `sm3_hash(password)`(无盐、单轮),DB 泄露后可彩虹表/撞库。改为**每用户随机盐 + N 轮 SM3**,存 `salt$rounds$digest`(全 hex)。

**原语已落地** [`app/auth/passwords.py`](../app/auth/passwords.py)([changes/0053](refactor/changes/0053-p5-password-hashing.md)),纯函数、精确签名以代码为准,要点:

- `hash_password(password, rounds) -> "salt$rounds$digest"`:新随机盐(16B)→ 首原像 `password||salt` → 迭代 `rounds` 轮 `sm3_hash_bytes` 拉伸成 32B 摘要。**轮数由调用方(注册/改密)从 `gameconfig.PWD_HASH_ROUNDS` 传入**——原语不读全局(纯、可测),旋钮仍在配置(不硬编码)。
- `verify_password(password, stored) -> bool`:**从存储串读回轮数**(不读当前配置)按同法重算,`hmac.compare_digest` **字节层常量时间**比对。把 rounds 写进串,正是为了「改 `PWD_HASH_ROUNDS` 不废旧哈希」(旧行按其自带轮数校验)。
- **fail-closed**:存储串结构非法(段数≠3 / 盐或摘要非 hex / 轮数非整或 <1)一律 `return False`——无法校验绝不放行,且绝不因一行脏 DB 数据崩掉登录路径。`rounds<1` 在 `hash_password`/`_derive` 侧 `raise`(会退化成不迭代的红线),`gameconfig.PWD_HASH_ROUNDS` 的 `ge=1` 兜住正常路径。

- **盐明文存,没问题**:盐不是密钥,作用是"每人哈希不同"(挡彩虹表 + 挡'相同密码→相同哈希'),不需要保密,跟哈希存一起即可。
- **DB 列已落地**([changes/0056](refactor/changes/0056-p5-user-auth-columns-authenticate.md)):`User` 加 `name`(登录账号,唯一,≤15)/ `hash_password`(存 `salt$rounds$digest`,salt/轮数已内嵌,无需另列)/ `k_user`(SM4 密钥 hex)三列 + Alembic 迁移 `49417b108733`。**均 nullable**:本平台无历史密码数据,加可空列 = 最安全增量迁移(既有行 NULL = 未启用登录;`name` 唯一 → 不能常量 `server_default` 回填,见 [changes/0056](refactor/changes/0056-p5-user-auth-columns-authenticate.md) 决策 1)。校验逻辑 `authenticate`(SM4 解 blob → `verify_password`)+ `load_user_for_login` 查询同批落地;`/user/login` 端点随下一砖。
- **初始密码由管理员生成**(高熵随机),私下发给用户;用户可自行改密。这层防的是"DB 泄露后被反推",和下面的传输加密是两件事,**两者都要**。
- **改密码已落地**([changes/0064](refactor/changes/0064-p7-change-password.md)):`POST /user/password` 走 §加密信道 的会话密钥信封(内层 `{old_password, new_password}`),**验旧密码**(第二因子,防盗 `session_token` 锁死真用户)→ 重算 `salt$rounds$digest`(新盐)→ **同步直写**(鉴权列 DB 权威、无内存副本,不走 delayDB,见 [storage.md](storage.md)「鉴权列写路径」)。错误分层:信封 401 / 旧密码错·未启用 403 / 请求畸形 400 / DB 错 500。**v1 不吊销其它会话**(记 [rest.md](rest.md) / future)。

## 共享密钥(手输,不在前端)

- 每个用户一把**对称密钥 `K_user`**(SM4 用,16 字节),由管理员**带外**(当面/私信)发放,登录时**用户手动输入**——它**不写进前端代码**,所以扒前端拿不到,这正是它和"前端内置 PSK"的关键区别。
- 服务器存每用户的 `K_user`(与鉴权列同表;0056 落单把 `k_user`,**[0066](refactor/changes/0066-p5-kuser-rotation.md) 扩成双钥**:`User.k_cur`(当前)+ `k_prev`(上一把,宽限期内仍可登录)+ 各自 `_ver`/`_until`,迁移 `b8ca88a687af`)。轮换缩小泄露窗口(每周一换,见下)。
- 取舍:**推荐每用户一把**(挡内部人互相解密、限爆炸半径)。若图省事用**全局一把**,则任何内部人都能解别人流量——仅当全员互信时才接受。

## K_user 每周轮换 —— 已落地([changes/0066](refactor/changes/0066-p5-kuser-rotation.md))

**目标:把"一把 `K_user` 泄露还能用多久"压到一周。** 要让轮换真正有意义,新密钥必须**带外、全新随机**(不能由旧密钥派生/经信道下发,否则拿到旧密钥就顺着链拿到新的);这也保住 `K_user`"不在前端、手输"的根本属性(见上)。轮换**不影响已建会话**(会话密钥派生自 `session_token`,与 `K_user` 无关)——只影响之后的登录。

**存储**(每用户两把,带宽限期,避免切换时锁死;`User` 表扩列,迁移 `b8ca88a687af`):

| 字段 | 含义 |
|---|---|
| `k_cur` + `k_cur_ver` + `k_cur_until` | 当前密钥 + 版本号 + **到期应轮换时刻**(排程,登录不查它,见下) |
| `k_prev` + `k_prev_ver` + `k_prev_until` | 上一把 + 版本号 + **宽限截止**(登录查它:过期即拒) |

- **`k_cur_until` 是排程不是拒登时刻(0066 决策 2,偏离本文原「失效时刻」字面)**:若登录也拒过期 `k_cur`,轮换 cron 迟跑/挂掉会把运维故障放大成**全员锁死**;「泄露窗口 ≤ 一周」本就依赖轮换真的发生,不靠拒登兜底。轮换任务只轮 `k_cur_until <= now` 的账号(幂等,跑多勤都行);`k_cur_until` 为 **NULL = 不排程**(dev 种子行),只能 `rotate --name` 显式轮换。
- `*_until` 存 **epoch 秒(float)**,与 auth 全链时基一致(`SessionStore.expires_at`/`now()`/blob.ts 都是 float;DateTime 列在 sqlite 读回丢 tz,鉴权比较不再引入 tz 补丁面)。

**轮换任务 = 管理员 CLI [`scripts/kuser_admin.py`](../scripts/kuser_admin.py) + 系统 cron(每周跑)**:`rotate` 挑出到期账号,逐个——生成**全新随机** `K_user` → 单条 UPDATE 原子搬移(旧 `k_cur` 降为 `k_prev`、宽限 `KUSER_GRACE_DAYS` 天;新键上位 `k_cur`、版本 +1、重排 `k_cur_until = now + KUSER_ROTATION_DAYS`)→ 新钥打到**管理员终端 stdout**。**不做进程内调度**(决定性理由):新钥必须带外下发,进程内轮换产出的新钥无处可去——打进服务器日志违反脱敏红线;CLI stdout 正是带外通道的起点,服务器进程全程不见新钥。CLI 另有 `list`(版本/排程记账,**不含键材料**)与 `issue`(首发/`--reset` 补发:生成高熵随机口令 + `K_user` v1 + 排程;补发即强制换代、清空 `k_prev` 不留宽限)。

**下发(带外)**:CLI 产出的新 `K_user` 由**管理员私下发给用户**(同首发那条带外通道,不走裸 ws/http),用户在宽限期内手输换上。`KUSER_GRACE_DAYS` 给的就是"还没来得及换的人仍能用旧钥登录"的缓冲。

**登录时选哪把(0066 决策 1,偏离本文原「请求带 `key_version`」设计)**:登录请求**不带版本**(`{name, iv, blob}` 不变)——`K_user` 是用户手输的,用户无从知道也不该被要求记版本;服务器**先试 `k_cur`、败再试宽限内的 `k_prev`**(两次 `authenticate`;错钥路径在 SM4 解密/JSON 解析即败、不进昂贵的 `verify_password`,本规模代价可忽略)。版本列退为管理员记账(`list` 对账),不进协议:

- `k_cur` 解开 → 正常,响应 `rotate=false`。
- `k_prev` 解开且未过宽限(`now <= k_prev_until`;`k_prev_until` 为 NULL 的脏行 fail-closed 拒)→ **接受**,响应附 `rotate=true` 提示客户端尽快换新钥。**响应用匹配到的那把加密**(旧钥客户端解不开新钥密文)。
- 两把都不行/旧钥过宽限 → 统一 401,要求带外补发(`issue --reset`)。

**首发 / 强制轮换(疑似泄露)**:`issue --name X`(新账号/启用登录)/ `issue --name X --reset`(立即换代且旧钥零宽限)/ `rotate --name X`(无视排程立即轮换,旧钥留正常宽限)。

> **决策(可改)· 别用"信道自动下发新钥"图省事**:把新 `K_user` 用旧钥加密经信道推给客户端、客户端存本地,虽免去手输,但①新钥进了客户端存储(破坏"不在前端"属性),②拿到旧钥就能解出新钥(链式,轮换不再限泄露窗口)。**仅在确实嫌每周手输烦、且接受削弱时才用**;默认带外手输。

**配置**(照 [config.md](config.md),已随 0066 进 `gameconfig` + `poker.env.example`):

```python
KUSER_ROTATION_DAYS: int = Field(ge=1, le=90)    # 轮换周期(天),基线 7
KUSER_GRACE_DAYS:    int = Field(ge=0, le=30)     # 旧钥宽限期(天),基线 3(0 = 立即失效)
```

## 登录握手(HTTP,把账号密码护住)

token **绝不明文上线**:它只在被 `K_user` 加密的登录响应里出现一次。

```
1. 客户端  POST /user/login
   body = { name,                                   # 明文(非秘密),供服务器选 K_user
            iv,                                      # 16B 随机
            blob = sm4_cbc_enc(K_user, iv, {password, client_nonce, ts}) }   # ts=客户端墙钟(0063 重放守卫)
2. 服务器  按 name 取 K_user(先试 k_cur、败再试宽限内 k_prev,见 §K_user 每周轮换)→ 解 blob → 校验密码(SM3+盐)
           生成  session_id(公开句柄) + session_token(32B 秘密)
           内存会话表[session_id] = { name, nickname, token, exp }
   响应 = sm4_cbc_enc(匹配到的 K_user, iv2, { session_id, session_token, exp, rotate })   # token 被 K_user 护住;rotate=true=在用旧钥、尽快换新(0066)
3. 客户端  解出 session_id(公开)与 session_token(秘密,只留本地)
```

- **登录包重放守卫已落地([changes/0063](refactor/changes/0063-p5-login-replay-guard.md))**:双守卫相与——① **freshness**:`|now − blob.ts| > LOGIN_REPLAY_WINDOW_SECONDS` 拒(绝对值容双向时钟偏斜);② **nonce 去重**:`(name, client_nonce)` 已见拒(`app/auth/nonce.py` `NonceCache`,活在 login router、惰性剪枝且**严格过期后才剪**)。**条目 TTL = 2×新鲜窗**——ts 可超前 now 至 W,blob 新鲜期最晚到 ts+W ≤ now+2W,条目必须盖住整个新鲜期,否则「条目先过期、blob 还新鲜」留重放缝(0063 自 review 抓修)。重放包要么 ts 过期、要么 nonce 撞库;检查在 `authenticate` 之后(伪造包灌不进缓存),失败仍统一 401。**已接受残余窗(记档)**:进程重启清空 nonce 缓存 → freshness 窗内旧包可复活一次(窗短 × 重启罕见,不持久化 nonce)。
- **第 2 步的凭证校验已落地**([changes/0056](refactor/changes/0056-p5-user-auth-columns-authenticate.md)):`app/db/queries.py` `load_user_for_login(name) -> LoginUser|None`(按 `name` 载 uid/nickname/hash_password/k_cur/k_prev/k_prev_until)+ `app/auth/credentials.py` `authenticate(hash_password, k_user_hex, iv, blob) -> LoginProof|None`(取 K_user 解 blob → `{password, client_nonce, ts}` → `verify_password`,**fail-closed**:缺列/解密坏/JSON 坏/缺 ts/ts 非数值/密码错一律 None、绝不崩;`client_nonce`/`ts` 透出供端点重放守卫,0063 起 ts 必填)。**登录端点已落地**([changes/0059](refactor/changes/0059-p5-login-endpoint.md)):`app/rest/login.py` `make_login_router` —— `{name,iv,blob}` → `load_user_for_login` → `authenticate` → `SessionStore.create` → `K_user` 加密下发 `{session_id, session_token, exp}`(**无 JWT**);**fail-closed 统一 401**(不泄未知账号/密码错/blob 坏之别);挂 `create_app` + `SessionStore` 进 DevShell。**dev 种子已 login-enable**([changes/0060](refactor/changes/0060-p5-dev-seed-login-enabled.md)):`seed_dev_users` 给 DEV_USERS 补 `name`=昵称 / 共享 `DEV_PASSWORD` 哈希 / 共享 `DEV_KUSER`(dev-only),故 DEV_USERS 可真登录(生产走每用户带外 K_user)。**ws 信道接线已落地**([changes/0061](refactor/changes/0061-p5-ws-secure-channel-wiring.md)):登录拿到 `{session_id, session_token}` 后,客户端以 `/ws?sid=<session_id>` 连接,此后每帧走会话密钥信封(Receiver/Sender 接进 `SecureChannel`,见 §加密信道)。**REST 信封亦已落地**([changes/0062](refactor/changes/0062-p5-rest-envelope-user-me.md),信封助手 + 路由工厂,非 middleware);**重放守卫亦已落地**([changes/0063](refactor/changes/0063-p5-login-replay-guard.md),见上);**K_user 双钥轮换亦已落地**([changes/0066](refactor/changes/0066-p5-kuser-rotation.md),双钥两次尝试 + `rotate` 提示 + 管理员 CLI,见 §K_user 每周轮换)。登录握手全链闭环,**P5 全部落地**。
- 会话表是**内存 shell 状态**(同原型 `_refresh_token_pool`,已随 0027 拆除),进程重启即失效→重新登录,可接受。**已落地** [`app/auth/session.py`](../app/auth/session.py)([changes/0055](refactor/changes/0055-p5-session-store.md)):`SessionStore`(`create(name,nickname,now)->(session_id, Session)` / `lookup(sid,now)`(过期删返 None)/ `revoke` / `prune(now)`)+ `Session{name,nickname,token,expires_at}`;`session_id=token_urlsafe`(公开句柄)、`token=token_bytes(32)`(秘密,派生逐帧密钥见 [channel.py](../app/auth/channel.py))。时钟外移(`now` 显式传,同 timer.md)、`exp=now+SESSION_TTL_SECONDS` 服务器兜底。`/user/login` 铸会话(0059)+ ws 握手 `?sid=` 查表(0061)均已落地。
- **登录只返回会话凭证(无 JWT)**:响应(被 `K_user` 加密)含 `{session_id, session_token, exp}`。`session_id` 公开、当报文 `selector`;`session_token` 只留客户端本地、派生 enc/mac 密钥。之后 ws 与 REST 都用会话密钥加密(见 §加密信道 / [changes/0057](refactor/changes/0057-p5-unified-encrypted-channel-design.md))。

## 加密信道(登录后一切流量:ws 与 REST 同构)

**登录后每条消息(ws 帧、REST 请求/响应)是同一个信封**,用会话密钥加密+认证;**无独立身份令牌,解密成功即认证**(见 [changes/0057](refactor/changes/0057-p5-unified-encrypted-channel-design.md))。

**密钥来自会话密钥**(登录下发的 `session_token`,永不再上线):

```
enc_key = KDF_sm3(session_token + b"\x01", 16)   # ws SM4 128-bit
mac_key = KDF_sm3(session_token + b"\x02", 32)   # ws HMAC-SM3
# REST 另派一对(info b"\x03"/b"\x04",derive_rest_keys):与 ws 分域,跨信道重放 MAC 必败(0062)
```

不再逐连接派 `server_nonce`——REST 无连接上下文,须「查会话即解」;跨重连重放改由**按会话计的 seq** 挡(见下)。

**信封格式**(出入站对称;seq 计法分信道:ws 各方向各自计数、REST 响应回显请求 seq,见下/0062):

```
报文 = selector ‖ iv(16B) ‖ ct ‖ mac(32B)
  selector = session_id                                   # 公开句柄;服务器据此查会话取 enc/mac 密钥 + 身份
  ct  = sm4_cbc_enc(enc_key, iv, seq(8B,BE) ‖ plaintext_json)   # seq 藏密文内(保密 + 被 MAC 罩住)
  mac = hmac_sm3(mac_key, iv ‖ ct)                        # encrypt-then-MAC(mac 不盖 selector:错 selector→错密钥→MAC 必败,完整性隐式受保;故原语传输无关)
```

**入站铁序(必须照此)**:读 `selector` → 查会话取 `enc_key`/`mac_key`/`user`(查不到/过期 → 拒)→ **① 验 MAC**(`compare_digest` 常量时间)→ **② 才 `sm4_cbc_dec` 解密** → 取出 `seq ‖ plaintext_json` → **③ 验 seq 新鲜**(ws:`seq > 本会话已见` 严格单调;REST:滑动窗判重,见「REST 信封」)→ `plaintext`=wire `ClientMessage`、身份=会话 `user` → 交业务(`to_command(origin=nick)` → inbox / REST handler)。任一步失败:丢弃 +(ws)关连接 /(REST)拒。**绝不先解密后验**——库去填充是裸的,解未验数据有 padding-oracle 风险,故 **MAC 必在解密前**。

> **seq 放密文内、解密后验**:内网无 DDoS 顾虑,不必解密前廉价拒重放;seq 进密文更保密、被 MAC 罩住改不了。重放的真包 MAC 能过但 seq 不新鲜 → 第 ③ 步拒。**seq 按会话计**(非按连接):跨重连旧包 seq 不新即被挡;进程重启会话表清空 → 会话失效 → 重登换钥,seq 从头也安全。0057 留白的「REST 并发 seq 处理」已按**滑动窗**落地(0062,见「REST 信封已落地」)。

**`hmac_sm3` 是带密钥的 MAC(两输入)**:`hmac_sm3(mac_key, msg)` —— key 证明持钥(=认证)、msg 防篡改;裸 `sm3(msg)` 无 key 谁都能算,故用 HMAC(标准 ipad/opad 构造,兼避裸 SM3 长度扩展)。**逐帧加解密原语**(`hmac_sm3`/`derive_keys(session_token)`/`SecureChannel`(`derive(token,max)`/`seal`/`open`))已随 [0054](refactor/changes/0054-p5-secure-frame-channel.md) 起、**并随 [0058](refactor/changes/0058-p5-session-channel-rework.md) 改造到本信封/顺序**(逐会话密钥[去 `server_nonce`] + seq 入 ct + `MAC→decrypt→seq` + mac 盖 `iv‖ct`)。

**ws 接线已落地([0061](refactor/changes/0061-p5-ws-secure-channel-wiring.md))**:`/ws?sid=` 握手查会话 → get-or-derive **挂 Session 的** `SecureChannel`(逐会话、跨重连复用 → seq 连续)→ `Connection.channel` 引用之;Receiver 收二进制帧 `open`(验 MAC→解密→验 seq)、Sender `seal` 出站,**core/reduce 全程不知有加密**。**ws 的 selector 落在握手 URL(`?sid=`)、逐帧省略**(连接已绑会话,`open` 收 `iv‖ct‖mac`);**REST 无连接上下文才逐请求带 selector**——落地形是 JSON `{sid, frame}`(`sid` 即 selector、`frame`=hex(iv‖ct‖mac),见「REST 信封已落地」),上面「报文=selector‖iv‖ct‖mac」是其概念形,ws 帧不含 selector。**客户端契约**:同一会话跨重连须**保留 ws seq**(仅新登录换会话才重置),否则重连首帧 seq 回退被 `stale_seq` 拒。**余**:dev 明文 `?nick=` 端点并存,前端切加密后退役(K_user 每周轮换已落地 [0066](refactor/changes/0066-p5-kuser-rotation.md))。

**REST 信封已落地([0062](refactor/changes/0062-p5-rest-envelope-user-me.md))**,与 ws 半边四点分化:

- **密钥分域**:REST 用 `derive_rest_keys(session_token)`(info `0x03/0x04`),与 ws(`0x01/0x02`)四钥互异——截获的 REST 信封注入 ws(或反向)MAC 必败,**跨信道重放根治于密钥层**;副产品是两边 seq 空间天然独立、客户端各自计数。
- **wire 形(hex JSON,同 login)**:请求 `POST` body `{sid, frame}`(`frame`=hex(iv‖ct‖mac),内层明文 = 端点参数 JSON,无参为 `{}`);响应 `{frame}`。信封原语复用无状态 `seal_envelope`/`open_envelope`(`SecureChannel.seal/open` 即其委托 + ws 严格单调策略)。
- **防重放 = 每会话滑动窗**(`ReplayWindow`,IPsec 式,挂 `Session.rest_window`,宽度 `REST_REPLAY_WINDOW`):`seq > top` 推进、窗内未见过的乱序迟到收、重复/太旧拒——REST 并发请求可能乱序到达,ws 的严格单调会误拒(0057 决策 3 落地)。**客户端重试规则**:重试 = 新请求,**重封新 seq**;响应丢失后原帧重投必被窗判重 → 401(服务器无从分辨重试与重放,fail-closed),别拿 401 当「需重登」的唯一信号盲目重登——先用新 seq 重试一次。
- **响应 seq 回显请求 seq(请求-响应绑定)**:不设第二个服务器出站计数器;客户端验「seq == 我发的」即绑定——同会话请求 seq 严格递增不复用,旧响应答不了任何后续请求。**此处偏离 0057「各方向各自计数」**(其 §4 本就注明细节实现砖定),绑定性更强且免第二计数器。**已知可接受面(记档)**:请求/响应共用同一对 REST 密钥,把请求帧**反射**回客户端当响应能过 MAC+seq,但内层是请求参数 JSON、响应形状解析必败——攻击者读不到任何东西,只是客户端一次解析错误(nuisance);要根治需再分收发两对密钥,本规模不值。
- **错误两段式**:信封任何一步不过(sid 不识/过期、hex/结构/MAC/解密坏、seq 重放、内层非 JSON 对象)→ **统一 401** fail-closed(同 login);信封验过后的失败(DB 错等)→ **明文 500 无 body 细节**(已认证,非鉴权问题,客户端不必重登)。
- **覆盖面(决策,可改)**:只有**需身份**的端点走信封(首个消费者 `POST /user/me`,见 [rest.md](rest.md));公开读(lobby/rooms、leaderboard、hands)**留明文**——三者无隐私(房配/头数/结算分/无底牌记录),rest.md 本就明示可留公开。日后要「一切 REST 加密」再把读端点改 POST 信封收编。

**身份 = 被认证的会话,不是自报的 `selector`。** selector 只是查密钥的公开句柄;真认证是「用该会话密钥解出且 MAC 验过」。报文里若还带自报 id,只能放**密文内**、由服务器校验等于会话身份(纵深防御),绝不信明文。

## 与新架构的衔接(加解密是 shell 的事)

- **加解密在 ws 边界,core 只见明文**:Receiver 收帧 → 验+解 → 明文 `ClientMessage` → `Command`;Sender 取 `Event` 的 `ServerMessage` → 序列化 → 加密成帧 → `ws.send`。**core / reduce 全程不知道有加密**,和它不知道 JSON 序列化一样(守分层、不变量 1)。
- **会话密钥与序号是 per-session 的 shell 状态**(挂在会话表项 / `SecureChannel` 上),**绝不进 `world`**——和 [timer.md](timer.md) 的"时间戳只活在 shell"同理,墙钟/密钥/序号都是非确定外部状态,进 core 就破坏确定性。
- **握手 → Connect**:验完会话拿到 `nickname`,投 `Connect(nick)` 接入大厅(模型 2:连接绑 nick;进房/载入积分在 `JoinRoom`,见 [lobby.md](lobby.md))。鉴权失败:握手阶段就用 ws 关闭码拒掉,**绝不接入、绝不建 `Connection`**。
- **鉴权字段不进 `UserState`**:`hash_password` / `K_user` / `session_token` 都是 DB/shell 的事,`world.users` 只放游戏权威字段(`points` 等),呼应 [user.md](user.md)。

## 残余风险与红线

- **密钥分发**:`K_user` 必须带外安全送达,别走同一条裸 ws/http;泄露就轮换。
- **全局密钥的内部威胁**:用全局一把则内部人可解他人流量——选每用户密钥规避。
- **前向保密只到"轮换窗口"粒度**:定期换钥(见「会话过期与密钥轮换」)让单把 `session_token` 泄露**只暴露它那一个 `SESSION_TTL` 窗口**的流量,不是全历史。但 **`K_user` 泄露仍是全损**——它能派生任意未来会话、解登录响应,直到 `K_user` 轮换。`K_user` 是真正要守住的根。积分非货币,这个边界接受。
- **实现正确性(最易翻车处)**:IV **每帧新鲜随机**(别复用、别用计数器当 IV);MAC **先验后解**且**常量时间**比对;seq 新鲜性照各信道之法(ws **严格递增**且双向各自计数;REST 请求过滑动窗、响应回显请求 seq,见「REST 信封已落地」);`session_token` 只留客户端内存、不落 URL/日志/storage;token 设 `exp` 并可吊销。
- **脱敏照旧**:`K_user`/`token`/`password` 任何级别都不进日志(并入 [log.md](log.md) 的红线,和 `hole_cards`/`deck` 同级)。
- **DoS 不在范围**:RST 切连无法防,客户端断线重连即可(走 [timer.md](timer.md) 的占座/重连窗口)。

## 配置(照 [config.md](config.md))

```python
class GameConfig(BaseSettings):
    PWD_HASH_ROUNDS: int      = Field(ge=1, le=100000)   # 密码哈希迭代轮数(已落地 0053)
    SESSION_TTL_SECONDS: int  = Field(ge=60, le=86400)   # 会话 token 有效期(已落地 0055,SessionStore 消费)
    WS_FRAME_MAX_BYTES: int   = Field(ge=256, le=1048576) # ws 单帧上限,防超大帧(已落地 0054)
    REST_FRAME_MAX_BYTES: int = Field(ge=256, le=1048576) # REST 信封上限(已落地 0062)
    REST_REPLAY_WINDOW: int   = Field(ge=1, le=4096)      # REST 防重放滑动窗宽度(已落地 0062)
    LOGIN_REPLAY_WINDOW_SECONDS: int = Field(ge=1, le=3600) # 登录包新鲜窗 W;nonce 条目 TTL=2W(已落地 0063)
    KUSER_ROTATION_DAYS: int  = Field(ge=1, le=90)        # K_user 轮换周期(天;已落地 0066)
    KUSER_GRACE_DAYS: int     = Field(ge=0, le=30)        # 旧钥宽限期(天;已落地 0066)
    # K_user / 盐 等秘密存 DB,不进 env
```

> 各字段**随其消费方砖落地**(不预铺无消费者的配置):`PWD_HASH_ROUNDS`([0053](refactor/changes/0053-p5-password-hashing.md))、`WS_FRAME_MAX_BYTES`([0054](refactor/changes/0054-p5-secure-frame-channel.md))、`SESSION_TTL_SECONDS`([0055](refactor/changes/0055-p5-session-store.md),`SessionStore`)均已进 `gameconfig` + `poker.env.example`。

## 待办 / 可选升级

- ~~**每用户密钥的下发与轮换工具**(管理员侧)~~:**已落地 [0066](refactor/changes/0066-p5-kuser-rotation.md)** —— [`scripts/kuser_admin.py`](../scripts/kuser_admin.py) `issue`(首发/补发)/ `rotate`(cron 轮换/强制)/ `list`(记账);见「K_user 每周轮换」。
- **REST 也走加密信封**(不再裸奔):查手牌/余额/排行的请求响应体套 §加密信道 的 `selector‖iv‖ct‖mac`,与 ws 同一把会话密钥(见 [changes/0057](refactor/changes/0057-p5-unified-encrypted-channel-design.md))。
- **SM2 升级路径(可选)**:若想连"手输密钥"都省掉,可改用 SM2 做密钥交换(服务器持私钥、前端内置公钥)协商会话密钥;能去掉带外分发,但多一套握手。当前手输密钥方案已够本规模。
- **wss 才是终局**:若日后能上反代(Caddy 自动证书几乎零配置),则本文的应用层加密**整套可拆除**,登录走 HTTPS、ws 用标准 JWT 即可。
```
