# 鉴权与 WS 安全信道(auth 模块)

## 定位 & 现状两个洞

本模块做两件事:认证身份;无 TLS(纯 ws,非 wss)时在应用层保护传输。

原型有两个漏洞:ws 端点零鉴权——`/pokertable/room?user_nickname=Y` 明文 query 传昵称,任何人可冒充,已于 0027 拆除;登录密码与游戏消息全程明文,可被嗅探。

对策是用国密算法自建安全信道(见 [changes/0057](refactor/changes/0057-p5-unified-encrypted-channel-design.md)):

- 不用 SM2 协商,改用手输共享密钥引导;登录时 `K_user` 对称加密保护账号密码,换回会话密钥。
- 登录后每条消息(ws 与 REST 都算)按 `selector‖iv‖ct‖mac` 信封发送:会话密钥 SM4 加密、HMAC-SM3 完整性校验、序号防重放。
- **解密成功即认证**,不另设 token 校验步骤。

> 用的库:[ttxsgm](../../lib/ttxsgm/ttxsgm/__init__.py) 的 `sm4_cbc_enc/dec`、`sm3_hash`/`sm3_hash_bytes`/`KDF_sm3`。

## 威胁模型(决定了"够用"的边界)

按内网小规模定边界:防嗅探和冒充,不防国家级攻击。

- 环境:内网、≤20 个可控用户、积分非货币。
- 要防:嗅探读消息;冒充他人 / 篡改;重放;密码被嗅;ws 裸昵称冒充。
- 不防(本规模接受):国家级攻击;TCP RST 切断连接(DoS,wss 也防不住);已拿到密钥的内部人;完整前向保密——只做到"轮换窗口"级,见残余风险。

## 身份模型:name vs nickname

一个用户有两个名字:登录用 `name`,游戏里用 `nickname`。

| 字段 | 是什么 | 用在 |
|---|---|---|
| `name` | 登录账号(唯一,≤15),不可变 | 登录、定位用户、选每用户密钥 |
| `nickname` | 游戏昵称(唯一,≤50),可改(change_nickname) | `world.users` 的键、座位、所有牌局逻辑 |

认证链:手输密钥 + 账号密码 → 认证出 `name` → 查 DB 得 `nickname` → 握手后投 `Connect(nick)` 接入大厅。

接入大厅时连接绑 nick、不绑房间,即"模型 2"(连接归属是人,不是牌桌)。进房与载入积分在之后的 `JoinRoom`(见 [lobby.md](lobby.md))。

## 密钥层级(引导密钥 + 会话密钥)

两层密钥:手输的 `K_user` 只用于登录,换回的会话密钥负责之后一切流量。不分 REST/WS 两套凭证,不用 JWT:身份从解密得出(0057)。

| 层 | 凭证 | 干嘛 |
|---|---|---|
| 引导 | `K_user`(每用户手输 SM4 密钥,每周轮换) | 只在登录时保护账号+密码、换回会话密钥 |
| 会话 | `session_token`(32B 秘密,带 `exp`)+ 公开 `session_id` | 登录下发;派生 enc/mac 密钥,此后 ws 与 REST 每条消息都用它加密+认证 |

`session_id` 就是报文里的 `selector`:公开句柄,服务器据此查会话取密钥和身份。

### 会话过期与密钥轮换

这里的轮换仅指 `session_token`,不涉及 `K_user`。每次登录生成新 `session_token` 并派生新的一对 enc/mac 密钥——一次登录 = 一把密钥。定期轮换缩小泄露面,对用户无感:

