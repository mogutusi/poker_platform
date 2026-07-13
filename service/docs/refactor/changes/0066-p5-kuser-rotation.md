# 0066 · P5 K_user 双钥轮换(k_cur/k_prev + 版本/宽限 + 管理员 CLI)

日期:2026-07-10 · 范围:`app/db/models.py`(`k_user`→`k_cur` + 五列)、Alembic 迁移(新)、`app/db/queries.py`(`LoginUser` 扩列 + 轮换查询)、`app/db/user_writes.py`(`rotate_kuser`/`issue_login`)、`app/auth/kuser.py`(新:密钥/口令生成 + 轮换编排)、`app/rest/login.py`(k_cur→k_prev 两次尝试 + `rotate` 提示)、`scripts/kuser_admin.py`(新:list/rotate/issue)、`app/shell/lifespan.py`(种子盖 ver)、`app/gameconfig.py` + `poker.env.example`(两旋钮)、tests、`docs/auth.md`/`dev.md`/`db-migrations.md`/`connection.md`/`TODO.md`。P5 最后一砖([TODO](../TODO.md) §P5「`K_user` 双钥 + 每周轮换任务 + 版本/宽限」)。

## 背景 / 为什么

`K_user` 是引导密钥(登录时护住密码、换回会话密钥),**泄露即全损**(能派生任意未来会话)——[auth.md](../auth.md) 的对策是**每周轮换**把「一把泄露还能用多久」压到一周。0056 只落了单把 `k_user` 列;本砖落双钥(当前 + 上一把宽限)+ 版本 + 轮换任务 + 管理员 CLI(auth.md 待办「每用户密钥的下发与轮换工具」)。轮换**不影响已建会话**(会话密钥派生自 `session_token`,与 `K_user` 无关)——只影响之后的登录。

## 关键设计决策(三处偏离 auth.md 原设计,均已同步改 auth.md)

1. **登录不带 `key_version`,服务器按「先 `k_cur` 后 `k_prev`」两次尝试**(偏离 auth.md 原「登录请求带 key_version、按版本取键」)。理由:`K_user` 是**用户手输**的密钥,用户无从知道也不该被要求记「这是第几版」;协议(`{name, iv, blob}`)保持不动,老客户端零改动。服务器侧两次 `authenticate` 尝试的代价:错钥路径在 SM4 解密/JSON 解析即败(不进昂贵的 `verify_password`),≤20 人规模可忽略。哪把解开即知是否旧钥(→ `rotate` 提示),版本列退为**管理员记账**(list/导出对账),不进协议。
2. **`k_cur_until` 语义 = 「到期应轮换时刻」(轮换任务的 due),登录不检查它**(偏离原表格「失效时刻」的字面读法)。若登录也拒过期 `k_cur`,轮换 cron 迟跑/挂掉会**锁死全员登录**——运维故障放大成全站不可用,而「泄露窗口 ≤ 一周」本就依赖轮换真的发生,不靠拒登兜底。改为:cron 每次跑只轮换 `k_cur_until <= now` 的用户(幂等,跑多勤都行);`k_cur_until` 为 NULL = **不排程**(dev 种子行、手动管理的行),只能 `rotate --name` 显式轮换。`k_prev_until`(宽限截止)登录**照查**——旧钥过宽限即拒,这是真正的安全边界。
3. **`*_until` 列用 epoch 秒(float),不用 DateTime**。auth 全链时基是 float epoch(`SessionStore.expires_at`、`now: Callable[[], float]`、blob.ts),DateTime 列在 sqlite 读回丢 tz(queries.py `_as_utc` 之坑)——鉴权比较逻辑不该再引入一处 tz 补丁面。DM/HandRecord 的 DateTime 列不动(它们要展示、要 SQL 比较)。
4. **轮换任务落点 = 管理员 CLI(`scripts/kuser_admin.py`)+ 系统 cron,不做进程内调度**(auth.md 本就二选一)。决定性理由:新钥必须**带外下发**(管理员私发给用户),进程内轮换产出的新钥无处可去——打进服务器日志违反脱敏红线(`K_user` 任何级别不进日志)。CLI 把新钥打到**管理员终端 stdout**(这正是带外通道的起点),服务器进程全程不知新钥。副产品:轮换写与登录读是跨进程并发,但都是单行短事务、列不相交于 PersistWriter(`SET k_*` vs `SET points`),无锁前提不破(storage.md「鉴权列写路径」)。
5. **`issue` 子命令一并落**(首发/补发):没有它,生产用户永远拿不到第一把钥、排程轮换永远没有对象(现状唯一 login-enable 路径是 dev 种子)。语义:按 `name` 定位或新建 User 行 → 生成高熵随机口令 + 全新 `K_user`(v1)→ 盖 `hash_password`/`k_cur`/`k_cur_ver=1`/`k_cur_until=now+周期` → 打到 stdout 由管理员带外发。已启用的行须 `--reset` 才覆盖(防手滑重置)。
6. **`rotate` 的 SQL 是单条 UPDATE 的列到列搬移**:`SET k_prev=k_cur, k_prev_ver=k_cur_ver, k_prev_until=:grace_end, k_cur=:new, k_cur_ver=k_cur_ver+1, k_cur_until=:due WHERE id=:uid AND k_cur IS NOT NULL`——SQL 的 SET 右值取**旧行值**(SQL 标准语义,sqlite/pg 一致),天然原子:无「读-改-写」窗口,与并发登录读互不半见。
7. **登录响应 payload 加 `rotate: bool`**(被匹配到的那把 K_user 加密——**响应必须用匹配键加密**,否则旧钥客户端解不开):`k_prev` 命中 → `rotate=true`,提示用户尽快换上管理员已带外发来的新钥。响应是登录引导信道的加密 JSON,不进 wire.gen.ts(同 0059),加性演进。

