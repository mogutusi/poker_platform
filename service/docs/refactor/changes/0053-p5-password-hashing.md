# 0053 · P5 密码存储原语(salt$rounds$digest + 常量时间校验)

日期:2026-07-01 · 范围:`app/auth/passwords.py`(新,`hash_password`/`verify_password`/`_derive`)、`app/auth/__init__.py`(新,建包)、`app/gameconfig.py`(加 `PWD_HASH_ROUNDS`)、`app/poker.env.example`(加 `PWD_HASH_ROUNDS`)、`tests/crypto/test_passwords.py`(新)、`tests/test_gameconfig.py`(补新字段)、`docs/auth.md`(密码存储段精化签名)、`docs/refactor/TODO.md`(P5 拆项)。开启 **P5** 第一砖。

## 背景 / 为什么

P5(国密安全信道)是 TODO 里唯一未动的大块,计划里明确「最后做」;其余(P0–P1、W、D、硬化、P4、P7 主体、P8 drain)均已落地。auth.md 把 **密码存储** 列为 P5 首项,且它是「登录握手」的前置(登录要校验密码)。本批只落这块**纯原语**。

现状:原型的裸 `sm3_hash(password)`(无盐、单轮)已随 0027 拆除,新架构尚无密码哈希。auth.md §密码存储 定的方案:**每用户随机盐 + N 轮 SM3 迭代**,存 `salt$rounds$digest`,校验用 `hmac.compare_digest` 常量时间比对。盐明文存(盐不是密钥,作用是「每人哈希不同」挡彩虹表 + 挡「同密码同哈希」)。

## 关键设计决策

1. **拆 TODO「密码哈希 + 数据迁移脚本」为两砖,本批只做原语(偏离 TODO,依 README §0 当场改计划)**。理由:
   - **无历史密码数据可迁**(原型已拆,是全新重建),所谓「数据迁移脚本」实为「给 `User` 加 `name`/`hash_password`/`K_user` 列的建表迁移」。
   - 此刻加这些列 = **死 schema**:没有注册写入方、没有登录读取方,列悬空。
   - 更重要:引入 `name`(登录账号,≠ 可变 nickname、≠ uid)是一次**实质的身份模型扩张**,应与其消费方(注册写 / 登录读 + `load_user_by_name` 查询 + dev 种子带密码)**一同落地并配测**,不该作孤儿列先行。
   - 遵循本仓既有「配置/schema 随消费方落地」的节奏(如 0050/0051 的 REST config 与查询同批)。
   - 故:**本砖 = 纯哈希原语 + 其 config 旋钮 + 穷举测**;**下一砖(登录握手)= `name`/`hash_password`/`K_user` 列 + 迁移 + `/user/login` + 会话表**。TODO P5 首项据此拆细。

2. **原语不读全局配置:`hash_password(password, rounds)` 显式收 `rounds`**(偏离 auth.md 伪码里直接 `gameconfig.PWD_HASH_ROUNDS`)。理由:
   - 纯函数、无隐藏全局依赖 → 可测性好(测试传小 `rounds` 提速,不必 monkeypatch 单例)。
   - **轮数由调用方从 `gameconfig.PWD_HASH_ROUNDS` 传**(注册/改密时),仍满足「不硬编码可调参数」——旋钮在 config,原语只是收参。
   - `verify_password` **从存储串读回轮数**(不读当前 config)→ 改 `PWD_HASH_ROUNDS` **不废旧哈希**(旧行按其自带轮数校验),这正是把 rounds 写进串的目的。
   - 同步精化 auth.md 伪码为此签名(伪码是示意,签名以代码为准;双向同步)。

3. **`PWD_HASH_ROUNDS` 进 `gameconfig`(非 `config.py`)**:auth.md §配置 明确放 `GameConfig`(游戏/策略可调,poker.env 轨),非基础设施密钥轨。`Field(ge=1, le=100000)`。`ge=1` 是**安全护栏**:rounds<1 会退化成「不迭代」(存明文近似),原语内也 `raise ValueError` 兜。env.example 值设 `100000`(SM3 numba 实测 100k 轮 ≈ 0.16s/次,登录不频繁,可接受;拉伸抬高暴力成本)。`SESSION_TTL_SECONDS`/`WS_FRAME_MAX_BYTES` 属会话/帧砖,**本批不加**(无消费方)。

