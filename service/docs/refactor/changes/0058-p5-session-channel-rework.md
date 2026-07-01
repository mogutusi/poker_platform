# 0058 · P5 逐会话信道改造(统一信封:iv‖ct‖mac、seq 入 ct、MAC→解密→seq)

日期:2026-07-01 · 范围:`app/auth/channel.py`(改 `derive_keys`/`SecureChannel`)、`tests/crypto/test_channel.py`(重写)、`docs/auth.md`(§加密信道 mac 覆盖 + 0058 落地)、`docs/refactor/changes/0057-*`(细化 mac 行)、`docs/refactor/TODO.md`。落地 [changes/0057](0057-p5-unified-encrypted-channel-design.md) 定的统一信封的**加解密内核**(把 0054 的逐连接帧改成逐会话信封)。

## 背景 / 为什么

[0054](0054-p5-secure-frame-channel.md) 的 `SecureChannel` 是**逐连接**帧(`seq‖iv‖ct‖mac`、逐连接 `server_nonce` 派生、入站序 `seq→MAC→decrypt`)。[0057](0057-p5-unified-encrypted-channel-design.md) 定案改**逐会话统一信封**(ws + REST 同构)。本砖把加解密内核改到位;selector 剥离/查会话/Receiver·Sender·REST 接线走后续砖。

## 关键设计决策

1. **信封 `iv‖ct‖mac`,seq 藏 ct 内首 8B**(0057):`ct = sm4_cbc_enc(enc_key, iv, seq(8B,BE)‖plaintext)`。seq 进密文 = 保密 + 被 MAC 罩住改不了。
2. **入站铁序 `MAC→解密→seq`**(0057,用户定):① 结构(长度/ct 16 整除)→ ② 验 MAC(常量时间)→ ③ 解密 → ④ 取出 seq 验 `> 本会话已见`。**MAC 仍在解密前**(避 padding-oracle,不松);seq 移到解密后(内网无 DDoS 顾虑,不必解密前廉价拒重放)。`_in_seq` 仅全通过后推进。
3. **`mac` 只盖 `iv‖ct`,不含 selector**(细化 0057 的 `selector‖iv‖ct`):selector 是**传输层**的密钥查找提示,不进本原语——错 selector → 查到错会话/错密钥 → MAC 必败,故 selector 完整性隐式受保护,无需纳入 MAC。好处:`SecureChannel` **传输无关**(不认识 selector 字符串),ws/REST 各自剥 selector 后调 `open(iv‖ct‖mac)`。安全等价(换 selector 只会导致解不开,不构成攻击)。
4. **密钥由会话密钥直接派生,去 `server_nonce`**:`derive_keys(session_token)`(enc=KDF(token+0x01,16)、mac=KDF(token+0x02,32),info 域分隔)。REST 无连接上下文须「查会话即解」,故不能逐连接派生。跨重连重放改由**按会话计的 seq** 挡(旧包 seq 不新);进程重启会话表清空 → 会话失效 → 重登换钥,seq 从头也安全。
5. **seq 按会话、严格单调**(`open` 拒 `seq ≤ 已见`)。**REST 并发的 seq**(同会话 ws+REST 共一计数会误拒乱序请求)属**接线砖**决策(滑动窗 / 只读接受重放,见 0057),本原语保持严格单调不变。
6. **fail-closed 不变**:结构坏/MAC 坏/解密坏/seq 旧一律 `FrameError(reason)`;新增 `bad_plaintext`(解密后 inner < 8B 无 seq,防御,认证过的帧不应触发)。`decrypt_failed`/`bad_plaintext` 是 MAC 过后的防御归一,正常不可达、不强测。

## 打算改什么

