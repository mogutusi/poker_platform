# 0055 · P5 ws 会话表(SessionStore,内存 shell 状态)

日期:2026-07-01 · 范围:`app/auth/session.py`(新,`Session` + `SessionStore`)、`app/gameconfig.py`(加 `SESSION_TTL_SECONDS`)、`app/poker.env.example`(加 `SESSION_TTL_SECONDS`)、`tests/auth/test_session.py`(新)、`tests/test_gameconfig.py`(补新字段)、`docs/auth.md`(§登录握手 / §token 层级 标会话表落地)、`docs/refactor/TODO.md`(P5 登录握手项)。承 [0053](0053-p5-password-hashing.md)/[0054](0054-p5-secure-frame-channel.md) 继续 P5,仍走「纯原语/自包含组件先行、再接 IO」。

## 背景 / 为什么

P5 登录握手(auth.md §登录握手)要一张 **ws 会话表**:`/user/login` 成功后铸 `session_id`(公开句柄)+ `session_token`(32B 秘密票据)+ `exp`,登记 `session_id → {name, nickname, token, exp}`;ws 握手 `?sid=` 查表拿 `token`(派生逐帧密钥,见 [0054](0054-p5-secure-frame-channel.md) `SecureChannel.derive`)+ `nickname`(投 `Connect`)。auth.md:会话表是**内存 shell 状态**(同原型 `_refresh_token_pool` 做法,已随 0027 拆除),**进程重启即失效 → 重登**,可接受。

本砖只落这张表本身(自包含组件,无 DB/FastAPI 依赖),供 `/user/login` 端点砖直接用。**先于端点做**,同 0053/0054 自底向上:把可脱离 DB/HTTP 穷举的会话生命周期(铸/查/过期/吊销/清)先钉死。

## 关键设计决策

1. **时钟外移进参数,组件保持可测**:会话 `exp` 是墙钟量(TTL 秒)。会话表是 shell 态、本可直接读钟,但 `create(name, nickname, now)`/`lookup(sid, now)`/`prune(now)` **显式收 `now: float`**(epoch 秒,端点传 `time.time()`)→ 无隐藏时钟依赖、过期逻辑可控测(同 timer.md「时间只活在 shell、且外移求可测」)。core 不涉(纯 shell 组件)。
2. **`session_id` 公开、`token` 秘密,分职**:`session_id = secrets.token_urlsafe(...)`(URL 安全串,进 `ws?sid=` 明文无妨,是公开句柄);`token = secrets.token_bytes(32)`(秘密票据,派生逐帧密钥、**永不再上线**,只留服务器会话表 + 客户端本地)。二者独立随机。
3. **表按 `session_id` 索引,不强制每用户单会话**:轮换 = 新登录铸新会话 + 连接层顶替(connection.md),会话层不必单例;宽限期内可并存新旧会话。`revoke(sid)` 吊销单会话,`prune(now)` 周期清过期。键唯一 `session_id`(公开句柄查表),简单无歧义。
4. **过期即无效 + 顺手删**:`lookup` 遇 `now >= exp` 返回 `None` 并删该行(惰性清);另有 `prune(now)` 供周期主动清(避免过期行长滞留)。**服务器 exp 兜底**(auth.md):客户端正常会提前无感轮换,撞到 exp 即被拒。
5. **鉴权秘密只在 shell,不进 world**:`token`/`name` 等是会话/DB 的事,`world.users` 只放游戏权威(`points` 等),呼应 user.md / auth.md「鉴权字段不进 UserState」。`SessionStore` 是 shell 单例(将挂 lifespan,同 ConnectionManager),不进 core。
6. **`SESSION_TTL_SECONDS` 进 gameconfig**(auth.md §配置,`Field(ge=60, le=86400)`;env 值 3600=1 小时,轮换周期由它定、客户端到期前留余量无感重连)。`SessionStore(ttl_seconds)` 收之。
7. **测试目录 `tests/auth/`**:crypto 原语(密码哈希 0053 / 逐帧信道 0054)测在 `tests/crypto/`;**会话表非密码学**(是 shell 状态管理),另置 `tests/auth/`。往后:纯密码学(K_user 派生)归 crypto,鉴权流程(会话/登录端点/authenticate)归 auth。

## 打算改什么

- **`app/auth/session.py`**(新):
  - `Session`(dataclass):`name`(登录账号,不可变)/`nickname`(游戏昵称)/`token`(32B 会话票据)/`expires_at`(过期墙钟 epoch 秒)。
  - `SessionStore`:`__init__(ttl_seconds)`、`create(name, nickname, now) -> (session_id, Session)`、`lookup(session_id, now) -> Session | None`(过期删返 None)、`revoke(session_id)`、`prune(now) -> int`、`__len__`(测/监控)。具名常量 `_SESSION_ID_BYTES`/`_SESSION_TOKEN_BYTES`。
