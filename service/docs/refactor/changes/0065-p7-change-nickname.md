# 0065 · P7 改昵称 POST /user/nickname(仅大厅 + DB/会话表/连接三处联动)

日期:2026-07-06 · 范围:`app/db/queries.py`(`load_identity_by_name`/`nickname_taken`)、`app/db/user_writes.py`(`update_nickname`)、`app/auth/session.py`(`SessionStore.rename_nickname`)、`app/rest/profile.py`(新工厂 `make_nickname_router`)、`app/shell/lifespan.py`(挂载)、`tests/rest/test_change_nickname.py`(新)、`tests/auth/test_session.py`(rename_nickname)、`docs/rest.md`/`docs/presence.md`/`docs/lobby.md`/`docs/refactor/TODO.md`。落 [rest.md](../rest.md)/[presence.md](../presence.md) 的改昵称设计(仅大厅 + 连接重挂),P7 profile 最后一件。

## 背景 / 为什么

`nickname` 是 `world` 的键(座位/`contributed`/ConnectionManager 全按它),在用时改会键错乱——故设计定死「**仅当不在任何游戏房**才能改」(lobby.md/rest.md/presence.md 三篇已设计,机制原语 `ConnectionManager.rename` 已随 0037 落地)。本砖把 REST handler 落地:大厅用户不在 `world.users`,改名只动 **DB + 会话表 + 连接键**三处 shell/DB 状态,core 全程无涉。

## 关键设计决策

