# 0062 · P5 REST 加密信封 + 首个消费者 POST /user/me

日期:2026-07-03 · 范围:`app/auth/channel.py`(抽无状态 `seal_envelope`/`open_envelope` + `derive_rest_keys` + `ReplayWindow`)、`app/auth/session.py`(Session 挂 `rest_window`)、`app/rest/secure.py`(新,信封助手)、`app/rest/profile.py`(新,`POST /user/me`)、`app/db/queries.py`(`load_profile_by_name`)、`app/gameconfig.py`+`poker.env.example`+`tests/test_gameconfig.py`(`REST_FRAME_MAX_BYTES`/`REST_REPLAY_WINDOW`)、`app/shell/lifespan.py`(挂 profile 路由)、`tests/crypto/test_channel.py`(信封原语 + 窗口)、`tests/rest/test_profile.py`(新)、`docs/auth.md`/`docs/rest.md`/`docs/connection.md`/`docs/refactor/TODO.md`。落 0057 统一信封的 **REST 半边**(ws 半边已 0061),并按「不预铺无消费者的机制」同砖落**首个需身份的端点** `/user/me`(P7 profile 首件)。

## 背景 / 为什么

0057 定案「登录后一切流量(ws + REST)走同一加密信封,解密即认证、无 JWT」;0061 落了 ws 半边,REST 仍全裸(现有 lobby/leaderboard/hands 是明示无隐私的公开读,尚可;但 P7 的 profile 端点**必须**知道「我是谁」,没有 REST 认证就无法落地)。本砖:①把信封机制落到 REST(0057 遗留的「REST 并发 seq 策略」在此细化);②用它落 `POST /user/me`(rest.md §用户资料 首件)。改昵称/改密码(需 Presence/rename 联动)留下一砖。

## 关键设计决策

1. **REST 密钥域分隔(info `\x03`/`\x04`),与 ws(`\x01`/`\x02`)互不可导**。同一会话 token 派生**两套**密钥:ws 帧用 0058 的 enc/mac,REST 信封用新 `derive_rest_keys`。**杀跨信道重放**:截获的 REST 信封注入 ws(或反向)MAC 必败——否则同钥下攻击者可拿高 seq 的 REST 帧灌 ws 使其 in_seq 跳跃、踢掉合法后续帧(DoS 虽在威胁模型外,一个 info 字节就根治,值得)。副产品:ws 与 REST 的 seq 空间天然独立,客户端各自计数,互不干扰。
2. **REST 防重放 = 每会话滑动窗(IPsec 式),非严格单调**(0057 预定的分叉,此处落地):`ReplayWindow(size)` 记「最高已见 seq + 窗内已见集合」——`seq > top` 推进;`top-size < seq ≤ top` 且未见过 → 收(容并发/乱序);重复或太旧 → 拒。ws 仍严格单调(`SecureChannel.open` 不变)。窗口大小 `REST_REPLAY_WINDOW` 进 gameconfig。
3. **响应 seq 回显请求 seq(请求-响应绑定)——偏离 0057「各方向各自计数」,当场改文档**。响应信封的 seq 不用独立服务器计数器,而是**原样回显请求 seq**:客户端验「seq == 我发的」即绑定——同会话请求 seq 严格递增不复用,旧响应答不了任何后续请求;还省掉「第二个服务器出站计数器与 ws Sender 计数互扰」的整类问题。0057 §4 本就注明「细节实现砖定」。**已知可接受面(记 auth.md)**:请求/响应共用同一对 REST 密钥 ⇒ 反射请求帧当响应能过 MAC+seq,但内层是请求 JSON、响应形状解析必败(攻击者零所得,纯 nuisance);根治要再分收发两对密钥,本规模不值。
4. **无状态信封原语抽出**:`seal_envelope(enc_key, mac_key, seq, plaintext)` / `open_envelope(enc_key, mac_key, frame, max) -> (seq, plaintext)`(结构→MAC→解密→取 seq,**无 seq 策略**);`SecureChannel.seal/open` 改为委托 + 各自 seq 策略(严格单调)。REST 每请求无连接状态,用原语 + 窗口即可,不造第二个有状态信道类。
5. **落法 = 信封助手 + 路由工厂,非 ASGI middleware**(改写 0057/TODO 的「中间件」措辞):只有需身份的端点走信封,公开读(lobby/leaderboard/hands)**本砖留明文**——rest.md 本就明示「排名可留公开」「历史 dev 无鉴权」,且三者无隐私(房配/头数/结算分/无底牌记录)。ASGI middleware 拦一切路径 + 重写 body/response 复杂且会把公开端点也拖进去。`app/rest/secure.py` 给 `open_request`/`seal_response` 两个助手,路由自取。**决策(可改)**:日后要「一切 REST 加密」再把三个读端点改 POST 信封收编。
6. **信封 wire 形(hex JSON,同 login)**:请求 `POST` body `{sid, frame}`(`sid`=session_id 公开 selector、`frame`=hex(iv‖ct‖mac),内层明文 = 端点各自的参数 JSON,`/user/me` 为 `{}`);响应 `{frame}`(hex,seq=回显)。HTTP JSON 友好、与 `/user/login` 的 hex 传输一致。
7. **错误语义两段式**:**信封任何一步不过**(sid 不识/过期、hex 坏、结构/MAC/解密坏、seq 重放、内层非 JSON)→ **统一 401**(fail-closed,同 login,不泄败因);**信封已验过之后**的失败(DB 错、行缺失)→ **明文 500 无 body 细节**——已认证,不是鉴权问题,如实区分让客户端不去无谓重登;500 响应无敏感内容,可明文。
8. `/user/me` 数据从 **DB 读**(`load_profile_by_name`,rest.md:points 滞后、精确余额在 ws);nickname 以 DB 行为准(改名后会话表同步是下一砖的事)。