- `app/auth/channel.py`:`derive_keys(session_token)` 去 nonce;`SecureChannel.derive(token, max)` 去 nonce;`seal`→ `iv‖ct‖mac`(seq 入 ct);`open`→ 结构→MAC→解密→seq;`_FRAME_MIN_BYTES = iv+block+mac = 64`(seq 不再占明文头);`FrameError` 加 `bad_plaintext`。
- `tests/crypto/test_channel.py`:重写贴新信封/顺序——derive_keys(token) 形/确定/跨 token 异;封拆 round-trip;MAC 拒伪(改 iv/ct/mac);先验后解(改 ct → bad_mac 非 decrypt/seq);重放(重投 → stale_seq,现在解密后);gap/乱序;跨会话(异 token → bad_mac);结构(过短<64/过长/ct 非 16 整除);IV 每帧新鲜;fuzz 不崩。
- docs:auth.md §加密信道 mac 行 `selector‖iv‖ct`→`iv‖ct` + 注 selector 传输层 + 「0054 调整中」→「0058 落地」;0057 envelope mac 行同步细化;TODO 逐帧项标 0058。

## 自 review

对照 review.md 逐维 + **对抗式多智能体复审(2 lens finder × 反驳验证者,后端已恢复跑完)**:**0 真 findings**(唯一 confirmed 是本段当时占位,即此刻补)。逐维:

- **① 分层 / 不变量**:`channel.py` 纯计算(无 async/IO/DB/日志);`SecureChannel` 每会话状态(密钥+双向 seq)不进 world;`FrameError` raise 在 shell/auth 边界合规(非 core reduce)。
- **② 代码↔文档同步**:auth.md §加密信道 的信封(`iv‖ct‖mac`、seq 入 ct)+ 铁序(`结构→MAC→解密→seq`)+ mac 盖 `iv‖ct` + `derive_keys(session_token)` 去 nonce,与代码逐条对齐;0057 mac 行细化、TODO 逐帧项标 0058。
- **③ 文档↔文档一致**:0058 ↔ 0057 ↔ auth.md ↔ TODO ↔ 代码一致;计数 24/554 三处同步;链指向存在文件。
- **④ 数据模型**:`_FRAME_MIN_BYTES = iv(16)+block(16)+mac(32) = 64`(seq 移入 ct 后不再占明文头);seq 8B、mac 32B、iv 16B 常量自洽;空明文 → inner=8B → 补一整块 → ct=16 → 帧 64 = min(实测 round-trip)。
- **⑤ 规范合规**:具名常量齐(去掉明文头 seq 后 `_FRAME_MIN` 重算);`compare_digest` 常量时间;`FrameError.reason` 只带分类不带密文;中文注释讲「为什么」(mac 不盖 selector 之因 / seq 入 ct / MAC 先于解密);无死代码。
- **⑥ 测试充分**:24 测——hmac 性质 / derive(会话密钥·域分隔·跨 token 异)/ round-trip(含空)/ seq 经 open 观察严格递增 / IV 每帧新鲜 / MAC 拒伪(iv·ct·mac)/ **先验后解(改 ct → bad_mac 非 decrypt/seq,证 MAC 先于解密)** / 重放 stale_seq / gap 后旧帧 / 失败不推进 seq / 跨会话 bad_mac / 结构(短<64·大·ct 非 16 整除)/ config 接线 / **fuzz(2000 随机 + 真帧逐位变异 → 全 FrameError、0 崩)**。未测 `decrypt_failed`/`bad_plaintext`(MAC 过后不可达,防御归一)。
- **⑦ 流程账本**:打算↔实际一致;TODO 更新;提交引用 0058、全英文。

**对抗核实(crux)**:逐行确认 `open`:结构 → **验 MAC(75-77)** → **才解密(79-82)** → inner<8 防御 → **seq(85-87)** → 仅成功推进 `_in_seq`(88)。MAC 严格先于解密(padding-oracle 不成立);seq 移到解密后但重放仍被 stale_seq 挡(真包 MAC 过、seq ≤ 已见即拒);mac 盖 `iv‖ct` 不含 selector 安全等价(错 selector→错密钥→MAC 败)。0 真 bug。

## 待办 / 下一步

- **登录端点砖**:`/user/login`(authenticate → SessionStore.create → `K_user` 加密下发 `{session_id, session_token, exp}`)。
- **接线砖**:Receiver 剥 selector → SessionStore 查 → `SecureChannel.open` → to_command;Sender `seal`;REST 信封中间件(+ REST 并发 seq 策略);dev 种子带 name/密码/K_user;SessionStore + 会话信道注册表挂 lifespan。
