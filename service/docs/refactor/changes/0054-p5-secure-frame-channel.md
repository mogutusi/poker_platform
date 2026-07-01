# 0054 · P5 逐帧安全信道原语(HMAC-SM3 + 派生密钥 + encrypt-then-MAC 帧封/拆)

日期:2026-07-01 · 范围:`app/auth/channel.py`(新,`hmac_sm3`/`derive_keys`/`FrameError`/`SecureChannel`)、`app/gameconfig.py`(加 `WS_FRAME_MAX_BYTES`)、`app/poker.env.example`(加 `WS_FRAME_MAX_BYTES`)、`tests/crypto/test_channel.py`(新)、`tests/test_gameconfig.py`(补新字段)、`docs/auth.md`(§WS 安全信道 精化为落地签名 + 序号起点)、`docs/refactor/TODO.md`(P5 逐帧加密项)。承 [0053](0053-p5-password-hashing.md) 继续 P5,仍走「纯原语先行、再接 IO」。

## 背景 / 为什么

P5 第三项「逐帧加密 `SecureChannel`」。auth.md §WS 安全信道 定的逐帧格式与**入站校验铁序**是本项目最易翻车的加解密面(库的 `sm4_cbc_dec` 去填充是**裸的**——读末字节即当填充长度、不校验,解未验密文有 padding-oracle 风险)。故先把**纯原语**钉死、穷举测,再由后续砖接进 Receiver/Sender。

**为何在「登录握手」(P5 第二项)之前做逐帧原语**(偏离 TODO 顺序,依 README §0 当场改序 + 论证):帧原语是 `(enc_key, mac_key, seq, iv, plaintext)` 的**纯函数**,与「密钥从哪来」无关;登录握手负责铸 `session_token` + 每连接 `server_nonce`,但那是 IO/DB/端点层。**自底向上**先落可脱离 DB/端点穷举的纯密码原语(同 0053 密码原语先于登录端点),把最高风险的加解密正确性脱离握手先验证。握手砖落地后,只需 `SecureChannel.derive(token, nonce, max)` 起本连接信道即可。

## 关键设计决策

1. **`SecureChannel` 类持每连接 shell 状态**(auth.md:密钥 + 双向序号是 per-connection shell 状态,**绝不进 world**——非确定外部态进 core 破确定性,同 timer.md 墙钟)。字段:`_enc_key`(16B SM4)、`_mac_key`(32B HMAC)、`_max_frame_bytes`、`_out_seq`、`_in_seq`。`seal`/`open` 是仅有的两个操作;`derive` 类方法从 `(session_token, server_nonce, max)` 起。除 IV 随机(secrets)外确定,可穷举测。
2. **入站铁序 `open`(绝不先解密后验)**:① 帧长上/下限 + ct 段长为正且 16 整除(结构)→ ② `seq > _in_seq`(防重放,严格递增)→ ③ 重算 HMAC-SM3、`hmac.compare_digest` 常量时间比(防篡改)→ ④ **才** `sm4_cbc_dec` 解密。任一步失败 `raise FrameError(reason)`,Receiver 捕获 → 丢帧 + 关连接(后续砖)。`_in_seq` **仅全通过后推进**(失败不动计数)。
3. **`FrameError` 用异常而非返回值**:这是 shell/auth 层的帧解析边界(非 core reduce——core 禁 raise),与既有 `ClientMessage.parse` 坏 JSON 抛异常、Receiver 捕获回 `INVALID_MESSAGE` 同构。`reason`(`frame_too_large`/`frame_too_short`/`bad_ct_length`/`stale_seq`/`bad_mac`/`decrypt_failed`)供日志定位——**绝不带明文/密钥/密文**(log.md 脱敏红线)。
4. **encrypt-then-MAC,MAC 盖 `seq‖iv‖ct`**:出站 `seq(8B BE)‖iv(16B)‖ct‖mac(32B)`。**IV 每帧新鲜随机**(`secrets.token_bytes(16)`,非计数器——auth.md 红线),故同明文两封 IV/ct 皆异。`seal` 内部生成 IV,调用方无从复用。
5. **序号每连接从 1 起、严格递增**(轻微偏离 auth.md 散文「从 0」):`_out_seq`/`_in_seq` 初始 0,`seal` 先 `+=1` → 首帧 seq=1;`open` 收 `seq > _in_seq` → 首帧需 ≥1。避免 -1 哨兵、更干净;安全性只系于「每连接重置 + 严格递增 + 每连接密钥」,首值 0/1 无关。同步精化 auth.md 该句(不再钉 0)。
6. **派生密钥域分隔**:`enc_key = KDF_sm3(token+nonce+b"\x01", 16)`、`mac_key = KDF_sm3(token+nonce+b"\x02", 32)`。info 字节 0x01/0x02 使两钥输入不同 → 互不可导。`server_nonce` 每连接新随机(握手砖发)→ 逐连接密钥不同 → **上一条连接抓到的帧在新连接 MAC 必失败**,跨重连重放被根除(auth.md 关键点)。
7. **`hmac_sm3` 用标准 HMAC 构造**(auth.md 给的实现):`H(opad‖H(ipad‖msg))`,块 64B,避开裸 SM3 长度扩展。mac_key 32B < 64 → ljust 补 0 到块长。
8. **`WS_FRAME_MAX_BYTES` 进 gameconfig**(auth.md §配置,`Field(ge=256, le=1048576)`;env 值 65536=64KB,wire JSON 小帧足够)。`open` 先查上限拒超大帧(防解析放大)。`SESSION_TTL_SECONDS` 属握手砖,本批不加。
9. **`decrypt_failed` 是防御归一(正常不可达)**:MAC 过 ⇒ ct 真实 ⇒ 由我方 PKCS#7 封,`sm4_cbc_dec` 不抛;仅当库对边缘输入抛时归一为 `FrameError` 免崩 Receiver。结构校验(ct 段 16 整除)已挡住会让 `frombuffer` 抛的非对齐 ct。**故不强测该分支**(无法在 MAC 过的真实帧上触发)。