## 打算改什么

- `app/auth/channel.py`:抽 `seal_envelope`/`open_envelope`(SecureChannel 委托);`derive_rest_keys(token)`(info `\x03`/`\x04`);`ReplayWindow`(`accept(seq)->bool`,top+窗内已见集,越界清理)。
- `app/auth/session.py`:`Session.rest_window: ReplayWindow | None = None`(REST 防重放窗,lazy)。
- `app/gameconfig.py` + `poker.env.example` + `tests/test_gameconfig.py`:`REST_FRAME_MAX_BYTES`(信封字节上限)、`REST_REPLAY_WINDOW`(窗口大小)。
- `app/rest/secure.py`(新):`SecureRequest{sid,frame}`/`SecureResponse{frame}` + `open_request(session_store, req, now)->(Session,seq,payload)`(查会话→derive_rest_keys→open_envelope→窗口→JSON;失败统一 401)+ `seal_response(session, seq, payload)->SecureResponse`。
- `app/db/queries.py`:`load_profile_by_name(sessionmaker, name) -> (name, nickname, points) | None`。
- `app/rest/profile.py`(新):`make_profile_router` 挂 `POST /user/me`。
- `app/shell/lifespan.py`:`create_app` 挂 profile 路由。
- tests:crypto(rest 密钥域分隔、无状态信封 round-trip、**跨信道 MAC 拒**、ReplayWindow 穷举、SecureChannel 委托后回归)、rest/test_profile(happy path 解密响应 + seq 回显、重放 401、乱序双帧都收、未知/过期 sid 401、坏 hex/伪 MAC 401、ws 帧注入 401、DB 错 500、路由注册)。
- docs:auth.md(§加密信道 REST 落地段 + 域分隔 + 窗口 + seq 回显 + 配置)、rest.md(共同原则 3 落地 + §用户资料 /user/me + 公开端点决策)、connection.md(0061 delta 余项)、TODO(P5 REST 信封 + P7 profile 首件 + 计数)。

## 实际改了什么(与「打算」对照)

与「打算改什么」一致落地。差异:自 review 补 **3 测**(KDF info 字节 known-answer 钉客户端契约 + `REST_FRAME_MAX_BYTES`/`REST_REPLAY_WINDOW` 两旋钮端到端消费)与 **4 处文档修正**(见下 confirmed);另把「反射请求帧当响应」的可接受面提前记档进 auth.md/本篇决策 3。共 573→**598** 测(crypto +9、profile +14、gameconfig +2)。

## 自 review

对照 [review.md](../review.md) 逐维 + **对抗式多智能体复审(3 lens finder × 反驳验证者,15 agent)**:**6 confirmed(全修)+ 6 refuted**。