- **轮换 = 无感重连**。客户端在 `SESSION_TTL` 到期前,用本地缓存的 `K_user` 静默重跑登录握手拿新 `session_token`。新连接顶替旧连接(即"顶替再连":同一用户的新连接把旧连接踢下线,见 [connection.md](connection.md)),reduce 私发 `StateSnapshot` 对齐。
- **服务器 exp 兜底**。`session_token.exp` 到点即拒:新握手 / REST 查表即拒(0055/0062);活 ws 连接也强制(0070),收帧和出站各比对一次 `expires_at`,过期即关连接 4401。例外:双向零流量的过期连接活到下次任一方向有活动——接受,无流量即无泄露面。
- **什么时候要重新手输**。客户端进程还在、`K_user` 还在内存时,轮换与重连都静默;`K_user` 即长期 refresh 凭证,不另设 refresh token。
  > **现状与这句不符,如实记档(0097)**:前端把 `K_user` 存在 `localStorage`(`transport/session.ts`),**登出与关页面都不清**(`clearKUser()` 至今零调用者),所以重新登录只需再输密码、不必再输 `K_user`。那是前端有意的取舍——`K_user` 每周轮换,每次登出都要重输摩擦太大。孰对孰错(共享机器的安全 vs 手输摩擦)**尚未定案**,在定案前别把上面这句当成实现契约。
- **不做中途 rekey / 完整前向保密**。定期无感重连换钥已够,本规模不做棘轮。

> 轮换周期由 `SESSION_TTL_SECONDS` 定(见配置);客户端应在到期前留余量主动轮换。

## 密码存储:SM3 + 每用户盐 + 迭代

密码不裸哈希,防的是 DB 泄露后被反推。原型裸 `sm3_hash(password)`,可被彩虹表/撞库;改为每用户随机盐 + N 轮迭代 SM3,存 `salt$rounds$digest`(全 hex)。这层与传输加密是两件事,两者都要。

原语在 [`app/auth/passwords.py`](../app/auth/passwords.py),纯函数,精确签名以代码为准([changes/0053](refactor/changes/0053-p5-password-hashing.md)):

- `hash_password(password, rounds) -> "salt$rounds$digest"`:取新随机盐(16B),首原像 = `password||salt`,迭代 `rounds` 轮 `sm3_hash_bytes` 得 32B 摘要;轮数由调用方从 `gameconfig.PWD_HASH_ROUNDS` 传入,原语不读全局以保持纯函数可测。
- `verify_password(password, stored) -> bool`:从存储串读回轮数(不读当前配置)按同法重算,用 `hmac.compare_digest` 常量时间比对;轮数内嵌在存储串里,改 `PWD_HASH_ROUNDS` 后旧哈希仍按自带轮数校验、不作废。
- fail-closed(校验不了就不放行):存储串结构非法一律 `return False`——段数≠3、盐或摘要非 hex、轮数非整或 <1,既不放行也不因脏数据崩掉登录路径;`rounds<1` 在 `hash_password`/`_derive` 侧 `raise`(否则退化成不迭代),`gameconfig.PWD_HASH_ROUNDS` 的 `ge=1` 兜住正常路径。

其余要点:

- 盐明文存没问题:盐不是密钥,只为让每人哈希不同,不需保密。
- DB 列已落地([changes/0056](refactor/changes/0056-p5-user-auth-columns-authenticate.md)):`User` 加三列 `name`/`hash_password`/`k_user`,配迁移 `49417b108733`;三列均 nullable,加可空列是最安全的增量迁移,NULL 表示未启用登录;`name` 唯一,故不能用常量 `server_default` 回填(0056 决策 1);`authenticate` 与 `load_user_for_login` 同批落地。
- 初始密码由管理员生成(高熵随机),私下发给用户;用户可自行改密。
- 改密码已落地([changes/0064](refactor/changes/0064-p7-change-password.md)):`POST /user/password` 走会话密钥信封,先验旧密码作第二因子(防止盗得 `session_token` 的人直接改密、把真用户锁死),再重算新盐哈希;同步直写 DB,鉴权列以 DB 为权威,不走 delayDB(延迟落库的写缓冲),见 [storage.md](storage.md)「鉴权列写路径」。错误分层与细节见 [rest.md](rest.md)。
  **改密成功即吊销该账号其它会话**([0097](refactor/changes/0097-revocation-that-actually-bites.md) 翻掉 0064 的「v1 不吊销」):改密要求旧密码作第二因子,所以能改的必是本人;而「怀疑号被盗 → 改密码」是用户唯一的自救手段,旧会话还活着这个手段就等于没有。留下当前会话,免得把正在操作的人自己踢下线;失败(旧密码错)不吊销任何东西,否则成了「猜错密码即踢人下线」的骚扰面。