## 打算改什么

- `app/db/models.py`:`k_user` → `k_cur`(重命名,语义=当前钥)+ `k_cur_ver`/`k_cur_until`/`k_prev`/`k_prev_ver`/`k_prev_until`(均 nullable;until 为 epoch 秒 float)。
- Alembic 新迁移:batch `alter_column` 重命名(autogen 会误判成删+加,手改保数据)+ 加五列 + 回填 `k_cur_ver=1 WHERE k_cur IS NOT NULL`(`k_cur_until` 留 NULL=不排程)。
- `app/gameconfig.py` + `poker.env.example`:`KUSER_ROTATION_DAYS`(ge=1 le=90,基线 7)/ `KUSER_GRACE_DAYS`(ge=0 le=30,基线 3)。
- `app/db/queries.py`:`LoginUser` 改 `(uid, name, nickname, hash_password, k_cur, k_prev, k_prev_until)`;`list_login_users`(CLI list:name/nickname/ver/until/有无 prev,**不带键材料**);`users_due_for_rotation(now)`。
- `app/db/user_writes.py`:`rotate_kuser(uid, new_key_hex, now, rotation_s, grace_s) -> bool`(决策 6 的 CAS 式单 UPDATE)+ `issue_login(...)`(首发/补发直写)。
- `app/auth/kuser.py`(新):`generate_kuser()`(16B hex)/ `generate_password()`(高熵 urlsafe)/ `rotate_due(sessionmaker, now) -> list[RotatedKey]`(查 due → 逐个生成 + 写 → 返回新钥清单给 CLI 打)。
- `app/rest/login.py`:先 `authenticate(hash, k_cur)`;败且 `k_prev` 在宽限内再试 `k_prev`;响应用匹配键加密、payload 加 `rotate`。
- `scripts/kuser_admin.py`(新):`list` / `rotate [--name NAME]` / `issue --name NAME [--nickname NICK] [--points N] [--reset]`;打新钥/新口令到 stdout(带外下发起点;提醒勿重定向到会入库/入 git 的文件)。
- `app/shell/lifespan.py` 种子:`k_cur=DEV_KUSER, k_cur_ver=1`(until 留 NULL 不排程,dev 钥不被 cron 轮走)。
- tests:`tests/auth/test_kuser_rotation.py`(轮换搬移/幂等/due 选择/NULL 跳过/issue 各臂)+ `tests/rest/test_login.py` 扩(宽限内旧钥 → 200+rotate=true+旧钥可解;过宽限 401;新钥 rotate=false;无 prev 时旧钥 401)+ 既有引用改列名。
- docs:auth.md(§K_user 每周轮换 落地改写 + 三偏离)、dev.md(CLI 用法)、db-migrations.md(新迁移)、connection.md(0061 余项清)、TODO.md(勾 P5 项)。

## 实际改了什么(与「打算」对照)

与「打算改什么」一致落地,四处细化:

- **迁移 `b8ca88a687af`**:autogen 果然把重命名判成「删 `k_user` + 加 `k_cur`」(丢已发密钥),手改成 batch `alter_column(new_column_name=)` 双向对称 + 回填 `k_cur_ver=1 WHERE k_cur IS NOT NULL`;up→down→up 实测数据保全、回填幂等。
- **`rotate_kuser` 的 ver+1 用 `coalesce`**:计划稿想「NULL ver 宁可炸出来」,但 SQL 里 `NULL+1 = NULL` 是**静默**抹版本、不会炸——改 `coalesce(ver,0)+1` 兜脏行(注释记明缘由)。
- **CLI 强制轮换路用 `load_identity_by_name`**(只需 uid),不用 `load_user_for_login`(免无谓载入 hash/密钥秘密列,沿 0064「最小化秘密面」)。
- **`issue` 入参形制在 CLI 拒**(sqlite 不强制 VARCHAR 长度):name ≤15 / nickname ≤50 / 首尾空白拒(承 0065 冒充面)。
- **`RotatedKey.__repr__` 脱敏**:frozen dataclass 默认 repr 会把新钥带进异常栈/调试输出,覆写为 `<redacted>`(log.md 红线的纵深)。