- **① 分层 / 不变量**:信封只在 REST 边界(`secure.py` 助手 + 路由);`ReplayWindow`/密钥挂 Session、绝不进 world;core 无新 import(grep 复验);`open_request` 全同步无 await(除路由自身的 DB 读在信封验后)。
- **② 代码↔文档同步**:auth.md「REST 信封已落地」六点(分域/形/窗/回显/两段式错误/覆盖面)+ 通用信封段的 seq 措辞改为分信道 + KDF 块注 REST 域 + 残余风险清单更新;rest.md 共同原则 3 + §用户资料;connection.md 0061 delta;TODO 双线。
- **③ 文档↔文档一致**:0062 ↔ auth.md ↔ rest.md ↔ connection.md ↔ TODO 一致;测数 598 同步;auth.md 内部两处「余」清单已一致(见 confirmed 2)。
- **④ 数据模型**:`Session.rest_window` 尾字段默认合法;`ReplayWindow` 状态两元(top+窗内集),拒绝路径不改状态、推进时剪枝(测证 `len(_seen) ≤ size`);`SecureRequest{sid,frame}`/`SecureResponse{frame}` 字段注释齐。
- **⑤ 规范合规**:两旋钮进 gameconfig+env+example(无裸字面量);统一 401 detail 恒 "unauthorized"、日志只记分类 reason(无 token/密钥/明文);`load_profile_by_name` 返回投影元组不含秘密列;中文注释讲「为什么」(分域之因/窗 vs 严格单调之别/回显绑定/两段式)。
- **⑥ 测试充分**:crypto 9(REST 密钥分域四钥互异 / 无状态信封回显 round-trip / **跨信道 MAC 拒(双向)** / 窗口穷举:单调+重复+非正、窗内乱序、太旧、滑动剪枝 / SecureChannel 委托回归 seq 1..3 / **KDF info 字节 known-answer**)+ profile 14(happy path 解密+回显+对 DB / 重放 401 / 乱序双帧收 / 未知+过期 sid、坏 hex、伪 MAC、**ws 域帧注入**、非 dict 内层 → 401 / DB 错+行缺失 → 500 / **超限帧 401** / **窗宽按 gameconfig 端到端** / 路由注册)+ gameconfig 2。
- **⑦ 流程账本**:打算↔实际差异上记;TODO 勾项 + 计数;提交引用 0062、全英文。

**confirmed(6,全修)**:
1. (major)auth.md §登录握手「余」仍列「REST 信封中间件」,与 34 行外「已落地」自相矛盾 → 改「REST 信封亦已落地(0062)」、余项只留 client_nonce。
2. (minor)auth.md 通用信封段仍把 ws 专属 seq 语义当普适(「各方向各自 seq」「验 seq>已见」为唯一铁序、滑动窗仍标 0057 待定、残余风险「严格递增且双向各自计数」、「selector‖iv‖ct‖mac 是 REST 形」)→ 逐处改为分信道措辞 + 指向 0062 落地。
3. (minor)TODO 登录握手行「client_nonce 守卫(REST 信封砖一并)」成假指针(0062 未做)→ 改「独立小砖:blob 加 ts + 短窗 nonce 去重」。
4. (nit)「响应丢失后原帧重投 → 窗判重 401 → 客户端误重登」无重试规则记档 → auth.md 补「重试 = 新请求重封新 seq;401 先重试再考虑重登」。
5. (minor)`REST_FRAME_MAX_BYTES`/`REST_REPLAY_WINDOW` 端到端零消费测试(换成 ws 配置/硬编码全绿)→ 补超限帧 401 + 窗宽恰滑出拒/窗内沿收两测。
6. (nit)KDF info 字节(0x03/0x04)无 known-answer 钉(互换 enc/mac info 全绿,破客户端契约)→ 补 known-answer 测(ws 0x01/0x02 一并钉)。

**refuted(6,反驳留档)**:客户端丢计数器重用 seq(违「token 只留内存」红线的假设,合规客户端不可达)/ 信封不绑路由(单消费者下不可达,记为下一枚 profile 砖的加固注意)×2 / `REST_FRAME_MAX_BYTES` 在 hex 解码后才生效「防放大」失实(残余成本线性无放大,DoS 域外)/ `load_profile_by_name` select(User) 拉了秘密列(返回元组不含,「投影」指返回形,与文件既有惯用法一致)/ 本段自 review 占位(pre-commit 规定态,此刻回填)。

**对抗核实(crux)**:①跨信道注入双向 bad_mac(测证);②窗口拒绝路径不动状态、推进剪枝、floor 边界 (top-size, top](测证恰滑出拒/内沿收);③响应回显绑定:同会话请求 seq 不复用 ⇒ 旧响应答不了后续请求;反射面已记档(零信息所得);④401/500 两段式不构成可用 oracle(401 恒同文案;500 只泄「服务器错」);⑤SecureChannel 委托重构字节级兼容(既有 24 测 + fuzz 未动全绿 + 委托回归测)。0 残留真 bug。