## 共享密钥(手输,不在前端)

每用户一把对称密钥,带外发放、登录时手输,永不进前端代码。

- `K_user` 是 SM4 密钥,16 字节;管理员当面 / 私信发放,用户登录时手动输入。
- 不写进前端代码,扒前端拿不到——这是与"前端内置 PSK"的关键区别。
- 服务器存每用户的 `K_user`,与鉴权列同表:0056 落地时是单把 `k_user`,[0066](refactor/changes/0066-p5-kuser-rotation.md) 扩成双钥 `k_cur` + `k_prev` + 各自 `_ver`/`_until`,迁移 `b8ca88a687af`。
- 取舍:每用户一把,内部人无法互相解密;全局一把仅全员互信时可接受。

## K_user 每周轮换 —— 已落地([changes/0066](refactor/changes/0066-p5-kuser-rotation.md))

目标:把「一把 `K_user` 泄露后还能用多久」压到一周。两条前提:

- 新密钥必须带外、全新随机。不能由旧密钥派生,也不能经信道下发——否则拿到旧密钥就能顺链拿到新密钥,也破坏「不在前端、手输」的属性。
- 轮换不影响已建会话。会话密钥派生自 `session_token`,与 `K_user` 无关;轮换只影响之后的登录。

**存储**:每用户存两把,旧的那把带宽限期,避免切换时把人锁死(迁移 `b8ca88a687af`)。`*_until` 存 epoch 秒(float),与 auth 全链时基一致:`SessionStore.expires_at`、`now()`、blob.ts 都是 float;DateTime 列在 sqlite 读回会丢 tz。

| 字段 | 含义 |
|---|---|
| `k_cur` + `k_cur_ver` + `k_cur_until` | 当前密钥 + 版本号 + 到期应轮换时刻;`k_cur_until` 只给排程用,登录不查它;轮换任务只轮 `k_cur_until <= now` 的账号(幂等);为 NULL 表示不排程(dev 种子行),只能用 `rotate --name` 显式轮换 |
| `k_prev` + `k_prev_ver` + `k_prev_until` | 上一把 + 版本号 + 宽限截止;`k_prev_until` 登录要查,过期即拒 |

`k_cur_until` 是排程时刻,不是拒登时刻(0066 决策 2):若登录也拒过期的 `k_cur`,轮换 cron 迟跑或挂掉,就把一次运维故障放大成全员锁死。「泄露窗口 ≤ 一周」靠轮换真的发生,不靠拒登兜底。

**轮换任务** = 管理员 CLI [`scripts/kuser_admin.py`](../scripts/kuser_admin.py) + 系统 cron(每周跑;crontab 示例见 [dev.md](dev.md)「K_user 管理」)。

`rotate` 做什么:

1. 挑出到期账号。
2. 逐个生成全新随机 `K_user`。
3. 单条 UPDATE 原子搬移:旧 `k_cur` 降为 `k_prev`,宽限 `KUSER_GRACE_DAYS` 天;新钥上位 `k_cur`,版本 +1,重排 `k_cur_until = now + KUSER_ROTATION_DAYS`。
4. 新钥打到管理员终端 stdout。

为什么不做进程内调度:新钥必须带外下发,进服务器日志违反脱敏红线;CLI stdout 即带外通道起点,服务器进程全程不见新钥。

CLI 另外两个子命令:`list` 打版本 / 排程记账,不含键材料;`issue` 做首发,或 `--reset` 补发——生成高熵随机口令 + `K_user` v1 + 排程,补发即强制换代,清空 `k_prev`、不留宽限。

定性:换钥半自动、发钥永远手动(设计使然)。cron 只完成「DB 里换上新钥」,新钥须管理员看 stdout 后带外私发,用户在宽限期内手输换上。`KUSER_GRACE_DAYS` 是给还没换的人仍能用旧钥登录的缓冲,忘跑 cron 也不锁人。

**登录时选哪把**(0066 决策 1):登录请求不带版本,`{name, iv, blob}` 不变——`K_user` 是手输的,不该要求用户记版本。服务器先试 `k_cur`,失败再试宽限内的 `k_prev`(两次 `authenticate`);错钥路径在 SM4 解密或 JSON 解析就失败,不会进昂贵的 `verify_password`。版本列只作管理员记账(`list` 对账),不进协议。三种结果:

