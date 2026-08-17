# 国密原语(crypto/)

一句话:后端没有 TLS,自己用国密做了一层加密信道,前端必须实现同一套原语,否则连都连不上。

四个原语,全是纯计算,不碰网络也不碰 React:

| 原语 | 用途 |
|---|---|
| `sm3(bytes) -> 32B` | 哈希;HMAC 和 KDF 的底座 |
| `sm4CbcEncrypt(key16, iv16, data) -> bytes` / `sm4CbcDecrypt` | 对称加解密,CBC 模式,PKCS#7 填充 |
| `hmacSm3(key, msg) -> 32B` | 带密钥的 MAC,认证 + 防篡改 |
| `kdfSm3(input, len) -> bytes` | 密钥派生 |

对应后端 [service/lib/ttxsgm](../../lib/ttxsgm/ttxsgm/) 与 [app/auth/channel.py](../../service/app/auth/channel.py)。

## 精确定义(照抄,不要自己发挥)

**`kdfSm3(input, length)` = `sm3(input).slice(0, length)`**,`length` 上限 32。就这么简单,不是 HKDF。

**`hmacSm3(key, msg)`** 是标准 HMAC 构造,块长 64 字节:

```
if key.length > 64:  key = sm3(key)
key = key 右侧补 0x00 到 64 字节
ipad = key 每字节 ^ 0x36
opad = key 每字节 ^ 0x5C
return sm3(opad ‖ sm3(ipad ‖ msg))
```

**SM4-CBC** 用 PKCS#7 填充:明文补 `n` 个值为 `n` 的字节,`n ∈ [1,16]`;明文已对齐也要补满一整块。

## 验证方式:已知答案向量

[crypto-test-vectors.json](../crypto-test-vectors.json) 由后端 `service/scripts/gen_crypto_vectors.py` 从后端原语直接生成,覆盖:

- `sm3` / `sm4_cbc` / `hmac_sm3` / `kdf` 四组基础向量;
- `ws_frame`:给定 `session_token`,一整帧 `iv‖ct‖mac` 的期望字节;
- `rest_envelope`:REST 信封的期望字节;
- `login_blob`:登录请求与响应的加解密期望值。

**这些向量就是本层的验收标准。** 单测必须逐条比对通过,不许「看起来对了」就算数。原语差一个字节,表现是服务器直接关连接,且没有任何有用的报错——所以必须在这一层就钉死。

向量文件是后端产物,**前端不许改它**;若后端改了原语,重新生成后前端跟着修。

## 注意点

- **字节序**:seq 是 8 字节大端无符号整数。JS 里用 `DataView.setBigUint64(0, v, false)`,别用小端。
- **别用 `number` 装 seq**:超过 2^53 会失精度。用 `bigint`。
- **SM4 的 32 位运算**:JS 位运算是有符号 32 位,移位和异或后要用 `>>> 0` 收回无符号,否则结果会带符号位错。
- **常量时间比较**:验 MAC 用逐字节异或累加后判零,不要用 `===` 提前短路。前端侧的时序攻击面很小,但没理由留下坏习惯。
- **不打日志**:密钥、明文、密文、`session_token` 一律不进 `console`。调试要看,临时加、提交前删(同后端 [log.md](../../service/docs/log.md) 的脱敏红线)。

## 为什么不用现成库

`npm` 上的国密实现质量参差,且我们必须和后端那份 `ttxsgm` **逐字节一致**——版本差异或填充策略差异都会导致 MAC 失败。自己实现 + 用后端生成的向量守门,比赌一个第三方库的行为更可控。代码量也就 SM3 约 60 行、SM4 约 120 行。
