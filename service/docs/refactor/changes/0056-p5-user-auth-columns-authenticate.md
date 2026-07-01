# 0056 · P5 User 鉴权列 + 迁移 + authenticate(登录凭证校验,无端点)

日期:2026-07-01 · 范围:`app/db/models.py`(`User` 加 `name`/`hash_password`/`k_user` 列)、新迁移 `alembic/versions/<hash>_add_user_auth_columns.py`、`app/db/queries.py`(`load_user_for_login`)、`app/auth/credentials.py`(新,`authenticate` 纯逻辑:SM4 解 blob → verify_password)、`tests/auth/test_credentials.py`(新)、`docs/auth.md`(§密码存储 / §共享密钥 标列落地)、`docs/db-migrations.md`(记新迁移)、`docs/refactor/TODO.md`。承 [0053](0053-p5-password-hashing.md)/[0054](0054-p5-secure-frame-channel.md)/[0055](0055-p5-session-store.md),把「校验一次登录凭证」所需的 schema + 查询 + 纯逻辑落齐,**不含 HTTP 端点**(下一砖)。

## 背景 / 为什么

登录握手(auth.md §登录握手)第 2 步:`按 name 取 K_user → 解 blob → 校验密码(SM3+盐)`。这一步需要:
1. **DB schema**:`User` 存 `name`(登录账号)、`hash_password`(`salt$rounds$digest`)、`k_user`(该用户 SM4 密钥,解登录 blob 用)。现 `User` 只有 `id`/`nickname`/`points`(models.py 注:「国密鉴权列随 P5 以新迁移加」)。
2. **查询**:按 `name` 载入上述字段。
3. **纯逻辑** `authenticate`:SM4 解 blob(`{password, client_nonce}`)+ `verify_password`。可脱离 FastAPI 穷举测。

本砖落这三样(schema + query + authenticate),延续 0053/0055 的「先落可穷举测的自包含件,再接 HTTP」。端点(`/user/login` + 会话铸造 + JWT + 响应加密 + dev 种子)归下一砖。

## 关键设计决策

1. **鉴权列一律 `nullable`(可空),不做数据回填**(对比 0052 的 `server_default` 手法)。理由:
   - `name` 是 **UNIQUE**,不能像 0052 的 `room` 那样给常量 `server_default=''`——多行会撞唯一约束。
   - 本项目无历史用户数据(原型 0027 拆,dev 用 `create_all` 非迁移建表);迁移面向「真库」,而真库此刻 user 表要么空、要么是 pre-P5 无鉴权行。
   - **加可空列 = 最安全的增量迁移**:既有行 `name/hash_password/k_user` 记 `NULL`(= 「未启用登录」),无需回填、无唯一冲突(sqlite/pg 视多个 NULL 互异)。`load_user_for_login` 按 `WHERE name = ?` 过滤,天然跳过 NULL-name 行。启用登录 = 后续给该用户写这三列(注册/管理员工具,另砖)。
   - 语义:`UserState`(内存)不变;这三列纯 DB/shell 鉴权字段,不进 world(auth.md/user.md 红线)。
2. **`name` 唯一 + 索引 + `max_length=15`**(auth.md 身份模型:登录账号 ≤15、不可变、唯一)。`Field(default=None, max_length=15, unique=True, index=True)`(登录按 name 查 → 索引)。
3. **`hash_password: str | None`**:存 `salt$rounds$digest`(0053 格式,≈100 字符)→ `max_length=128`(与 dedupe_key 同,宽松够用)。
4. **`k_user: str | None`(hex)**:SM4 密钥 16B → 32 hex 字符,`max_length=64`(留余量)。存 hex 而非裸 bytes 列:跨方言稳、便于管理员工具导出/比对;`authenticate` 解回 `bytes.fromhex`。**单把 K_user 版**——auth.md 的双钥/版本/宽限(k_cur/k_prev/…)属「K_user 每周轮换」砖,本砖只落当前有效那把;脱敏红线:k_user 不进日志。
5. **`authenticate` 纯逻辑、fail-closed、Go 风格返回**:`authenticate(user_row, iv, blob, now?) -> (uid, name, nickname) | None`(或 `Err`)。步骤:取 `k_user`(缺 → None,未启用登录)→ `sm4_cbc_dec(k_user, iv, blob)` → JSON 解 `{password, client_nonce}`(坏 → None)→ `verify_password(password, hash_password)`(不过 → None)。**任何异常/缺字段/解密坏 → None(不放行、不崩)**,同 `verify_password` 的 fail-closed。`client_nonce`/`exp` 重放防护由端点砖按会话/时间做(auth.md §登录握手);本砖 authenticate 只做「密钥+密码」这层,client_nonce 透传给端点校验(设计随端点砖细化)。放 `app/auth/credentials.py`。
6. **迁移用 `--autogenerate` + 审**(db-migrations.md):autogen 出 add_column ×3 + name 唯一索引;因全可空、无 server_default 顾虑,基本直用;`down_revision='010d8e8a08d7'`(接 HandRecord.room 后)。实测 upgrade(空库 + 有 pre-migration 行两路)/downgrade round-trip。
7. **`load_user_for_login` 与 `load_user_by_nick` 分职**:后者(0030)按 nick 取 (uid, points) 供 JoinRoom;本查询按 name 取 (uid, name, nickname, hash_password, k_user) 供登录。分文件同 queries.py 既有风格。

## 打算改什么(实现时细化)