- `k_cur` 解开 → 正常,响应 `rotate=false`。
- `k_prev` 解开且未过宽限(`now <= k_prev_until`)→ 接受,响应附 `rotate=true` 提示尽快换新钥;响应用匹配到的那把加密,因为旧钥客户端解不开新钥密文。`k_prev_until` 为 NULL 的脏行 fail-closed 拒。
- 两把都不行,或旧钥过宽限 → 统一 401,要求带外补发(`issue --reset`)。

**首发 / 强制轮换(疑似泄露)**:`issue --name X` 用于新账号或给已有账号启用登录;`issue --name X --reset` 立即换代且旧钥零宽限;`rotate --name X` 无视排程立即轮换,旧钥留正常宽限。

> **决策(可改)· 不用「信道自动下发新钥」**:新钥用旧钥加密推给客户端虽免手输,但有两点削弱——新钥进了客户端存储;拿到旧钥就能解出新钥,轮换不再限制泄露窗口。仅在接受这两点时才用;默认带外手输。

**配置**(照 [config.md](config.md),已随 0066 进 `gameconfig` + `poker.env.example`):

```python
KUSER_ROTATION_DAYS: int = Field(ge=1, le=90)    # 轮换周期(天),基线 7
KUSER_GRACE_DAYS:    int = Field(ge=0, le=30)     # 旧钥宽限期(天),基线 3(0 = 立即失效)
```

## 登录握手(HTTP,把账号密码护住)

登录用 `K_user` 加密一来一回,换回会话凭证。token 绝不明文上线:只在被 `K_user` 加密的登录响应里出现一次。

```
1. 客户端  POST /user/login
   body = { name,                                   # 明文(非秘密),供服务器选 K_user
            iv,                                      # 16B 随机
            blob = sm4_cbc_enc(K_user, iv, {password, client_nonce, ts}) }   # ts=客户端墙钟(0063 重放守卫)
2. 服务器  按 name 取 K_user(先试 k_cur、败再试宽限内 k_prev,见 §K_user 每周轮换)→ 解 blob → 校验密码(SM3+盐)
           生成  session_id(公开句柄) + session_token(32B 秘密)
           内存会话表[session_id] = { name, nickname, token, exp }
   响应 = sm4_cbc_enc(匹配到的 K_user, iv2, { session_id, session_token, exp, rotate })   # rotate=true=在用旧钥、尽快换新(0066)
3. 客户端  解出 session_id(公开)与 session_token(秘密,只留本地)
```

### 登录包重放守卫

两道检查都要过([changes/0063](refactor/changes/0063-p5-login-replay-guard.md)):

1. **freshness**:`|now − blob.ts| > LOGIN_REPLAY_WINDOW_SECONDS` 就拒。取绝对值,以容双向时钟偏斜。
2. **nonce 去重**:`(name, client_nonce)` 已见过就拒。缓存是 `app/auth/nonce.py` 的 `NonceCache`,活在 login router,惰性剪枝且严格过期后才剪。

条目 TTL = 2×新鲜窗,理由:ts 可超前 now 至 W,故 blob 的新鲜期最晚到 ts+W ≤ now+2W;条目必须盖住整个新鲜期,否则出现「条目先过期、blob 还新鲜」的重放缝(0063 发现并修)。

其余规则:检查放在 `authenticate` 之后,伪造包才灌不进缓存,失败统一 401;已接受的残余窗是进程重启会清空 nonce 缓存,freshness 窗内的旧包可复活一次——窗短、重启罕见,不为此持久化 nonce。

### 凭证校验([changes/0056](refactor/changes/0056-p5-user-auth-columns-authenticate.md))

两个函数配合:

