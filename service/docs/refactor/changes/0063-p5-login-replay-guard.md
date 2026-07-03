# 0063 · P5 登录重放守卫(blob 加 ts + 服务器短窗 nonce 去重)

日期:2026-07-03 · 范围:`app/auth/credentials.py`(blob 加 `ts`、`LoginProof.ts`)、`app/auth/nonce.py`(新,`NonceCache`)、`app/rest/login.py`(freshness + nonce 去重)、`app/gameconfig.py`+`poker.env.example`+`tests/test_gameconfig.py`(`LOGIN_REPLAY_WINDOW_SECONDS`)、`tests/auth/test_credentials.py`/`tests/auth/test_nonce.py`(新)/`tests/rest/test_login.py`、`docs/auth.md`/`docs/dev.md`/`docs/connection.md`/`docs/refactor/TODO.md`。落 [0059](0059-p5-login-endpoint.md) 决策 5 留的「client_nonce/exp 重放守卫」(auth.md「client_nonce + 短 exp 防登录包重放」),P5 登录握手最后一件。

## 背景 / 为什么

`/user/login` 现可被**原包重放**:攻击者截获 `{name, iv, blob}` 重投,服务器会再铸一个会话。0059 评为低危(攻击者无 K_user 解不了响应、拿不到 token),但**会话表被凭空灌**且「防重放」是 auth.md 登录握手的明文承诺。0059 也指出严谨的重放窗需 blob 内带时间戳——当时 blob 只 `{password, client_nonce}`,属设计细化,现落。

## 关键设计决策

1. **blob 形状破坏性升级:`{password, client_nonce, ts}`,`ts` 必填**(客户端墙钟,epoch 秒)。fail-closed:缺 `ts`/非数值 → `authenticate` 返 None(同缺 password)。**现在改最便宜**——消费者只有测试与 dev 流(前端登录尚未接),wire.md「破坏性变更 = 一次性同步」适用;dev.md 的 dev 登录说明同步。
2. **双守卫:freshness(exp)+ nonce 去重,缺一不可**。① `|now - ts| > LOGIN_REPLAY_WINDOW_SECONDS` → 拒(绝对值:容双向时钟偏斜;窗口进 gameconfig);② 窗口内 `(name, client_nonce)` 已见 → 拒(`NonceCache`,entry 带过期、每次调用惰性剪枝)。只有 ① 则窗口内可无限重放;只有 ② 则缓存重启/条目过期后旧包复活——两者相与,重放包要么 ts 过期、要么 nonce 撞库。
3. **`NonceCache` 时钟外移、TTL 逐调用传**(同 SessionStore/ReplayWindow 惯例):`check_and_add(name, nonce, now, ttl) -> bool`,内部 `dict[(name, nonce)] = expires_at`,先剪过期再判重。**缓存活在 `make_login_router` 内**(单 create_app 单实例;无第二消费者,不上 DevShell)。**条目 TTL = 2×新鲜窗 + 严格过期才剪(自 review 抓修,见下)**:ts 容超前 now 至 W ⇒ blob 新鲜期最晚到 ts+W ≤ now+2W;TTL 若只 W 则「条目先过期(now+W)、blob 还新鲜(至 ts+W)」出现重放缝——超前时钟的合法 blob 在 [now+W, ts+W] 可重放成功;剪枝 `now > exp`(非 `>=`)再闭掉「恰到期瞬间」的单点。
4. **已接受的残余窗(记档)**:进程重启清空 nonce 缓存 → 重启后、仍在 freshness 窗内的旧包可重放一次成功。窗口短(默认 120s)× 重启罕见,本规模接受;要消除得持久化 nonce(不值)。
5. **失败仍统一 401**(fail-closed,不泄「ts 过期 vs nonce 重复 vs 密码错」;真因只落日志分类)。**freshness/nonce 检查在 `authenticate` 之后**——先验密码再查重放,重放包也付哈希验证成本(无 DoS 顾虑,内网),换取「nonce 缓存只收真凭证」(伪造包灌不进缓存)。

## 打算改什么

- `app/auth/credentials.py`:payload 解析加 `ts`(int|float → float,bool 拒);`LoginProof` 加 `ts: float`。
- `app/auth/nonce.py`(新):`NonceCache`(`check_and_add(name, nonce, now, ttl)`,惰性剪枝 + `__len__`)。
- `app/gameconfig.py` + env example + test kwargs:`LOGIN_REPLAY_WINDOW_SECONDS: int = Field(ge=1, le=3600)`(默认 120)。
- `app/rest/login.py`:authenticate 后 ① `abs(now()-proof.ts) > 窗口` → 401;② `nonce_cache.check_and_add(...)` False → 401;日志记分类。
- tests:credentials(ts 缺失/非数值/bool 拒 + happy path 带 ts)、test_nonce(新:首见收/重复拒/过期后可复用/剪枝/跨 name 隔离)、test_login(`_make_blob` 默认带 ts + 新增 stale ts 401 / future ts 401 / 原包重放 401 / 新 nonce 二登成功)。
- docs:auth.md(§登录握手 blob 形 + 守卫落地 + 配置;§加密信道 0061 段余项清)、dev.md(dev 登录 blob 形)、connection.md(0061 delta 余项)、TODO(登录握手项勾余 + 计数)。