4. **fail-closed 校验**:`verify_password` 遇结构非法存储串(段数≠3 / 盐或摘要非 hex / 轮数非整 / 轮数<1)一律 `return False`——**无法校验绝不放行**,且绝不因一行脏 DB 数据崩掉登录路径(把「脏数据」与「密码错」都归为「不通过」,安全侧的正确取舍)。

5. **比对在字节层用 `hmac.compare_digest`**(非 hex 串):`_derive` 出 32B、存储摘要 `bytes.fromhex` 回 32B,`compare_digest(bytes, bytes)` 常量时间;长度不等自然 False,不崩。

6. **脱敏红线**:原语纯函数、不打日志;明文密码/盐/摘要任何级别不进日志(log.md 红线,和底牌/牌堆同级)——本砖无日志点,红线由「不写 log」天然守住,登录砖接日志时再逐点核。

## 打算改什么 / 实际改了什么

- **`app/auth/__init__.py`**(新):建 auth 包(空)。
- **`app/auth/passwords.py`**(新):
  - `_derive(password, salt, rounds) -> bytes`:首原像 `password.encode()+salt`,迭代 `rounds` 轮 `sm3_hash_bytes`;`rounds<1` raise。共享给 hash/verify(免哈希逻辑分叉)。
  - `hash_password(password, rounds) -> str`:新盐 `secrets.token_bytes(16)` → `salt.hex()$rounds$digest.hex()`。
  - `verify_password(password, stored) -> bool`:split→解析→按存储盐/轮数重算→`compare_digest`;非法串 fail-closed False。
  - 具名常量 `_SALT_BYTES=16`、`_FIELD_SEP="$"`(无裸字面量)。
- **`app/gameconfig.py`**:`GameConfig` 加 `PWD_HASH_ROUNDS: int = Field(ge=1, le=100000)`(鉴权段)。
- **`app/poker.env.example`**:加 `PWD_HASH_ROUNDS=100000`(新分段「鉴权 auth.md」)。
- **`tests/test_gameconfig.py`**:`_valid_kwargs` 补 `PWD_HASH_ROUNDS=100000` + bounds-reject 补 `("PWD_HASH_ROUNDS", 0)`。
- **`tests/crypto/test_passwords.py`**(新):穷举——round-trip / 错密码 / 格式(3 段·盐 32hex·轮数·摘要 64hex)/ 盐唯一(同密码两哈希不同串但都校验过)/ 轮数写进串且 verify 用存储轮数(改「当前轮数」不影响旧串)/ 非法串 fail-closed(空/2 段/坏盐 hex/坏轮数/坏摘要 hex/摘要长度不符)/ 篡改末位 → False / rounds<1 raise(hash)+ False(verify)/ 空密码 / unicode 密码 / 确定性(同盐同轮同密码同摘要)/ config 接线(`hash_password(pw, gameconfig.PWD_HASH_ROUNDS)` round-trip)。
- **docs**:auth.md 密码存储段精化(签名 + verify 用存储轮数 + fail-closed)+ §配置段标 PWD_HASH_ROUNDS 已落地;TODO P5 首项拆「原语(本批)/ 列+迁移+登录(下砖)」。

**实际结果**:与「打算」一致落地,无签名偏离。`tests/crypto/test_passwords.py` **23 测**(自 review 后 +1 边界测)+ `tests/test_gameconfig.py` +1 bounds-reject 参数,**全绿 462→486**。手工验 `verify_password` 对抗解析边界(`0x10`/`+5`/unicode 数字轮数)均 False、不崩、不误认证;`gen_wire_ts --check` 未碰 wire(无需重跑)。

## 自 review

