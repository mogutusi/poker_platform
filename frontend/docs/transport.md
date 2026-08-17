# 传输层(transport/)

一句话:登录换回会话凭证,之后 ws 和 REST 的每一条消息都套同一个加密信封。

后端出处:[auth.md](../../service/docs/auth.md) §登录握手 / §加密信道 / §入站铁序。

## 一、登录握手

```
POST /user/login
body = { name, iv, blob }
  iv   = 16 字节随机,hex
  blob = sm4CbcEncrypt(K_user, iv, JSON{password, client_nonce, ts})，hex
       ts = 客户端墙钟秒数;client_nonce = 每次登录新随机

响应 = sm4CbcEncrypt(K_user, iv2, JSON{session_id, session_token, exp, rotate})
```

要点:

- **`K_user` 是带外发放的每用户共享密钥,每周轮换**,不是密码派生出来的。前端必须让用户能填它,并缓存在本地(见下「秘密怎么存」)。没有 `K_user` 就登不上。
- 服务器先试 `k_cur`,失败再试宽限期内的 `k_prev`。若响应里 `rotate === true`,说明**在用旧钥**,应提示用户尽快向管理员换新钥。
- 失败一律 401,**不区分**账号不存在 / 密码错 / blob 坏。所以前端也只能提示「登录失败」,不要猜测原因误导用户。
- 重放守卫:`ts` 偏离服务器时间太多会被拒;`(name, client_nonce)` 重复也会被拒。所以**每次登录都要新的 `client_nonce` 和当前 `ts`**,不能复用上次的请求体重发。

## 二、加密信封

登录拿到 `session_token` 后,由它派生两对密钥。**ws 与 REST 分域**,互相不能用:

```
ws:    encKey = kdfSm3(session_token ‖ 0x01, 16)    macKey = kdfSm3(session_token ‖ 0x02, 32)
REST:  encKey = kdfSm3(session_token ‖ 0x03, 16)    macKey = kdfSm3(session_token ‖ 0x04, 32)
```

分域的目的是:截获的 REST 信封注入 ws(或反向)必然 MAC 失败。

信封格式:

```
iv(16B) ‖ ct ‖ mac(32B)
  ct  = sm4CbcEncrypt(encKey, iv, seq(8B 大端) ‖ JSON明文)
  mac = hmacSm3(macKey, iv ‖ ct)
```

- **seq 藏在密文里**,不在外面,所以它既保密又被 MAC 罩住。
- **mac 只盖 `iv‖ct`,不盖 sid**。错的 sid 会导致服务器取到错的密钥,MAC 自然失败,不需要额外盖。
- 每帧 `iv` 都要新鲜随机,不许复用。

### 收帧必须照这个顺序

1. 验 MAC(常量时间比较)。
2. 才解密。
3. 再验 seq 新鲜。
4. 最后当 JSON 解析。

**MAC 一定在解密之前验。** 去填充没有防护,对未验证的密文解密有 padding-oracle 风险。这个顺序在后端叫「入站铁序」,前端收服务器帧时同样照办。

任何一步失败就丢弃这一帧。

## 三、seq 纪律(最容易踩的坑)

- **seq 按会话计,不按连接计。** ws 断线重连后,seq 要**接着原来的数往上加**,不能从 0 重来——重连首帧 seq 回退会被服务器判 `stale_seq` 拒掉,然后连接被关,陷入重连死循环。
- 只有**重新登录换了会话**才把 seq 归零。
- 收发各自计数:发送用自己的计数器,校验服务器帧用「收到的 seq 必须大于已见的最大值」。
- REST 不同:响应回显请求的 seq,服务器用滑动窗判重,允许乱序到达。**REST 重试必须重新封一个新 seq**,原样重发会被判成重放。

## 四、ws 连接

```
ws://<host>/ws?sid=<session_id>
```

- `sid` 是公开句柄,放 URL 没问题;`session_token` 是秘密,**绝不上线**。
- 帧是二进制,`socket.binaryType = "arraybuffer"`,收发都用 `ArrayBuffer`,不用文本帧。
- ws 帧里**不含 sid**(连接已经绑定会话了),只有 `iv‖ct‖mac`。REST 才需要逐请求带 sid。
- 服务器主动关连接、关闭码 `4401`,意思是鉴权/信封出了问题,应引导用户重新登录,而不是自动重连。

### 重连

- 网络断开是常态,自动重连,退避重试。
- 重连成功后服务器会私发一份 `StateSnapshot`,前端整份替换本地状态即可,**不需要自己补状态**。
- 同一账号在别处登录会「顶替」当前连接:旧连接被静默关掉。这时不要自动重连去抢,提示用户「账号在别处登录」。

## 五、REST 信封

需要身份的端点(`/user/me`、`/user/password`、`/user/nickname`):

```
POST <endpoint>
body = { sid, frame }         frame = hex(iv ‖ ct ‖ mac)，用 REST 密钥
响应 = { frame }
```

公开读(`/lobby/rooms`、`/leaderboard`、`/hands`)**是明文的**,直接 GET,不套信封。

错误分层(见 [rest.md](../../service/docs/rest.md)):信封本身不过 → 401;业务错 → 403/409/400;服务端故障 → 500。**401 不等于「该重登」**——REST 重试若原样重发也会得 401,先用新 seq 重试一次再判断。

## 六、秘密怎么存

| 东西 | 存哪 | 理由 |
|---|---|---|
| `K_user` | `localStorage`,用户可清 | 每周轮换,要跨会话留存,否则每次登录都要手输 |
| `session_token` | 内存(模块变量),**不进 localStorage** | 它能派生所有帧密钥,泄露等于会话被接管;进程刷新就重登 |
| `session_id` | 内存即可 | 公开句柄,但没必要留存 |

刷新页面会丢 `session_token`,需要重新登录。这是有意的取舍:把最敏感的东西留在内存里。若后续觉得体验不可接受,再讨论用 `sessionStorage` 换取便利,但要在文档里记清风险。

> 合并进来的 UI 目前往 `localStorage` 写了 `auth_token` 和 `player_name`(0076),那是 mock 阶段的写法,接线时替换成上表。
