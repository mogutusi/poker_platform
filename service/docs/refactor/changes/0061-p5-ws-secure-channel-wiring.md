# 0061 · P5 ws 安全信道接线(Receiver/Sender 逐帧加解密 + `/ws?sid=` 握手)

日期:2026-07-03 · 范围:`app/auth/session.py`(Session 挂 channel)、`app/shell/connection.py`(Connection 挂 channel)、`app/shell/receiver.py`(入站分流:加密帧 open / 明文帧 parse)、`app/shell/sender.py`(出站分流:seal / 明文)、`app/shell/lifespan.py`(新增 `/ws?sid=` 加密端点 + 会话信道解析)、`tests/shell/_fakes.py`(FakeWS 加 bytes 路)、`tests/shell/test_secure_channel_wiring.py`(新)、`docs/auth.md`/`docs/connection.md`/`docs/wire-protocol-guide.md`/`docs/refactor/TODO.md`。把 0054/0058 的 `SecureChannel` 原语 + 0055 会话表 + 0059/0060 登录闭环**接进真实 ws 收发**——登录后的 ws 流量走加密信封(替 dev `?nick=` 明文,dev 端点保留并存)。

## 背景 / 为什么

P5 至此:密码哈希(0053)、`SecureChannel` 逐会话信封原语(0054/0058)、会话表(0055)、authenticate+列(0056)、登录端点(0059)、dev 种子 login-enable(0060)全部落地,但**加解密原语从未接进 Receiver/Sender**——ws 仍是 `?nick=` 明文握手 + 明文 JSON 帧(dev 脚手架)。本砖落「接线」:登录拿到 `{session_id, session_token}` 后,客户端以 `?sid=<session_id>` 连 ws,此后每帧走 `iv‖ct‖mac` 信封(会话密钥 SM4+HMAC-SM3+seq)。这是 0058/0059/0060「待办·下一步」反复点名的「接线砖(大件)」。**REST 信封中间件 + client_nonce 重放守卫 + K_user 每周轮换**仍是各自后续砖(本砖只做 ws)。

## 关键设计决策

1. **信道挂会话(`Session.channel`),逐会话计 seq——不逐连接**。auth.md/0058 定「seq 按会话计(非按连接)」以挡跨重连重放;若每连接重派信道则 seq 归零、旧帧可重放。故 `SecureChannel` 缓存在 `Session` 上(`derive` 一次、跨重连复用同一实例 → seq 连续),握手时 get-or-derive:`session.channel or SecureChannel.derive(session.token, WS_FRAME_MAX_BYTES)`。connection.md 早已暗示「挂在会话表项 / SecureChannel 上」。`Connection.channel` 指向该会话信道(引用,非各连接独立)。
   - **客户端契约(记入 auth.md / guide)**:客户端须**跨重连保留同一会话的 seq**(仅**新登录**换会话时才重置)——与服务端逐会话计对称,否则重连首帧 seq 回退被服务端 `stale_seq` 拒。
2. **ws 逐帧不带 selector;selector 在握手 `?sid=` 一次性给**。`SecureChannel.open` 收 `iv‖ct‖mac`(0058:selector 传输层剥离)。ws 连接握手时已由 `?sid=` 绑定会话,故**每帧省 selector**(连接上下文已知会话)。REST 无连接上下文,才需逐请求带 selector(REST 信封砖)。auth.md「报文=selector‖iv‖ct‖mac」是**REST/通用**形态;ws 的 selector 落在 URL query、不进帧。
3. **加密路 = 二进制帧**(`receive_bytes`/`send_bytes`);**明文 dev 路 = 文本帧**(`receive_text`/`send_text`)。`Connection.channel is None` ⇒ 明文(dev),非 None ⇒ 加密。Receiver/Sender 据此分流;`dispatch`/`GameLoop`/`reduce` 全程不知有加密(守分层,connection.md 契约 3)。
4. **加密端点并存,不替换 dev 明文**(增量交付,非一刀切)。`/dev/ws?nick=`(明文,dev-only)保留;新增 `/ws?sid=`(加密)。理由:前端联调 + 既有 dev e2e 测都用 `?nick=`,一刀切会全断;信道成熟、前端切过去后再退役明文端点(记 auth.md 待办)。`run_receiver` 共用(只帧 I/O 分流,Connect/登录补收/顶替全不变)。
5. **`FrameError` → 关连接(安全信号),区别于解析错误(回 Err 续跑)**。入站帧 MAC/seq/结构失败 = 伪造/重放/损坏,`log.warning`(只 reason,不含明文/密钥)+ 关 ws + break(finally 清理);而**解密成功但 JSON/type 非法**仍走既有 `INVALID_MESSAGE` 回发、不关连接(合法持钥者的协议笔误)。auth.md「任一步失败:丢弃 +(ws)关连接」。
6. **握手鉴权 = sid 查会话(存在且未过期);持钥证明 = 首帧 MAC**。`?sid=` 查 `SessionStore.lookup` 得会话 → 建 `Connection` + 派信道;首帧验 MAC 过 = 证明持 token(connection.md 步 1)。sid 是**公开句柄**,嗅探者可拿 sid 连上并顶替(触发 Connect),但**无 token 造不出任何合法帧**(首帧即 `bad_mac` 关连接)、也**读不了下发密文**——只能造成 disruption。**这属 DoS(威胁模型外:内网 ≤20、RST 切连本就防不住)**;「首帧验证前不登记/不顶替」是**后续硬化**(记 auth.md 待办),本砖接受。
7. **seq 单调 + 结构原子**:`SecureChannel.open`/`seal` 同步无 await ⇒ 顶替瞬间新旧连接共享同一会话信道不交错(单线程 asyncio,同 dispatch↔Timer);`_in_seq` 仅全通过才推进(0058)。