- `app/db/queries.py` 的 `load_user_for_login(name) -> LoginUser|None`:按 `name` 载 uid/nickname/hash_password/k_cur/k_prev/k_prev_until。
- `app/auth/credentials.py` 的 `authenticate(hash_password, k_user_hex, iv, blob) -> LoginProof|None`:解 blob → `{password, client_nonce, ts}` → `verify_password`。fail-closed、绝不崩——缺列、解密坏、JSON 坏、缺 ts、ts 非数值、密码错,一律返回 None;`client_nonce`/`ts` 透出供端点做重放守卫,0063 起 ts 必填。

### 登录端点([changes/0059](refactor/changes/0059-p5-login-endpoint.md))

`app/rest/login.py` 的 `make_login_router`,流程:`{name,iv,blob}` → `load_user_for_login` → `authenticate` → `SessionStore.create` → 用 `K_user` 加密下发 `{session_id, session_token, exp}`。

- fail-closed 统一 401,不区分未知账号 / 密码错 / blob 坏;挂 `create_app` + `SessionStore` 进 DevShell。
- dev 种子可登录([changes/0060](refactor/changes/0060-p5-dev-seed-login-enabled.md)):`seed_dev_users` 给 DEV_USERS 补 `name`=昵称、共享 `DEV_PASSWORD` 哈希、共享 `DEV_KUSER`。dev-only,生产走每用户带外 K_user。
- ws 接线见 [changes/0061](refactor/changes/0061-p5-ws-secure-channel-wiring.md),REST 信封见 [changes/0062](refactor/changes/0062-p5-rest-envelope-user-me.md)。P5 全部落地。

### 会话表

会话表是内存 shell 状态(同原型 `_refresh_token_pool`,已随 0027 拆除),进程重启即失效,重新登录即可。已落地 [`app/auth/session.py`](../app/auth/session.py)([changes/0055](refactor/changes/0055-p5-session-store.md)):

- `SessionStore` 的方法:`create(name,nickname,now)->(session_id, Session)`、`lookup(sid,now)`(过期则删除并返 None)、`revoke(sid)->bool`、`revoke_all_for_name(name, except_id=None)->int`、`rename_nickname(name,new_nick)->int`、`prune(now)`。
- `Session{name,nickname,token,expires_at}`:`session_id=token_urlsafe` 是公开句柄,`token=token_bytes(32)` 是秘密,用于派生逐帧密钥(见 [channel.py](../app/auth/channel.py))。
- 时钟外移:`now` 显式传入,同 timer.md;`exp=now+SESSION_TTL_SECONDS`,做服务器兜底。

登录只返回 `{session_id, session_token, exp}`,无 JWT;此后 ws 与 REST 都用会话密钥加密(见 §加密信道)。

#### 吊销(0097)

**吊销 = 摘表项 + 就地把那个 `Session` 判死(`expires_at = 0`)。** 只摘表项不够:活 ws 连接持有的是 `Session` **对象**和从它派生的 `SecureChannel`,收帧与出站都只比对 `conn.session.expires_at`、从不回头查表(见上 §会话过期 的 exp 兜底)。判死对象才能让那条既有的强制路径在**下一帧(任一方向)** 把连接关掉(4401),连「双向零流量的连接活到下次有活动」这个例外都原样继承。

两个消费者:

- **`POST /user/logout`**:吊销发起方自己这一个会话(见 [rest.md](rest.md))。前端「退出」必须调它——只清本地的话,服务器上那把 token 一直有效到 TTL 自然到期。
- **改密码**:吊销该账号**其它**会话,留下当前这个(见下 §密码存储)。

按 `name` 找会话是线性扫 `_by_id`(`rename_nickname` 同款),**不建 `name→sessions` 索引**:在线 ≤20,而索引是第二份事实源,`create`/`lookup` 惰性删/`prune`/`revoke` 四处都得维护,漂一处就是「吊销了但没吊销干净」。

吊销**不是即时屏障**,三处边界如实记:

- **零流量的连接活到下次有活动**——继承自 exp 兜底的同一条例外(见上 §会话过期)。无流量即无泄露面,但它在 `world` 里仍占着座、presence 仍报在线,直到被顶替或清理。
- **已经进门的那一帧照常执行**。吊销只挡「下一帧」;此刻正在 GameLoop 里处理的命令会照常 commit、照常落库。要做到「吊销即刻起没有任何命令再生效」得引入屏障,本规模不做。
- **偷的若正是你手上这把 token(同一 `session_id`),改密赶不走他**:`except_id` 放过的是「当前这个会话」,而他与你共用它。补救是改完密码**再登出一次**(现在登出是真的了),重新登录即换新会话。

