# 0064 · P7 改密码 POST /user/password(REST 信封 + 同步鉴权列直写)

日期:2026-07-03 · 范围:`app/db/queries.py`(`load_password_for_change`)、`app/db/user_writes.py`(新,`update_password_hash` 同步直写)、`app/rest/profile.py`(加 `POST /user/password`)、`tests/rest/test_change_password.py`(新)、`docs/rest.md`/`docs/auth.md`/`docs/storage.md`/`docs/db.md`/`docs/refactor/TODO.md`。落 [rest.md](../rest.md) §用户资料的改密码项(走 0062 的会话密钥信封),兑现 [auth.md](../auth.md)「改密码=重算 salt$rounds$digest」。

## 背景 / 为什么

`/user/me`(0062)是首个信封端点但**只读**(内层参数 `{}`)。改密码是首个**带真参 + 有写**的信封消费者:验证 0062 信封确实能安全承载敏感请求参数(旧/新密码),并补齐 P7 profile 的账号管理。改昵称(需 Presence/rename/多会话联动)留下一砖。

## 关键设计决策

1. **鉴权列写 = 同步直写,不走 delayDB(架构决策,记 db.md/storage.md)**。全局积分等**内存权威**实体走 delayDB(PersistWriter 唯一写者、异步追平);但 `hash_password`/`name`/`k_user` 这些鉴权列**不进内存**(user.md:不放 `UserState`)、**DB 即权威、无内存副本**,delayDB 的「内存先生效、崩溃丢窗口可接受」模型对它们不成立——密码改了却在崩溃窗内静默回退,用户拿新密码登不进,比丢几点积分严重得多。故改密码走**请求级 session 的同步 UPDATE**、commit 后才回 200(所见即所得)。
   - **不破「唯一 DB 写者」不变量**:PersistWriter 仍是**delayDB** 唯一写者;鉴权列写是**独立的同步写路径**(DB 权威、无内存副本),与 delayDB 正交。**无锁前提靠列不相交**:PersistWriter 的状态写是**定向列 UPDATE**(`SET points`,见 0028)、改密码是 `SET hash_password`,两写永不碰同一列 ⇒ 无 lost-update、无需 `FOR UPDATE`。dev sqlite 并发写由 SQLite 库级串行兜(busy timeout),生产 postgres 行级 MVCC;改密码极低频,碰撞可忽略。
2. **旧密码必验(不只信会话)**:信封解密即证明持 session_token(bearer);再验 `old_password` = 第二因子,专防「session_token 被盗后改密码锁死真用户」。旧密码错 → 拒、不改库。
3. **错误分层(承 0062 两段式,补业务层)**:① 信封任何一步不过 → **401**(`open_request` fail-closed);② 信封验过后的**业务失败**:旧密码错/账号未启用密码 → **403**(已认证但此操作不允许;不同于 401 的「未认证」),缺参/新密码空/参数非字符串 → **400**(请求畸形);③ 基础设施失败(DB 错/会话 name 无行)→ **500**。信封已加密,状态码对被动观察者无信息(sid 不透明、参数不可解),对合法客户端是准确 UX。
4. **新密码结构底线 = 非空**(strip 后),复杂度策略(最短长度/字符类)属未来配置化,本规模不设。新密码用**新盐**重算(`hash_password` 每次新盐),改成同密码也换哈希。
5. **不吊销其它会话(v1,记档)**:改密码只改 DB 哈希,现有已认证会话仍有效(它们已持 token,改密码防的是**未来登录**)。撤销需 name→sessions 索引(SessionStore 现按 sid 键、无此索引),本规模用户改自己密码无需踢自己其它设备;列为未来。
6. **事件循环阻塞 = 继承登录的既有特征**:`verify_password` + `hash_password` 各 ~0.16s(100k 轮 SM3,`PWD_HASH_ROUNDS`),同步调阻塞事件循环 ~0.32s。登录路径(0059)已有此特征(verify ~0.16s);改密码极低频、内网 ≤20、ACTION_TIMEOUT 15s ≫ 0.32s ⇒ 无 gameplay 影响。要消除可 `asyncio.to_thread` 卸载,登录未卸载,本砖为一致亦不卸载,列为共同未来优化。

## 打算改什么