## 打算改什么

- `app/auth/session.py`:`Session` 加 `channel: SecureChannel | None = field(default=None, repr=False)`(缓存本会话信道;`repr=False` 脱敏,含密钥)。import `SecureChannel`。
- `app/shell/connection.py`:`Connection` 加 `channel: SecureChannel | None = None`;`create(...)` 加 `channel` 形参(缺省 None = 明文 dev)。
- `app/shell/receiver.py`:收帧循环按 `conn.channel` 分流——None:`receive_text` → parse(原路);非 None:`receive_bytes` → `channel.open`(FrameError → warning + 关 ws + break)→ 明文 bytes 交 `_frame_to_command`。`_frame_to_command` 形参 `raw: str | bytes`(parse 已支持 bytes)。
- `app/shell/sender.py`:出站按 `conn.channel` 分流——None:`send_text(model_dump_json())`(原路);非 None:`send_bytes(channel.seal(model_dump_json().encode()))`。
- `app/shell/lifespan.py`:`import time`;`_channel_for(session)` get-or-derive 会话信道(读 `gameconfig.WS_FRAME_MAX_BYTES`);新增 `@app.websocket("/ws")`:accept → `session_store.lookup(sid, time.time())`(None → close 4401)→ `_channel_for` → `Connection.create(nick=session.nickname, session_id=sid, ws=ws, channel=…)` → `run_receiver`。`/dev/ws?nick=` 不动。
- `tests/shell/_fakes.py`:`FakeWS` 加 `sent_bytes` + `send_bytes` + `receive_bytes` + `feed_bytes`(与 text 共用 `_inbox` + `_WS_CLOSED` 哨兵)。
- `tests/shell/test_secure_channel_wiring.py`(新):Sender 加密往返、Receiver 加密帧穿管线、FrameError 关连接、`_channel_for` 复用(seq 连续)、`/ws` 路由注册 + 未知 sid 拒。
- docs:auth.md(§登录握手 余 / §加密信道 余 / §与新架构衔接 标接线落地 + ws selector 一次性 + 客户端 seq 契约 + DoS/硬化待办)、connection.md(dev delta + 生命周期步 1 + 三结构 SecureChannel 落地)、wire-protocol-guide.md §9(加密端点)、TODO.md(P5 逐帧/登录握手项 + 计数)。

## 实际改了什么(与「打算」对照)

与「打算改什么」一致落地。差异:测试从计划的 6 测扩到 **8 测**(自 review 补 `/ws` handler 的**未知 sid 拒 4401** + **有效 sid 建加密连接**两测,见下);顺带修两处 pre-0061 遗留 JWT 漂移(lobby.md「REST 走 JWT」→ 会话密钥信封、user.md `refresh_token` 例→ `k_user`/`session_token`,0057 去 JWT 时漏改)。共 565→**573** 测。

## 自 review

对照 [review.md](../review.md) 逐维 + **对抗式多智能体复审(3 lens finder × 反驳验证者,9 agent)**:**3 confirmed(全修)+ 3 refuted**。