## 打算改什么

- **`app/auth/channel.py`**(新):`hmac_sm3(key,msg)`、`derive_keys(token,nonce)->(enc,mac)`、`FrameError(reason)`、`SecureChannel`(`__init__`/`derive`/`seal`/`open`)+ 具名长度常量(seq/iv/mac/block/frame-min,无裸字面量)。
- **`app/gameconfig.py`** + **`poker.env.example`**:`WS_FRAME_MAX_BYTES`(鉴权段,承 0053 分段)。
- **`tests/test_gameconfig.py`**:`_valid_kwargs` + bounds-reject 补 `WS_FRAME_MAX_BYTES`。
- **`tests/crypto/test_channel.py`**(新):hmac 性质 / 派生密钥(长度·确定·跨 nonce 异·跨 token 异·域分隔)/ 封拆 round-trip(空·小·多块·非对齐)/ seq 单调 / MAC 拒伪(改 ct/iv)/ seq 拒重放(重投·递减·gap 后旧帧)/ 先验后解(篡改 ct → bad_mac 而非 decrypt_failed,证 MAC 先于解密)/ IV 每帧新鲜 / 跨连接重放(A 封 B 拆 → bad_mac)/ 帧过短·过长·ct 非 16 整除 / config 接线。
- **docs**:auth.md §WS 安全信道 精化(落地签名 + 四步铁序 + 序号从 1 + FrameError 语义);§配置 标 WS_FRAME_MAX_BYTES 已落地;TODO P5 逐帧项 `[~]`。

**实际结果**:与「打算」一致,无签名偏离。`tests/crypto/test_channel.py` **23 测**、`tests/test_gameconfig.py` +2 bounds-reject 参数,**全绿 486→511**。另跑**模糊对抗**:20000 条随机字节 blob + 真帧逐字节(每位 ×3 xor,216 变异)喂 `open` —— **全部 `FrameError`、0 崩溃、0 误开**(MAC 覆盖 seq/iv/ct/mac,任一位翻转即拒)。`gen_wire_ts --check` 未碰 wire。

## 自 review

