# 0057 · P5 统一加密信道设计定案(设计讨论记录,去 JWT)

日期:2026-07-01 · 类型:**设计讨论 + 定案**(非代码变更;据此重排后续实现砖)。范围文档:`docs/auth.md`(§定位/§token 层级/§登录握手/§WS 安全信道/§待办 改写)、`docs/refactor/TODO.md`(P5 重排)。触发:用户指出现设计(auth.md「REST 走明文 JWT、WS 走逐连接握手」)与其真实设计不符。

## 用户的设计(权威,本篇据此改文档)

> 「先通过服务器每周刷新一次的 K,来加密用户账密从而登陆;登陆完成下发本次会话的密钥(有有效期、每个用户不一样);登陆完成后无论是 ws 还是别的,都要通过密钥加密。用户除了登陆,其他和服务端的数据沟通(包括 ws)都是『用户名、加密数据』这样的格式,后端拿到后解密,然后拿到 user。」

拆成两段:

1. **登录引导**:每用户一把 `K_user`(每周轮换)加密账号+密码登录。**与现设计一致**(0056 `authenticate` 已落地)。
2. **登录后一切流量(ws + REST/别的)走同一个加密信封**,用登录下发的**会话密钥**(per-user、带 exp)加解密。**解密成功即认证**——身份从解密结果得出,**不需要独立身份令牌(JWT)**。这是与现设计的分歧点。

## 定案(讨论结论)

### 1. 去掉 JWT

现 auth.md 把 REST 认证挂在「沿用原型 JWT」上——但原型 0027 已拆、新架构无 JWT,且用户设计里身份从解密得出,**JWT 冗余**。**删除 JWT 通道**(已删未提交的 0057 JWT 助手实现:`tokens.py`/`test_tokens.py`)。REST 与 ws 统一走加密信封。

### 2. 统一信封(登录后每条消息,ws 与 REST 同构)

```
报文 = selector ‖ iv(16B) ‖ ct ‖ mac(32B)          # 明文头只有 selector + iv
  ct  = sm4_cbc_enc(enc_key, iv, seq(8B,BE) ‖ 明文JSON)   # seq 藏在密文里(保密 + 被 MAC 罩住)
  mac = hmac_sm3(mac_key, selector ‖ iv ‖ ct)             # encrypt-then-MAC
```

- **selector = `session_id`**(登录响应里已下发的公开不透明句柄;用它而非用户 id,嗅探者看不出「谁在线/谁在说话」)。服务器 `session_id → 会话(enc_key/mac_key/user)`。
- `enc_key`/`mac_key` 由**会话密钥直接派生**(`KDF_sm3(session_token,…)`),**不再逐连接派生**(去掉旧设计的 per-connection `server_nonce` 握手)——因为要支持无连接上下文的 REST「查表即解」。
- **HMAC 两输入**:key(=mac_key,认证)+ message(=`selector‖iv‖ct`,防篡改)。裸 `sm3` 无 key 谁都能算,故用 HMAC。

### 3. 入站铁序(用户定,采纳):MAC → 解密 → seq

```
① 读 selector → SessionStore 查会话 → 取 enc_key/mac_key/user(查不到/过期 → 拒)
② 验 MAC(hmac_sm3 常量时间比)—— 不过则拒。绝不先解密后验(避 padding-oracle,库去填充是裸的)
③ sm4_cbc_dec 解密 → seq(8B) ‖ 明文JSON
④ 验 seq > 本会话已见(防重放)—— 不过则拒;过则推进已见
⑤ 明文JSON = wire ClientMessage;user = 会话身份 → 交业务(to_command(origin=nick) → inbox / REST handler)
```

- **seq 放进 ct、解密后验**:无 DDoS 顾虑(内网≤20),不必在解密前廉价拒重放;seq 进密文更保密、且被 MAC 罩住改不了。重放的真包 MAC 能过但 seq ≤ 已见 → ④ 拒。
- **seq 按会话计**(不按连接):服务器每会话记 last_seen_seq。ws 天然有序;跨重连重放被 seq 挡(旧包 seq 不新);进程重启会话表清空 → 会话失效 → 重登换新会话密钥,seq 从头也安全。
- **REST 并发**:同会话 ws 与 REST 若共享一个 seq 计数,并发/乱序请求可能误拒。**决策(可改)**:REST 用「每会话滑动窗重放缓存」(记最近 N 个 seq/nonce、只拒精确重复)而非严格单调,或对只读查询接受重放(幂等)。ws 仍用严格单调 seq。实现砖细化。

### 4. 出站对称

服务器→客户端同构信封:`selector ‖ iv ‖ ct ‖ mac`,各方向各自 seq 计数。(selector 出站可省——同一连接/会话已知;细节实现砖定。)

## 对已落地砖的影响

| 砖 | 影响 |
|---|---|
| 0053 密码哈希 / 0056 authenticate+列 | 不变(登录引导那步) |
| 0055 会话表 | 基本不变(发/查会话密钥 + user);selector=session_id 正是它的公开句柄 |
| 0054 逐帧信道 | **加解密内核复用**(encrypt-then-MAC / MAC 先于解密 / hmac_sm3 / seq 防重放);**要改**:①信封头 `seq‖iv‖ct‖mac` → `selector‖iv‖ct‖mac`(seq 移入 ct);②入站序 `seq→MAC→decrypt` → `MAC→decrypt→seq`;③去 per-connection `server_nonce` 派生,密钥来自会话密钥;④seq 按会话计 |
| 0057 JWT | **删除**(设计里不需要) |

## 待办 / 下一步(重排 P5)

- **改文档**(本篇即改):auth.md 上述 5 节 + TODO。
- **实现砖**:①按新设计改 `SecureChannel`(信封/顺序/密钥来源/seq 作用域)②`/user/login` 端点(authenticate → SessionStore.create → 用 K_user 加密下发 {session_id, session_token, exp})③Receiver 解密路(收信封 → 查会话 → MAC→decrypt→seq → to_command)④Sender 加密路 ⑤REST 信封中间件 ⑥dev 种子带 name/密码/K_user + SessionStore 挂 lifespan ⑦K_user 每周轮换。
- 配置:`SESSION_TTL_SECONDS`(0055 已加)、会话密钥派生参数、`JWT_*` **不再需要**。