- **① 分层 / 不变量**:core/reduce/dispatch 全程不见密文(`grep app/core` 无 shell/auth/sqlalchemy import);加解密只在 ws 边界(Receiver `open`/Sender `seal`);`SecureChannel`/密钥/seq 挂 Session、绝不进 world;`Connection` 引用会话信道。`_channel_for` 同步无 await ⇒ 检查-派生-赋值原子,并发握手无竞态。
- **② 代码↔文档同步**:auth.md(§加密信道 ws 接线落地 + selector 一次性 + 客户端 seq 契约 + DoS/硬化待办 / §登录握手 余)、connection.md(三结构 SecureChannel `挂 Session|None` + dev delta + 生命周期步 1 + **line 67 遗留 `挂连接`/`先验 seq→…` 改正**)、wire-protocol-guide §9、TODO 逐条对齐代码。
- **③ 文档↔文档一致**:0061 ↔ auth.md ↔ connection.md ↔ guide ↔ TODO 一致;测数 573 同步;顺修 lobby.md/user.md 的 JWT 漂移;链解析。
- **④ 数据模型**:`Session.channel: SecureChannel | None`(默认尾字段、`repr=False` 脱敏)、`Connection.channel: SecureChannel | None`(缺省 None=明文)字段序合法(green 验证)。
- **⑤ 规范合规**:具名常量(close 码 4400/4401)、中文注释讲「为什么」(逐会话 seq / MAC 先于解密 / FrameError vs 解析错误之别);**脱敏红线**:`Session.channel`/`Connection.channel` `repr=False`,FrameError 只 log `reason`(不含明文/密钥/密文),session_token/密钥不进日志;无死代码。
- **⑥ 测试充分**:8 测——Sender seal↔客户端 open 保序 / Receiver open 穿 GameLoop 改 world / FrameError 关连接(4400 + 投 Disconnect)/ `_channel_for` 缓存复用 / 逐会话 seq 挡重放(重连复用同信道 → stale_seq)/ `/ws` 路由注册 + **未知 sid 拒 4401 不建连**(自 review 补)+ **有效 sid 建加密连接**(自 review 补);明文 dev 路由既有 test_receiver/test_sender 兜(未回归)。
- **⑦ 流程账本**:打算↔实际差异上记;TODO 勾项 + 计数;提交引用 0061、全英文。

**confirmed(3,全修)**:
1. **connection.md:67 遗留 `挂连接`(per-connection)** —— 与本砖「挂 Session」核心决策 + 同文件 line 27/42/70 矛盾。**修**:改 `挂 Session(Connection.channel 只引用)`。
2. **connection.md:67 遗留入站序 `先验 seq → 验 MAC → 才解密`** —— 反了 MAC-before-decrypt 铁律(seq 藏密文内、不可能先验),0058 漏改。**修**:改 `验 MAC → 解密 → 验 seq`。
3. **`/ws` handler 未测**(仅测了路由名注册)—— reject/accept 分支零覆盖,改 close 码/漏 accept/反 `is None` 都能绿。**修**:补两测直调 handler(FakeWS 加 `accept()`)覆盖 4401 拒 + 有效 sid 建加密连接。

**refuted(3,反驳留档)**:
1. **明文 `/dev/ws` 可顶替加密 `/ws` 同 nick 连接(降级)** —— 机制真,但 dev-only 脚手架(仅 DEV_USERS、生产不挂 dev 端点)、决策 4 明示并存,非真实凭证降级,非缺陷。
2. **加密端点收到文本帧 → `receive_bytes` KeyError(非 FrameError)** —— 机制真,但 finally 干净关连接、无崩溃/泄漏/绕过(文本帧客户端本就造不出合法密文),仅 log 级别/关闭码的观察性 nit,行为正确。
3. **本段自 review 当时是占位** —— diff 未提交,「push 前回填」正是规定的 pre-submit 态,非落地缺陷(此刻即回填)。

**对抗核实(crux)**:逐条确认——① Receiver 仅对 `channel.open` 全通过的明文帧续命 + 交业务,FrameError → 关连接 + break(伪造/重放绝不进 reduce);② `_channel_for` 挂 Session、跨重连复用 → seq 逐会话连续,重放旧帧 `stale_seq` 拒(测证);③ 明文 dev 路 `send_text`/`receive_text` 行为与 0061 前逐字节等价(既有测未回归);④ 密钥/token/明文/密文不进日志、`repr=False` 兜。0 残留真 bug。