测试:`test_kuser_rotation.py` 17(轮换搬移含 until 值+参数序/两轮丢最旧/未发钥拒/RETURNING 回版本+repr 脱敏/NULL ver coalesce 重计/due 三分/批量幂等/**单账号失败隔离·边轮边出**/issue 五臂含版本回报/生成器形制/list 无键材料)+ `test_login.py` +7(宽限内旧钥 200+rotate=true+旧钥可解[边界恰在 `now==k_prev_until`] / 新钥 rotate=false / 过宽限 401 / prev_until NULL 脏行 fail-closed 401 / 无 prev 时错钥 401 / **k_cur_until 过期仍可登录**[钉决策 2] / **旧钥原包重放 401**[守卫 × 双钥])+ 既有 happy 断言补 `rotate` 键 + `test_gameconfig` 两旋钮边界;662→**688** 全绿。CLI 对着真 sqlite 走通 issue→list→rotate(due 幂等)→rotate --name→重 issue 拒/--reset(版本如实 v2),错误臂(ghost 账号/坏 name 形制)退出码 1。

docs:auth.md(§共享密钥/§K_user 每周轮换 落地改写含三偏离、§登录握手 rotate、§配置、§待办 CLI 勾销)、connection.md(0061 余项清:P5 全落)、dev.md(K_user 管理一节)、db-migrations.md(迁移清单 + 「必审 autogen」现行案例)、storage.md/user.md/rest.md(列名 `k_user`→`k_cur`/`k_prev`)、TODO.md(勾 P5 项,全 P5 闭环)。

**残留(记档,均 dev-only/接受)**:① 改列名后,**旧 dev sqlite 库**(pre-0066 由 `create_all` 引导建的 `poker.db`)没有新列——`create_all` 不做迁移(db-migrations.md 已明示),dev 修复 = 删 `poker.db` 重建或跑 `alembic upgrade head`。② CLI 直连库须 async URL(缺省 `sqlite+aiosqlite` 正确;pg 的 `postgresql+psycopg://` 同串双栖,无坑;手给 `sqlite:///` 同步形会报非异步方言,dev nuisance)。③ 轮换与登录跨进程并发:登录读投影与轮换单行 UPDATE 之间无锁——最坏情形是登录拿到「轮换前一瞬」的投影,用刚被降位的钥登录成功但 `rotate` 提示缺失一次(下次登录即正确);密钥有效性不受影响(该钥此刻已是 `k_prev`、在宽限内本就合法)。

## 自 review

对照 [review.md](../review.md) 逐维 + **对抗式多智能体复审两轮**(4 lens finder × 逐发现反驳验证者;首轮中途撞会话额度,仅 tests-model lens 完成 → 其 6 候选由主线逐条对抗核实;二轮补齐 security/correctness/layering-docs 三 lens + 9 验证者,共 22 agent):**10 confirmed(全修/记档)+ 5 refuted**。

- **① 分层 / 不变量**:全在 shell/DB/scripts(core 零触碰;`grep` 复验 core/ 无 auth/db import);`app/auth/kuser.py` import db 合法(auth 是 shell 侧,同 session.py);鉴权列写与 PersistWriter 列不相交延续成立(本轮把例外「issue 建新行带 points——该用户必不在内存」论证补进 storage.md/模块头注)。轮换不进 world/会话(轮换不动会话密钥)。
- **② 代码↔文档同步**:auth.md §K_user 每周轮换按落地形改写并记三偏离(无 key_version 两次尝试 / `k_cur_until`=排程非拒登 / epoch float 列);storage.md 无锁句从「SET hash_password」扩成完整写集(0064/0065/0066)+ issue INSERT 例外论证;dev.md/db-migrations.md/connection.md/user.md/rest.md 同步。
- **③ 文档↔文档一致**:0066 ↔ auth.md ↔ storage.md ↔ TODO 一致;测数 688 两处对齐(confirmed 9 抓过陈旧 684)。
- **④ 数据模型**:双钥六列均 nullable(未启用=NULL 族);`rotate_kuser` 返回 `int|None`(RETURNING 版本)、`issue_login` Go 风格 `(ver, refusal)`;`RotatedKey.__repr__` 脱敏;`RotationFailure` 不携密钥(失败即未 commit)。
- **⑤ 规范合规**:天秒换算收敛 `SECONDS_PER_DAY`;CLI 长度界限具名(`_NAME_MAX_LEN` 对齐 models);新列逐字段中文注释;无死代码/裸 print 调试。
- **⑥ 测试充分**:见「实际改了什么」测试段;首轮 6 候选中 5 转为钉子(天秒换算+参数序 / 宽限含端点边界 / 决策 2 过期 k_cur 可登录 / NULL ver coalesce / 旧钥重放),1 驳回(迁移 pytest 化——已手工 up→down→up 带数据验证,项目无迁移测试基建,记录代替)。
- **⑦ 流程账本**:变更记录先行 + 打算↔实际差异回填;TODO 勾项;提交引用 0066、全英文。