- `app/db/models.py`:`User` 加 `name`/`hash_password`/`k_user` 三可空列(注释齐 + 唯一/索引/长度)。
- `alembic/versions/<hash>_add_user_auth_columns.py`:autogen + 审;三 add_column(可空)+ name 唯一索引。
- `app/db/queries.py`:`load_user_for_login(sessionmaker, name) -> tuple | None`。
- `app/auth/credentials.py`:`authenticate(...)` 纯逻辑(SM4 解 + verify_password,fail-closed)。
- `tests/auth/test_credentials.py`:正路(正确 K_user+密码 → 通)/ 错密码 / 错 K_user(解出乱码 JSON→None)/ 坏 blob / 缺 k_user(未启用)/ client_nonce 透出 / unicode 密码。
- docs:auth.md(§密码存储/§共享密钥/§登录握手 标列 + query + authenticate 落地)+ db-migrations.md 记迁移 `49417b108733` + TODO。

**实际结果**:与「打算」一致落地。`app/auth/credentials.py` `authenticate` + `app/db/queries.py` `load_user_for_login`/`LoginUser` + `models.py` 三可空列 + 迁移 `49417b108733`。`tests/auth/test_credentials.py` **22 测** + `test_login_query.py` **6 测**,**全绿 524→553**(net +29:22+6+1[0055 repr]=29)。**迁移人工实测**:空库 upgrade + 插 pre-migration `legacy` 行 → upgrade 后该行 name/hash/k_user=NULL 存活、`ix_user_name` 就位;downgrade -1 + re-upgrade round-trip 通,legacy 行存活。**authenticate 模糊对抗**:5000 条随机 iv/blob → 全 None、0 崩溃、0 误认证。

## 自 review

对照 review.md 逐维 + **跑对抗式多智能体复审(4 lens finder × 反驳验证者,后端已恢复,完整跑完)**:**4 lens 全空、0 findings**。另做人工逐维:

- **① 分层 / 不变量**:`credentials.py` 纯逻辑(无 async/IO/DB;import verify_password[纯] + sm4_cbc_dec[纯])。`load_user_for_login` 是 async DB 读(queries.py,正确层)。三鉴权列纯 DB 字段,不进 world/UserState(user.md/auth.md 红线)。
- **② 代码↔文档同步**:auth.md 三处(密码存储/共享密钥/登录握手)标列 + authenticate + query 落地,签名与代码一致;db-migrations.md 记迁移 id `49417b108733`;models 列注释 ↔ 迁移 ↔ 查询三处 name/hash/k_user 语义一致。
- **③ 文档↔文档一致**:0056 ↔ auth.md ↔ db-migrations ↔ TODO ↔ 代码一致;迁移 id、列名、nullable 决策四处同述。链指向存在文件。
- **④ 数据模型**:三列 nullable(既有行 NULL=未启用登录);`name` 唯一 + 索引(多 NULL 互异,sqlite/pg)+ `max_length=15`;`hash_password` 128、`k_user` 64。**对抗自问「唯一列加到有行的表会崩?」**→ nullable 无 server_default 亦安全(NULL 不撞唯一),人工 backfill 实测 legacy 行存活。
- **⑤ 规范合规**:具名常量 `_KEY_BYTES`/`_SM4_BLOCK_BYTES`;中文注释讲「为什么」(nullable 迁移理由 / fail-closed / 结构护栏免喂裸去填充);类型标注齐;`LoginUser` 用 NamedTuple 而非裸 5-tuple(守 coding_principle「有结构别用裸 tuple」);无死代码;迁移手加 nullable-why 注释。
- **⑥ 测试充分**:authenticate——正路 / 错密码 / 错 K_user / 未启用(NULL 两路)/ 坏 k_user_hex ×4 / 坏 iv 长 / 坏 blob 长 ×4 / 非 JSON / 缺 password / 缺 client_nonce / 非 dict·字段非 str ×4 / unicode / fuzz-不崩;query——启用/具名/name 设密钥空/未知/name≠nickname/legacy NULL 不可载。**未自动测迁移**(测套用 create_all,故人工 round-trip 验,同 0052 惯例)。
- **⑦ 流程账本**:与 0055 隔离提交(先 push 0055 再动 0056 共享文档,diff 不缠);打算↔实际对照;TODO `[~]` + 计数;提交引用 0056、全英文。

**对抗核实(crux = authenticate 解不可信 blob)**:①「解未验密文 = padding-oracle?」→ 登录 blob 无 MAC 是 auth.md 设计(登录前无会话;SM4+短 exp+client_nonce 保护),`sm4_cbc_dec` 裸去填充但**任何失败均归一 None(登录失败)**、无可辨识 oracle 信号,且 K_user 是根秘密(无它造不出有效结构);结构护栏(iv==16 / blob 非空 %16 / k_user==16B)+ `(ValueError,KeyError,TypeError)` 捕获使解密/JSON/取字段全路径 fail-closed(fuzz 5000 佐证 0 崩)。存活=设计正当。②「会误认证?」→ verify_password 仅在取到 str password 后调,False→None;不可能对错密码返回 LoginProof。0 真 bug。

## 待办 / 下一步

- **P5 砖(登录端点)**:`POST /user/login`(解 body → authenticate → `SessionStore.create` → SM4 加密响应 {session_id, session_token, exp} + JWT sub=name)+ client_nonce/exp 重放防护 + dev 种子给 dev 用户写 name/密码/K_user + `SessionStore`/`SessionTable` 挂 lifespan。
- **P5 砖(信道接线)** / **K_user 每周轮换**(双钥/版本/宽限列 + 轮换任务)。