- **`app/gameconfig.py`** + **`poker.env.example`**:`SESSION_TTL_SECONDS`(鉴权段,承 0053/0054)。
- **`tests/test_gameconfig.py`**:`_valid_kwargs` + bounds-reject 补 `SESSION_TTL_SECONDS`。
- **`tests/auth/test_session.py`**(新):create(id≠token、id/token 每次异、exp=now+ttl、登记可查)/ lookup(命中·未知 None·过期 None 且删)/ 多会话并存(每用户多 sid)/ revoke(删单·幂等)/ prune(清过期返计数·不误清未过期)/ token 是 32B bytes / config 接线。
- **docs**:auth.md §登录握手 / §配置 标会话表落地(签名);TODO P5 登录握手项记会话表已落、余端点。

**实际结果**:与「打算」一致。`tests/auth/test_session.py` **12 测**(含自 review 后 +1 repr 脱敏测)、`tests/test_gameconfig.py` +2 bounds-reject 参数,**全绿 511→524**(+repr 测后 net 见下批统计)。

## 自 review

对照 review.md 逐维 + 跑对抗式多智能体复审(4 lens finder × 反驳验证者)。**复审工况**:安全 finder 产出后,workflow 因后端 "temporarily unavailable" **中途卡住**(其余 3 lens 未回结论)。据已产出的**安全发现 + 补做人工逐维**定稿(review.md:对抗式自 review 是门槛,workflow 是其一种形态,人工亦可)。

- **安全 finder(confirmed,已修)**:`Session` 是 dataclass,默认 `__repr__` 会带出 32B 秘密 `token` → 误 `print`/log 一个 Session 即泄 session_token(违 log.md 脱敏红线)。**修**:`token: bytes = field(repr=False)`,把「纪律级」保护变「代码级」(纵深防御)+ 补测 `test_repr_redacts_secret_token`(断言 token hex/bytes 不在 repr、非秘密字段仍在)。实测 `repr(session)` = `Session(name=..., nickname=..., expires_at=...)`,token 不现。
- **① 分层 / 不变量**:`SessionStore` 纯 shell 组件(无 core/world 耦合、无 async/IO/DB);时钟外移(`now` 显式传)。鉴权秘密 `token` 不进 world(user.md/auth.md 红线)。
- **② 代码↔文档同步**:auth.md §登录握手 标 `SessionStore`(create/lookup/revoke/prune)+ `Session` 字段落地;§配置 标 SESSION_TTL_SECONDS 已落地。签名与代码一致。
- **③ 文档↔文档一致**:0055 ↔ auth.md ↔ TODO ↔ 代码一致;新链指向存在文件。
- **④ 数据模型**:`Session{name,nickname,token,expires_at}` 字段注释齐;`SESSION_TTL_SECONDS` `Field(ge=60,le=86400)`、env 3600 在界内。过期判据 `now >= expires_at`(到点即失效,不是「过点才失效」)——人工核对边界:`lookup` 测覆盖 `exp-1` 仍在、`exp` 恰失效并删。
- **⑤ 规范合规**:具名常量 `_SESSION_ID_BYTES`/`_SESSION_TOKEN_BYTES`;中文注释讲「为什么」(时钟外移 / 惰性清 / 不强制单会话);类型标注齐;无死代码。
- **⑥ 测试充分**:create(id≠token·唯一·exp·可查)/ lookup(命中·未知·过期删)/ 多会话并存 / revoke 幂等 / prune 只清过期 / token 32B / repr 脱敏 / config 接线——人工核 `prune` 的 iterate-then-delete 安全(先建 expired 列表再删,无迭代中改)、`lookup` 惰性删安全(按单键删)。
- **⑦ 流程账本**:打算↔实际对照;TODO 项 `[~]` + 计数;提交引用 0055、全英文。

**对抗核实**:人工逐行核 create/lookup/revoke/prune —— `now >= exp` 边界对、过期即删无泄、`session_id`(公开)与 `token`(秘密)分职、`secrets` CSPRNG、无 caller-supplied id(无会话固定风险)、无秘密入 world。0 残留真 bug(安全发现已修)。**注**:因 workflow 卡死,未得其余 3 lens 的机器复核,以人工逐维补偿;下批(0056)若后端恢复再跑完整 workflow。

## 待办 / 下一步

- **P5 砖(authenticate + schema)**:`User` 加 `name`(唯一·不可变·≤15)/`hash_password`/`k_user` 列 + Alembic 迁移;`load_user_by_name` 查询;`authenticate(name, blob, iv)`(SM4 解 blob → verify_password)纯逻辑。
- **P5 砖(登录端点)**:`POST /user/login`(解 blob + authenticate + `SessionStore.create` + SM4 加密响应 + JWT sub=name);dev 种子带 name/密码/K_user;`SessionStore` 挂 lifespan。
- **P5 砖(信道接线)**:Receiver 握手 `?sid=` → `SessionStore.lookup` → `SecureChannel.derive` → 收发帧走 `open`/`seal`。