1. **判定「在房」用 DB 的当前昵称查 Presence**:身份 = 会话 `name`(不可变)→ DB 读 `(uid, old_nick)`(昵称以 DB 为准,会话表可能滞后)→ `presence.current_room(old_nick) is not None` → **403**。presence 读 committed world 可能滞后一拍——presence.md 早已记档接受(≤20 友善用户;要严须过 reduce,本规模不值)。**本砖把该竞态的最坏后果记准**(补 presence.md):若改名与同刻发出的 `join_room` 精确交错(join 以旧名富化入 inbox、尚未 reduce,改名判大厅放行),world 会以**旧名**装入成员而连接键已改**新名**——该成员收不到房间消息、其命令(origin=新名)`NOT_IN_ROOM`、`Cleanup` 见其 WATCHING 非 OFFLINE 而不清 ⇒ **幽灵占位直到重启**(房不空、不销毁)。触发需同刻双发(UI 不提供此路径),友善用户下接受;严格解仍是「改名过 reduce 守门」,留给需要时。
2. **三处联动的顺序 = DB 先行,内存随后**:① **CAS** 同步直写 DB(`update_nickname(uid, old_nick, new)`,`WHERE id=uid AND nickname=old_nick`——**自 review 从无条件 UPDATE 升级**:同账号并发双改名[双设备/同会话乱序双 seq,REST 滑动窗本就容并发]两个都无条件成功会让 DB/会话表/连接键各随一个赢家**永久发散**,还铸出「无 DB 行背书的孤儿连接键」可被他人改名撞上 → 跨账号身份错配;CAS 后输者 0 命中 → 409 且跳过内存联动)→ ② `SessionStore.rename_nickname(name, new)`(该账号**全部**会话,含其它设备)→ ③ `conns.rekey(live_conn, new)`(**自 review 从按键 `rename(old,new)` 升级为按对象**:handler 在 await 前捕获本人 live 连接,rekey 按 `is` 判定只动该对象——按键 pop 在 await 窗内若键已被并发 rename/顶替动过会误挂**他人**连接;new 键被孤儿占则覆盖 + WARNING)。②③ 纯内存同步、其间无 await ⇒ 原子;DB 写失败/CAS 输则 ②③ 未动(无半改,有测钉)。
3. **唯一性 = 预查 + DB 约束双保险**:先 `nickname_taken(new)` → **409**(干净拒);写时再兜 `IntegrityError` → 409(并发窗:预查与写之间有 await,两请求可交错,`User.nickname unique=True` 是最终裁判;有 monkeypatch 失明预查的测钉)。
4. **校验分层(承 0064)**:信封不过 401;非串/空/**首尾空白**(" Bob" 与 "Bob" 视觉同名键不同 = 冒充面,自 review 补)/超长(>50,对齐 `User.nickname max_length`)/与旧名相同 → **400**;在房 → **403**;撞名/CAS 输 → **409**;DB 错/会话 name 无行/presence 未接线 → **500**。
5. **不广播、不动 world**:大厅用户无 world 状态;房内成员名单/座位不涉及。改名对他人可见性 = 下次照面(DM 按新 nick、排行榜新 nick)。
6. **残留小账(记档,均无害)**:① Timer `_liveness` 里旧 nick 的条目不迁移——到期投 `Cleanup(old_nick)`,reduce 见不在房 no-op(已验 `_cleanup` 首臂);新 nick 下一帧起重新续命;大厅用户本无可清理的 world 态。② dev 明文端点 `?nick=` 只认 `DEV_USERS`,dev 用户改名后只能走加密 `?sid=` 连接;**且自 review 补堵**:改名后旧名在 DEV_USERS 仍在、DB 已无行——若放行会铸「无 DB 背书的孤儿连接」(`_build_join` 还会按 nick 错配他人行),故 `dev_ws` 加「DB 行仍在」守门(无行 → 4404)。③ 与 0060 dev「name=nickname」耦合仅在种子时刻,改名后 name 不变(登录不受影响)。④ **登录与改名的 TOCTOU(接受)**:`/user/login` 读完 DB 昵称、铸会话前的一个 await 窗内若同账号改名,新会话带旧昵称且 `rename_nickname` 已跑完抓不到它——该会话 ws 握手会以旧名登记连接(孤儿键类)。窗 = login 内单个 await 且须同账号同刻双动作,友善用户不可达;要闭须 login 铸会话时二次读或全局串行化,不值。

## 打算改什么

- `app/db/queries.py`:`load_identity_by_name(sessionmaker, name) -> (uid, nickname) | None`;`nickname_taken(sessionmaker, nickname) -> bool`。
- `app/db/user_writes.py`:`update_nickname(sessionmaker, uid, new_nick)`(定向 UPDATE;IntegrityError 由调用方兜 409)。
- `app/auth/session.py`:`SessionStore.rename_nickname(name, new_nick) -> int`(该账号全部会话改 nickname,返回条数)。
- `app/rest/profile.py`:`make_nickname_router(get_sessionmaker, session_store, get_presence, conns, now)`(独立工厂,免动既有 `make_profile_router` 签名/测试)挂 `POST /user/nickname`(信封内参 `{new_nickname}`)。
- `app/shell/lifespan.py`:挂载(`get_presence=lambda: shell.presence` 迟绑——presence 在 `setup()` 后才建)。
- tests:test_change_nickname(happy 三处联动 / 无连接仍成 / 在房 403 / 撞名 409 / 同名·空·超长·非串 400 / 未知 sid 401 / DB 错 500 / 会话无行 500 / presence 未接线 500 / 路由注册)+ test_session 补 `rename_nickname`(多会话同改 / 他账号不动)。
- docs:rest.md(§用户资料 改昵称落地)、presence.md(REST handler 落地)、lobby.md(改昵称落地)、TODO(P7 profile 闭环)。

## 实际改了什么(与「打算」对照)

与「打算改什么」一致落地,外加自 review 抓修五件(见下 confirmed):CAS UPDATE(决策 2①)、`ConnectionManager.rekey` 按对象重挂(决策 2③)、首尾空白 400(决策 4)、dev 端点「DB 行仍在」守门(决策 6②)、登录 TOCTOU 记档(决策 6④)。测试从计划面扩到 **nickname 21 + rekey 3 + session 1**;共 654→**662**。

## 自 review

对照 [review.md](../review.md) 逐维 + **对抗式多智能体复审(2 lens finder × 反驳验证者,13 agent)**:**10 confirmed(全修/记档)+ 1 refuted**。

- **① 分层 / 不变量**:全在 shell/DB(REST 端点、queries、user_writes、SessionStore、ConnectionManager);core/world 零触碰(改名仅大厅,world 无键);CAS 写与 PersistWriter 列不相交(`SET nickname` vs `SET points`)⇒ 沿 0064 无锁前提。
- **② 代码↔文档同步**:rest.md(CAS/rekey/错误分层含首尾空白)、presence.md(步 1-3 全按落地形改写:DB 名判定、CAS、rekey 按对象;竞态最坏后果记准)、lobby.md(line 19 补「+ 连接键」+ §改昵称落地)、TODO;`ConnectionManager.rename` 前提注释仍准(rekey 是并发窗版本,rename 留给无窗调用方)。
- **③ 文档↔文档一致**:0065 ↔ rest.md ↔ presence.md ↔ lobby.md ↔ TODO 一致;测数 662。
- **④ 数据模型**:`update_nickname` 返 bool(CAS 命中与否);`rekey(conn, new)` 语义注释齐(is 判定/孤儿覆盖/已顶替只改自身)。
- **⑤ 规范合规**:`_NICKNAME_MAX_LEN` 具名(注明对齐 models);日志无昵称外敏感字段;中文注释讲「为什么」(CAS 之因/按对象之因/空白冒充面)。
- **⑥ 测试充分**:nickname 21(happy 三处联动 / 无连接 / 在房 403 / 撞名 409 / **IntegrityError 兜底 409 + 内存原封**[失明预查] / **CAS 输者 409 + 跳过联动**[陈旧 identity] / **会话昵称陈旧而 DB 为准**[403 按 DB 名 + rekey 按 DB 名捕获] / 同名·空·**首尾空白 ×2**·超长·非串·缺参 400 / 401 / 500×3 / 路由)+ rekey 3(移对象 / 被顶替不动表 / 孤儿覆盖后 unregister 无害)+ session rename_nickname 1(多会话齐改/他账号不动)。
- **⑦ 流程账本**:打算↔实际差异上记;TODO 更新;提交引用 0065、全英文。

**confirmed(10)**:
1. (major)**同账号并发双改名致三处发散 + 孤儿键跨账号错配** → CAS UPDATE(`WHERE id AND nickname=old`)+ 输者 409 跳联动 + 测钉。
2. (minor)**改名 commit-到-resume 窗内旧名被他人抢注 → `rename` 无碰撞防护误挂他人连接** → handler await 前捕获对象 + `rekey` 按 `is` 判定 + 孤儿覆盖 WARNING + 测钉(被顶替不动表)。
3. (minor)**登录 racing 改名铸出陈旧昵称会话** → 记档接受(决策 6④:窗极窄、须同账号同刻双动作)。
4. (minor)**dev `?nick=` 收改名后的旧名 → 孤儿连接 + `_build_join` 错配** → `dev_ws` 加 DB 行守门(4404)。
5. (nit)**首尾空白昵称 = 视觉冒充面** → `new_nick != new_nick.strip()` 拒 400 + 参数化测 ×2。
6. (major)**「判定/重挂用 DB 名非会话名」无测钉**(全部测试两者恰相等,换成 session.nickname 全绿)→ 补陈旧会话昵称测(403 按 DB 名 + rekey 按 DB 名)。
7. (minor)**「DB 写失败 ⇒ 内存未动」无内存侧断言** → IntegrityError 测补会话/连接原封断言。
8. (minor)presence.md:37 仍写 `Err(CANT_CHANGE_NICK_IN_ROOM)`(码不存在)+ 无 DB 名限定 → 改写步 1-3。
9. (nit)lobby.md:19 「只动 DB + 会话表」漏连接键 → 补。
10. (minor)本段自 review 占位 → 此刻回填。
**refuted(1)**:「IntegrityError 兜底分支未测」——`test_race_integrity_error_maps_409` 失明预查逼出该分支(finder 漏看)。

**对抗核实(crux)**:①CAS 完备性:并发双改名任意交错下,后 commit 者 `WHERE nickname=old` 必 0 命中(先者已改)→ 409 无联动;三处只随唯一赢家。②rekey 安全性:`is` 判定 ⇒ 永不摘错键;唯一可覆盖的 new 键持有者是「DB 无行背书的孤儿」(CAS 后正主不可能占),覆盖后孤儿 unregister 的 `is` 判定保证退出无害(测钉)。③幽灵占位竞态(rename×join_room)仍在——presence.md 已按最坏后果记准,升级路径(改名过 reduce)保留。0 未处置发现。