**够不着的那一块,如实记:`K_user` 泄露后没有带外吊销通道。** [`kuser_admin.py`](../scripts/kuser_admin.py) 是独立进程,而会话表是服务器进程的内存态——CLI 结构上伸不进去。所以 `issue --reset` 只换钥与密码,**不动已建会话**;要立刻掐断,唯一手段是**重启服务器**(重启即清空会话表)。不要以为 `--reset` 顺带清了会话。

## 加密信道(登录后一切流量:ws 与 REST 同构)

登录后每条消息(ws 帧、REST 请求/响应)都是同一个信封,用会话密钥加密+认证(0057)。密钥派生自登录下发的 `session_token`,该 token 永不再上线:

```
enc_key = KDF_sm3(session_token + b"\x01", 16)   # ws SM4 128-bit
mac_key = KDF_sm3(session_token + b"\x02", 32)   # ws HMAC-SM3
# REST 另派一对(info b"\x03"/b"\x04",derive_rest_keys):与 ws 分域,跨信道重放 MAC 必败(0062)
```

不逐连接派 `server_nonce`:REST 无连接上下文,必须「查会话即解」。跨重连的重放由按会话计的 seq 挡(见下)。

### 信封格式

出入站对称。seq 的计法分信道:ws 各方向各自计数,REST 响应回显请求 seq(见下 / 0062)。

```
报文 = selector ‖ iv(16B) ‖ ct ‖ mac(32B)
  selector = session_id                                   # 公开句柄;服务器据此查会话取 enc/mac 密钥 + 身份
  ct  = sm4_cbc_enc(enc_key, iv, seq(8B,BE) ‖ plaintext_json)   # seq 藏密文内(保密 + 被 MAC 罩住)
  mac = hmac_sm3(mac_key, iv ‖ ct)                        # encrypt-then-MAC(mac 不盖 selector:错 selector→错密钥→MAC 必败)
```

### 入站铁序(必须照此)

1. 读 `selector`,查会话取 `enc_key`/`mac_key`/`user`。查不到或已过期 → 拒。
2. 验 MAC,用 `compare_digest` 常量时间比对。
3. 才 `sm4_cbc_dec` 解密,取出 `seq ‖ plaintext_json`。
4. 验 seq 新鲜:ws 要求 `seq > 本会话已见`,严格单调;REST 用滑动窗判重。
5. `plaintext` 当 wire `ClientMessage` 解,身份取会话里的 `user`,交业务:`to_command(origin=nick)` → inbox,或 REST handler。

任一步失败:丢弃,ws 关连接、REST 拒。

**MAC 必须在解密前验**:库的去填充没有防护,解密未验证的数据有 padding-oracle 风险。

> seq 为什么放密文内、解密后才验:内网无 DDoS 顾虑,不需解密前就廉价拒重放;seq 进密文更保密,且被 MAC 罩住改不了。重放的真包 MAC 能过,但 seq 不新鲜,在第 4 步被拒。
>
> seq 按会话计,不按连接:跨重连的旧包 seq 不新即被挡。进程重启会清空会话表 → 重登换钥,seq 从头也安全。

### MAC 为什么用 HMAC

`hmac_sm3` 是带密钥的 MAC:key 证明持钥(即认证),msg 防篡改。裸 `sm3(msg)` 无 key,谁都能算,故用 HMAC(标准 ipad/opad 构造,顺带避开裸 SM3 的长度扩展问题)。

逐帧原语随 [0054](refactor/changes/0054-p5-secure-frame-channel.md) 落地:`hmac_sm3`、`derive_keys(session_token)`、`SecureChannel`(`derive(token,max)`/`seal`/`open`)。随 [0058](refactor/changes/0058-p5-session-channel-rework.md) 改成本信封与顺序:逐会话密钥、去 `server_nonce`、seq 入 ct、`MAC→decrypt→seq`、mac 盖 `iv‖ct`。