## 实际改了什么(与「打算」对照)

与「打算改什么」一致落地,外加自 review 抓修三件:① **nonce TTL 缝**(见决策 3 补注:TTL=W 时 ts 超前的合法 blob 在 [now+W, ts+W] 有重放缝 → TTL=2W + 严格过期才剪 + 端到端回归测 `test_replay_blocked_across_full_freshness_window_of_skewed_blob`);② **ts=NaN 缝**(`isinstance` 放行 NaN、而 `abs(now-nan)>W` 恒 False → freshness 形同虚设 → `math.isfinite` fail-closed + 参数化 NaN/±Inf 测);③ 测试再补两枚端到端(探测包不毒缓存 / nonce 按 name 隔离)。共 598→**621** 测(credentials +10 / nonce 5 / login +7 / gameconfig +1)。

## 自 review

对照 [review.md](../review.md) 逐维 + **对抗式复审(2 lens finder 多智能体;verify 层因会话限额换人工对抗核实——逐条默认反驳后定夺)**:**7 confirmed(全修)+ 1 refuted + 1 部分采纳**。

- **① 分层 / 不变量**:守卫全在 shell REST 端点(`login.py`/`auth/nonce.py`);`NonceCache` 时钟外移(now/ttl 逐调用传)、不进 world;core 无涉。
- **② 代码↔文档同步**:auth.md 握手伪码 blob 加 ts、守卫段(双守卫 + TTL=2W + 残余窗)、配置块;dev.md dev 登录 blob 形;connection.md 余项;login.py 头/字段注释同步(confirmed 修);TODO 三项 [x] 翻转 + 0056 truth-up。
- **③ 文档↔文档一致**:0063 ↔ auth.md ↔ dev.md ↔ connection.md ↔ TODO 一致;测数 621;auth.md 内已无残留「余:重放守卫」。
- **④ 数据模型**:`LoginProof.ts: float`(注明用途);`NonceCache._seen: dict[(name,nonce)] = expires_at`;拒绝路径不改状态(含「重放摸访问不续命」测)。
- **⑤ 规范合规**:`LOGIN_REPLAY_WINDOW_SECONDS` 进 gameconfig+env+example(注明 TTL=2W 派生);失败统一 401、日志只记 `stale_ts`/`replayed_nonce` 分类(无 ts/nonce 值、无凭证);中文注释讲「为什么」(NaN 之害 / 2W 之因 / 边界严格性 / 守卫次序)。
- **⑥ 测试充分**:credentials(缺 ts / 非数值[str/bool/None/list/NaN/±Inf] / 整数 ts 透 float / happy path 带 ts)+ nonce 5(首见/重复/严格过期边界/跨 name 隔离/剪枝有界/拒绝不续命)+ login +7(stale ts / future ts / 原包重放 / 新 nonce 二登 / **偏斜 blob 全新鲜期重放拒**[杀 TTL 回退 1W 变异] / **探测包不毒缓存**[杀守卫前移变异] / **nonce 按 name 隔离**[杀全局键变异])。
- **⑦ 流程账本**:打算↔实际差异上记;TODO 更新;提交引用 0063、全英文。

**confirmed(7,全修)**:① ts=NaN 过 isinstance → freshness 恒真(实际可利用性≈0:blob 须 K_user 持有者铸,合法客户端不产 NaN;但破 fail-closed 且一行修)→ `math.isfinite`;② TODO 计数 614 陈旧(TTL 修复加测后未更)→ 621;③④ login.py 模块头 + `LoginRequest.blob` 注释仍旧 blob 形 → 加 ts;⑤ 「守卫在 authenticate 后」无测钉(守卫前移则探测包可 401-锁死合法登录)→ 补测;⑥ nonce 按 name 键仅单元级有测、端点级单用户测不出全局键退化 → 种第二用户补测;⑦ gameconfig/env/auth.md 配置注释把去重窗写成 = 旋钮(实为 2×)→ 逐处改。
**refuted(1)**:「无测钉 2×TTL」——`test_replay_blocked_across_full_freshness_window_of_skewed_blob` 第二重放点(`_T0+2W`)在 TTL 回退 1W 时必失败(finder 看的是修复中间态)。
**部分采纳(1)**:「打算改什么 测试清单缺后补两测」——「打算」段是动工前计划,按 README §5 差异记入「实际改了什么」段(上),不回改计划。

**对抗核实(crux)**:①TTL=2W 完备性:接受于 t₁ 的 blob(ts∈[t₁−W, t₁+W]),条目活到 t₁+2W(含);t₂≤t₁+2W → 撞库拒;t₂>t₁+2W → t₂−ts > W → freshness 拒——无缝(端到端测钉两个临界点);②NaN 只此一穴:+Inf/−Inf 使 |now−ts|=inf>W 本就拒,NaN 是唯一比较恒 False 的值,`isfinite` 全堵;③守卫次序:authenticate 先 → 无 K_user 者(伪造/探测)绝灌不进缓存,nonce 锁死面收敛到 K_user 持有者自身(无意义);④残余窗(重启 + freshness 窗内重放一次)已记档接受。0 残留真 bug。