**confirmed(10,含两轮)**:
1. (major)**`rotate_due` 无单账号失败隔离 + CLI 攒批打印——中途失败吞掉已 commit 密钥**(stdout 是唯一导出点;重跑不补,该账号已不再 due → 宽限尽即锁死;且与代码注释「一个失败不拖累其余」自相矛盾;security/correctness 双 lens 同源)→ `rotate_due` 改 async generator 逐个产出(`RotatedKey | RotationFailure`)+ 单账号 try/except 继续 + CLI **commit 即打印**(flush)+ 失败打 stderr、退出码 1 + 失败隔离测钉(monkeypatch 首账号瞬断,次账号照常且同轮产出、失败者原封仍 due)。
2. (minor)**`rotate_one` commit 后二次回读版本 = 「已换未导」窗口**→ `rotate_kuser` 改 UPDATE…RETURNING 同语句回版本(sqlite ≥3.35/pg 均支持),窗口收敛为零。
3. (minor)**CLI `issue` 硬打「K_user v1」而 `--reset` 补发实为旧 ver+1**(误导管理员对账)→ `issue_login` 返 `(新版本, 拒因)`,CLI 如实打印 + 测钉。
4. (minor)**storage.md 无锁不变量句仍只列 `SET hash_password`**(0066 后写集已是 hash/nickname/k_*,且 issue INSERT 带 points 的安全论证无处落笔;两处代码注释还引用着它)→ 扩写该句 + 模块头注同步。
5. (minor)**账本测数陈旧**(记录 684/15/+5,现实 688/17/+7)→ 两处更新。
6. (major,首轮)**天→秒换算与 rotation/grace 参数序在 day 面 API 无钉**(对调两参不红)→ `test_rotate_one` 补 until 值断言。
7. (minor,首轮)**决策 2(k_cur_until 过期不拒登)无钉**(有人把「过期拒登」写进登录路径不红)→ `test_overdue_k_cur_still_logs_in`。
8. (minor,首轮)**宽限边界 `now == k_prev_until` 恰端点无钉**→ 宽限测改恰取端点(`<=` 写成 `<` 必红)。
9. (minor,首轮)**重放守卫 × 双钥路径无钉**(守卫挪进 k_cur 单臂不红)→ `test_replayed_old_key_blob_rejected`。
10. (nit,首轮)**NULL ver 的 coalesce 兜底无钉**→ `test_rotate_dirty_null_ver_recounts_from_one`。

**refuted(5)**:①「强制轮换给疑似泄露钥留满额宽限」——`issue --reset` 即零宽限路径且 auth.md 明写分工(rotate --name=正常宽限 / issue --reset=零宽限),CLI 当场打印宽限时长,残余在接受面;②「cron 无人值守与 stdout 唯一导出矛盾」——文档约束的是**去向**(勿入 git/日志采集)非强制 TTY,管理员私有信箱/0600 文件在同一信任域(DB 本就明文存全部 K_user);③「issue 对既有账号静默忽略 --nickname」——help 已写明二参仅新行生效,反向「拒绝不匹配」会破默认 nickname=name 的正常补发,且 CLI 写 nickname/points 会破 0065 CAS 联动与列不相交前提;④「auth.md:62 残留 k_user」——历史叙述(0056 当时列名),现行契约行(69/76-84/124)已全改;⑤(首轮)「迁移手改无 pytest」——已手工带数据 up→down→up 验证,无迁移测试基建,记录代替。

**对抗核实(crux)**:①边轮边出的完备性:密钥自 commit 至打印之间不再有任何可失败操作(RETURNING 并回 commit、print 紧随 yield),进程被杀最多丢「正在轮的这一个」且其未 commit(事务原子)→ 重跑仍 due;②失败隔离后「重跑即补」重新成立(失败=未 commit=仍 due,有测钉);③双钥登录不扩攻击面:第二次尝试仅当 `k_prev` 非 NULL 且宽限内,fail-closed 逐层(NULL until 拒/过期拒/错钥拒),401 统一不泄败因,重放守卫在匹配之后统一生效(测钉)。0 未处置发现。