### ws 接线([0061](refactor/changes/0061-p5-ws-secure-channel-wiring.md))

- 登录拿到 `{session_id, session_token}` 后,以 `/ws?sid=<session_id>` 连接;握手查会话 → get-or-derive 一个挂在 Session 上的 `SecureChannel`,它逐会话、跨重连复用,所以 seq 连续,`Connection.channel` 引用该 channel。
- Receiver 收二进制帧走 `open`,Sender 用 `seal` 出站;core/reduce 全程不知有加密。
- ws 的 selector 落在握手 URL 的 `?sid=`,逐帧省略——连接已绑会话,`open` 只收 `iv‖ct‖mac`。REST 无连接上下文,才逐请求带 selector,落地形是 JSON `{sid, frame}`,其中 `frame`=hex(iv‖ct‖mac)。上面「报文=selector‖iv‖ct‖mac」是概念形,ws 帧不含 selector。
- 客户端契约:同一会话跨重连须保留 ws seq,仅新登录换会话才重置。否则重连首帧 seq 回退,会被 `stale_seq` 拒。
- 另:dev 明文 `?nick=` 端点**已于 [0086](refactor/changes/0086-retire-plaintext-endpoint.md) 退役**(前端加解密落地即满足既定退役条件);`/ws?sid=` 是唯一 ws 入口。

### REST 信封([0062](refactor/changes/0062-p5-rest-envelope-user-me.md))

与 ws 有四点分化。

**一、密钥分域**

- REST 用 `derive_rest_keys(session_token)`,info 为 `0x03/0x04`;ws 用 `0x01/0x02`。
- 四把密钥互异,截获的信封跨信道注入时 MAC 必败。副产品:两边 seq 空间天然独立,客户端各自计数。

**二、wire 形(hex JSON,同 login)**

- 请求:`POST` body `{sid, frame}`,内层明文 = 端点参数 JSON,无参时为 `{}`;响应:`{frame}`。
- 原语复用无状态的 `seal_envelope`/`open_envelope`。`SecureChannel.seal/open` 就是委托给它们,再加上 ws 的严格单调策略。

**三、防重放 = 每会话滑动窗**

- 实现是 `ReplayWindow`(IPsec 式),挂在 `Session.rest_window`,宽度 `REST_REPLAY_WINDOW`;规则是 `seq > top` 则推进,窗内未见过的乱序迟到包收下,重复或太旧的拒。
- 为什么不用 ws 的严格单调:REST 并发请求可能乱序到达,严格单调会误拒(0057 决策 3 落地)。
- 客户端重试规则:重试 = 新请求,要重封新 seq。原帧重投必被窗判重 → 401,因为服务器无从分辨重试与重放,只能 fail-closed;因此别把 401 当「需重登」的唯一信号,先用新 seq 重试一次。

**四、响应 seq 回显请求 seq(请求-响应绑定)**

- 不设第二个服务器出站计数器。客户端验「seq == 我发的」即完成绑定:请求 seq 严格递增不复用,旧响应答不了任何后续请求。
- 此处偏离 0057「各方向各自计数」,其 §4 本就注明细节由实现定。
- 已知可接受面:请求与响应共用同一对 REST 密钥,把请求帧原样反射回去能过 MAC+seq;但内层按响应形状解析必败,攻击者读不到任何东西。要根治需再分收发两对密钥,本规模不值。

另两条 REST 规则:

- **错误两段式**:信封任何一步不过 → 统一 401 fail-closed,包括 sid 不识或过期、hex/结构/MAC/解密坏、seq 重放、内层非 JSON 对象;信封验过之后的失败(DB 错等)→ 明文 500,无 body 细节,此时已认证,客户端不必重登。
- **覆盖面:全覆盖,`POST /user/login` 是唯一暴露在外的入口(0094 兑现 0057 的定案)**。除登录外的每一个 REST 端点都走信封,ws 亦然;「解密即认证」⇒ 未登录者一个字节也拿不到。
  - 0062 曾把它收窄成「只有需身份的端点走信封,公开读(lobby/rooms、leaderboard、hands)留明文」,理由是「三者无隐私」。**那个理由不成立**:`/hands` 是逐手财务流水、还能 `?user=` 点名查任何人,而威胁模型第一条要防的正是「嗅探读消息」;更根本的是**没有 TLS**,信封是这套架构里唯一的传输保护,留明文读等于开洞。0094 已收编,本条口径随之改实。