- `app/db/queries.py`:`load_password_for_change(sessionmaker, name) -> (uid, hash_password) | None`(按 name 读不可变 uid + 当前哈希供验旧;不带 k_user,最小化)。
- `app/db/user_writes.py`(新):`update_password_hash(sessionmaker, uid, new_hash)`——请求级 session、`UPDATE User SET hash_password WHERE id=uid`、commit;文件头标「鉴权列同步直写:delayDB 之外的唯一另一 DB 写路径,列不相交、DB 权威」。
- `app/rest/profile.py`:`POST /user/password`——`open_request` → 取 `{old_password, new_password}` → `load_password_for_change(session.name)` → 验旧(hash None→403;错→403)→ 校验新(非空)→ `hash_password(new, PWD_HASH_ROUNDS)` → `update_password_hash(uid, …)` → `seal_response({"status":"ok"})`。
- tests:happy(改后旧哈希验新密码过、旧密码不过 + 响应 `status:ok`)、旧密码错 403 不改库、新密码空 400、缺参 400、参数非字符串 400、未知/重放 sid 401(信封继承)、DB 错 500、会话 name 无行 500、路由注册。
- docs:rest.md(§用户资料 改密码落地)、auth.md(改密码经信封 + 同步直写注)、storage.md/db.md(鉴权列同步直写=delayDB 之外的写路径,列不相交)、TODO。

## 实际改了什么(与「打算」对照)

与「打算改什么」一致落地。自 review 补 3 测:① 列不相交端到端(改密码后 points/nickname/name 原封)——兑现决策 1;② 改成同密码仍换哈希(新盐)——兑现决策 4;③ 写路径失败 500 + 库未改——补齐决策 3 的写侧 500(原只测了 load 侧)。共 621→**637** 测(change_password 16 + 其余不变)。

## 自 review

对照 [review.md](../review.md) 逐维 + **对抗式多智能体复审(2 lens finder × 反驳验证者,7 agent)**:**2 confirmed(全修)+ 3 refuted**。

- **① 分层 / 不变量**:端点/查询/写全在 shell(`rest/`、`db/`);core 无涉(grep 复验)。**关键不变量**:鉴权列同步直写与 delayDB **列不相交**(`SET hash_password` vs PersistWriter `SET points`)⇒ 无 lost-update、无锁——`db/user_writes.update_password_hash` 用定向列 `update(User).values(hash_password=…)`,不整行 merge(有端到端测钉 points/nickname 原封)。PersistWriter 仍是 delayDB 唯一写者(鉴权列写正交,不入 delayDB)。
- **② 代码↔文档同步**:rest.md(§用户资料 `POST /user/password` 落地 + 契约 6 鉴权列直写)、auth.md(§密码存储 改密码落地)、storage.md(新「鉴权列写路径」节)、db.md(契约 5 例外)、TODO;端点用 **POST**(非 PATCH,rest.md 早先 PATCH 措辞已改)。
- **③ 文档↔文档一致**:0064 ↔ rest.md ↔ auth.md ↔ storage.md ↔ db.md ↔ TODO 一致;测数 637。
- **④ 数据模型**:`load_password_for_change -> (uid, hash|None)`(不带 k_user,最小化);`SecureResponse{status:"ok"}`。
- **⑤ 规范合规**:`PWD_HASH_ROUNDS` 引用(无裸值);失败不记密码/摘要(日志只「lookup/write failed」分类);中文注释讲「为什么」(同步直写之因 / 列不相交 / 验旧为第二因子 / 错误分层)。
- **⑥ 测试充分**:16 测——happy(旧哈希换新、旧作废、seq 回显)/ **列不相交**(points/nickname/name 原封)/ **同密码仍换哈希**(新盐)/ 旧密码错 403 不改库 / 未启用密码 403 / 新密码空 400 / 缺参·非串 400(×4)/ 未知 sid 401 / 重放 401 / **load 500** / **write 500 且库未改** / 会话 name 无行 500 / 路由注册。
- **⑦ 流程账本**:打算↔实际差异上记;TODO 更新;提交引用 0064、全英文。

**confirmed(2,全修)**:① 写路径 500 无测(原 `test_db_lookup_error_500` 的 sessionmaker 首调即抛,只命中 load 侧;删 write 侧 try/except 不会红)→ 补 `test_write_path_error_500_leaves_hash_unchanged`(call-counter sessionmaker:load 成功、update 抛 → 500 + 库未改);② 决策 4「同密码换哈希」无测(happy path 改的是不同明文,复用旧盐也绿)→ 补 `test_change_to_same_password_still_rehashes`。
**refuted(3)**:① 本段自 review 占位(pre-push 规定态,此刻回填);② 同上重复项;③ 未启用 403(快)vs 密码错 403(~0.16s)时序差——身份取自已认证会话(`session.name`),调用者只能探自己账号(其密码启用与否本就自知),无跨账号枚举面 → 非可利用,不修(记档:分支时序差是有意的,不引入无谓的假 verify)。

**对抗核实(crux)**:①列不相交经端到端测钉(改密码后 points=100/nickname=Alice 未动);②`verify_password` 绝不接触 None——`current_hash is None or not verify_password(...)` 短路,None 先走 403(测 `test_password_not_enabled_403`);③旧密码验通过才写、写失败库未改(测),两段式错误(401/403/400/500)各有测;④响应 seq 回显请求 seq(happy path 断言 seq==1)。0 残留真 bug。