对照 review.md 逐维 + **跑了对抗式多智能体复审**(5 lens finder × 默认反驳验证者;逐行追 `open` 的解密顺序)。**0 真代码 bug**:唯一「confirmed」是本段当时仍是占位(即此刻补),另 1 条「文档步骤编号不符」被**反驳**(代码四步序正确、MAC 先于解密,文档「另在 ① 前加结构校验」措辞略绕已顺手改清)。逐维:

- **① 分层 / 不变量**:`channel.py` 纯计算(无 async/IO/DB/读钟/日志),`FrameError` raise 在 shell/auth 帧边界合规(非 core reduce)。密钥 + 双向序号是 `SecureChannel` 实例状态(per-connection shell 态),**不进 world/core**(同 timer.md 墙钟)。不 import shell/fastapi/sqlalchemy。加 `WS_FRAME_MAX_BYTES` 不破启动单例。
- **② 代码↔文档同步**:auth.md §WS 安全信道 精化为落地签名(`hmac_sm3`/`derive_keys`/`SecureChannel`/`FrameError`)+ 四步铁序 + 序号从 1 + 六个 `reason` 名与代码逐字对齐;§配置 标 WS_FRAME_MAX_BYTES 已落地。
- **③ 文档↔文档一致**:0054 ↔ auth.md ↔ TODO ↔ 代码四处一致;计数 23/511 三处同步。auth.md 新链(channel.py / changes/0054)指向存在文件。全文已无「seq 从 0」残留(散文改为「从头重置,实现从 1 严格递增」)。
- **④ 数据模型**:帧结构 `seq(8B BE)‖iv(16B)‖ct‖mac(32B)` 长度常量自洽(`_FRAME_MIN_BYTES = seq+iv+block+mac = 72`);`WS_FRAME_MAX_BYTES` `Field(ge=256,le=1048576)`,env 值 65536 在界内、注释一致。
- **⑤ 规范合规**:所有裸数(8/16/32/64/72/0x01/0x02/0x36/0x5C)均具名常量或 HMAC 标准掩码(有注释);中文注释讲「为什么」(先验后解避 padding-oracle / IV 每帧新鲜 / server_nonce 逐连接密钥根除重放);类型标注齐;无死代码/print;模块头短注非大 docstring。
- **⑥ 测试充分**:安全性质**逐条被证**——先验后解(篡改 ct → `reason=="bad_mac"` 而非 decrypt_failed)/ 重放(重投 → stale_seq)/ gap 后旧帧拒 / 跨连接重放(异 nonce → bad_mac)/ IV 每帧新鲜 / 失败不推进 `_in_seq` / 结构拒(过短·过长·ct 非 16 整除)/ 域分隔 + 跨 nonce·token 密钥异。**另有模糊对抗**(见上)覆盖「未想到的」畸形帧。**未单测** `decrypt_failed`(决策 9:MAC 过后不可达,防御归一)。
- **⑦ 流程账本**:本篇打算↔实际对照回填;TODO 逐帧项 `[~]` + 计数;提交引用 0054、全英文。

**对抗核实**:crypto lens 逐行确认 `open`:结构(81-87)→ seq>已见(92-94)→ 验 MAC 常量时间(95-97)→ 才解密(98-101)→ 仅成功后推进 `_in_seq`(102);`seal`/`open` 的 MAC 均盖**同一** `seq‖iv‖ct`。无先解密后验、无 padding-oracle、无越界切片、无未捕获崩溃(20216 次对抗输入 0 崩)。

## 待办 / 下一步

- **P5 砖(登录握手)**:`/user/login`(SM4 护密码 + `client_nonce`/`exp` 防重放)、内存会话表、`session_id`/`session_token`、JWT(`sub`=name)、`User.name`/`hash_password`/`K_user` 列 + 迁移、`SESSION_TTL_SECONDS`。
- **P5 砖(信道接线)**:Receiver 握手发 `server_nonce` → `SecureChannel.derive` → 收帧 `open`→明文 `ClientMessage`;Sender `ServerMessage`→序列化→`seal`→`ws.send`;鉴权失败 ws 关闭码拒、绝不建 Connection。
- **P5 砖**:`K_user` 双钥 + 每周轮换 + 版本/宽限。