### 身份 = 被认证的会话,不是自报的 `selector`

selector 只是查密钥的公开句柄;真正的认证是「用该会话密钥解出且 MAC 验过」。报文里若还带自报 id,只能放密文内,并由服务器校验它等于会话身份(纵深防御),绝不信明文。

## 与新架构的衔接(加解密是 shell 的事)

- 加解密在 ws 边界,core 只见明文:入站 Receiver 收帧 → 验+解 → 明文 `ClientMessage` → `Command`;出站 Sender 取 `Event` 的 `ServerMessage` → 序列化 → 加密成帧 → `ws.send`。core/reduce 不知道有加密,就像不知道 JSON 序列化(守分层,不变量 1)。
- 会话密钥与序号是 per-session 的 shell 状态,挂在会话表项 / `SecureChannel` 上,绝不进 `world`。同 [timer.md](timer.md) 的「时间戳只活在 shell」:非确定的外部状态进 core 就破坏确定性。
- 握手 → Connect:验完会话拿到 `nickname`,投 `Connect(nick)` 接入大厅(模型 2,进房 / 载入积分在 `JoinRoom`,见 [lobby.md](lobby.md));鉴权失败在握手阶段用 ws 关闭码拒掉,不接入、不建 `Connection`。
- 鉴权字段不进 `UserState`:`hash_password`/`K_user`/`session_token` 都是 DB/shell 的事,`world.users` 只放游戏权威字段(见 [user.md](user.md))。

## 残余风险与红线

- **密钥分发**:`K_user` 必须带外安全送达,不走同一条裸 ws/http;泄露就轮换。
- **前向保密只到「轮换窗口」粒度**:单把 `session_token` 泄露,只暴露它那一个 `SESSION_TTL` 窗口的流量;但 `K_user` 泄露是全损——能派生任意未来会话、解登录响应,直到 `K_user` 轮换。所以 `K_user` 是真正要守住的根;积分非货币,这个边界可接受。
- **实现正确性**(最易出错处):IV 每帧新鲜随机,不复用,也不用计数器当 IV;MAC 先验后解,且常量时间比对;seq 新鲜性按各信道规则;`session_token` 只留客户端内存,不落 URL / 日志 / storage;token 设 `exp` 并可吊销。
- **脱敏**:`K_user`/`token`/`password` 任何级别都不进日志。并入 [log.md](log.md) 的红线,与 `hole_cards`/`deck` 同级。
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

> 各字段随其消费方一起落地,不预铺无消费者的配置。已进 `gameconfig` + `poker.env.example` 的有:`PWD_HASH_ROUNDS`([0053](refactor/changes/0053-p5-password-hashing.md))、`WS_FRAME_MAX_BYTES`([0054](refactor/changes/0054-p5-secure-frame-channel.md))、`SESSION_TTL_SECONDS`([0055](refactor/changes/0055-p5-session-store.md))。

## 待办 / 可选升级

- ~~每用户密钥的下发与轮换工具(管理员侧)~~:已落地 [0066](refactor/changes/0066-p5-kuser-rotation.md) —— [`scripts/kuser_admin.py`](../scripts/kuser_admin.py) 的 `issue` / `rotate` / `list`,见 §K_user 每周轮换。
- REST 也走加密信封:查手牌/余额/排行的请求响应体套 §加密信道 的 `selector‖iv‖ct‖mac`,与 ws 同一把会话密钥(见 [changes/0057](refactor/changes/0057-p5-unified-encrypted-channel-design.md))。
- SM2 升级路径(可选):改用 SM2 做密钥交换(服务器持私钥、前端内置公钥)协商会话密钥,可去掉带外分发,但多一套握手。当前手输密钥方案已够本规模。
- wss 才是终局:若日后能上反代(Caddy 自动证书几乎零配置),应用层加密整套可拆除,登录走 HTTPS、ws 用标准 JWT 即可。