对照 review.md 逐维 + **跑了一遍对抗式多智能体复审**(5 lens finder × 默认反驳验证者;审 auth 这类最高风险面)。结果:**11 findings 提出,2 确认(均测试卫生,非代码 bug),4 反驳**:

- **① 分层 / 不变量**:`passwords.py` 纯函数(无 async/IO/DB/读钟/日志),不在 reduce 路径 → `_derive` 的 `raise ValueError` 合规(core reduce 禁 raise,库原语允许)。不 import shell/fastapi/sqlalchemy。加 `PWD_HASH_ROUNDS` 到 `GameConfig` 不破启动单例、不碰 headless alembic(那是 `app/config.py` 另一轨)。
- **② 代码↔文档同步**:auth.md §密码存储 已精化为真实签名(`hash_password(pw,rounds)`/`verify_password(pw,stored)`/verify 用存储轮数/fail-closed);§配置 标 PWD_HASH_ROUNDS 已落地。伪码为示意、签名以代码为准(双向同步兑现)。
- **③ 文档↔文档一致**:0053 ↔ auth.md ↔ TODO ↔ 代码四处一致;测试计数 23/486 三处同步(改 review 后回填)。auth.md 新链(passwords.py / changes/0053)指向存在的文件。
- **④ 数据模型**:存储串 `salt$rounds$digest` 结构自洽;`PWD_HASH_ROUNDS` `Field(ge=1,le=100000)`,env 值 100000 在界内、注释一致。无不可能态可表达。
- **⑤ 规范合规**:具名常量 `_SALT_BYTES`/`_FIELD_SEP`(无裸字面量);中文注释讲「为什么」(盐进第一轮足矣 / fail-closed 取舍 / rounds<1 红线);类型标注齐;无死代码/print;模块头是短「是什么」注释非复述文档大 docstring。
- **⑥ 测试充分**(review 唯一抓到处,2 条,均已修):(a)**major** `test_verify_uses_stored_rounds_not_current` 原未断言现行配置 ≠ 存储轮数,「独立于当前配置」未真证明 → 补 `assert gameconfig.PWD_HASH_ROUNDS != 7` 前置;(b)**minor** 缺 `rounds=1` 合法最小边界端到端测 → 补 `test_rounds_minimum_boundary_round_trips`。
- **⑦ 流程账本**:本篇打算↔实际对照回填;TODO P5 拆项 + 计数更新;提交将引用 0053、全英文。

**对抗核实(反驳 4 条,均假阳性)**:①「rounds 无上限 → 不可信 DB 致 DoS」——DoS 明确不在威胁模型(auth.md §威胁模型「DoS 不在范围」),且 stored 来自我们自写的 DB(非攻击者可控),驳回;②「缺盐长度不符测」——盐任意 hex 经 `_derive` 出摘要,长度不符自然 compare 失败 False,`test_digest_length_mismatch_rejected` 已覆盖同码路,cosmetic,驳回;③「缺超长摘要测」——同上,`compare_digest` 长度不等即 False,已测,驳回;④「注释称常量时间但无测证明」——该短语在 passwords.py/auth.md(设计),测试头未声称验时序,系混淆设计文档与测试注释,假阳性,驳回。

**0 真代码 bug**;2 处测试卫生已修。绿测 486 + 对抗复审双门通过。

## 待办 / 下一步

- **P5 砖 2(登录握手)**:`User` 加 `name`(登录账号,≤15,不可变,唯一)+ `hash_password` 列 + Alembic 迁移;`/user/login`(SM4 护密码、返 session_id/session_token + JWT);内存会话表;`load_user_by_name` 查询;dev 种子带 `name`+哈希密码;`SESSION_TTL_SECONDS` 进 config。
- **P5 砖 3+**:逐帧 `SecureChannel`(先验 seq→验 MAC→才解密)、`K_user` 双钥 + 每周轮换、`WS_FRAME_MAX_BYTES`、`tests/crypto/` 扩(MAC 拒伪/seq 拒重放/先验后解/IV 不复用)。
