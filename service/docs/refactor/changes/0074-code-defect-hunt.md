# 0074 · 第六轮:纯代码缺陷猎杀(**换靶不等于免检**——见文末教训)

日期:2026-07-29 · 性质:**缺陷猎杀 + 即修**(与 0072 的「符合性审计」互补:那轮问「代码是否守文档」,本轮问「代码本身是否会错」)· 触发:用户「继续发现代码问题」。

## 与前五轮的分工

0072 五轮工作流把**文档↔代码一致性 + 架构合理性**挖到收敛(8 条原发现 + 36 条新发现全部对抗验证)。本轮**换的是「找什么」的靶**——只打纯代码缺陷(crash / correctness / security / resource-leak / data-loss / logic-bug),专攻此前覆盖最薄的面:

> ⚠️ **换靶只改「找什么」,绝不改「改代码时要读什么」**。本轮因把这点表述成「不再查文档一致性」而实打实翻了一次车(0074·B 误改),教训见文末「反思」。凡动行为就必须先读该行为的设计文档——这是 coding_principle 的硬规则,任何轮次都不豁免。

| 此前从未审过 | 本轮靶向 |
|---|---|
| `lib/ttxsgm` 国密库内部(SM4/SM3/KDF 实现) | ✅ gm-sm4 / gm-sm3 |
| 真扑克规则语义(超越 rules.md 符合性) | ✅ betting-semantics / sidepot-math / start-hand-edge |
| reduce 可达崩溃点(assert/StopIteration/越界) | ✅ reduce-crash |
| codegen 生成器正确性 | ✅ codegen |
| 迁移内容(不只是链完整性) | ✅ migrations |
| K_user CLI 写路径 / 前端实现 | ✅ kuser-admin / frontend |

**已知未修项(R1/R2/N2/N3/N4/N5/N9/N-e32/N-e9/N-e16/N-r4)在 prompt 里列为「报了算废」**,避免与 0072 重复劳动。

## 编排与执行

15 个靶向 finder(high)+ 每条新发现 2 名对抗验证者(要求**实跑 repro**,跑不出即 REFUTED)+ critic。第一批 34 agent 中 25 个完成后撞额度(6 个核心 finder + critic 中断),**中断部分已另起 r6b 续跑**(见文末)。

> **验证纪律升级**:本轮要求验证者「必须亲手用 .venv 跑最小复现」——这直接杀掉了多条「代码属性成立但运行时不可达」的报告(见 REFUTED 清单)。

## 已确认并**已修复**的缺陷

### 0074·A · `authenticate` 巨整数 ts 触发 OverflowError 逃逸(medium · crash + security)

- **机理**:[credentials.py](../../app/auth/credentials.py) 的 `try/except (ValueError, KeyError, TypeError)` 只裹到 `ts = payload["ts"]`;其后的 `math.isfinite(ts)` 与 `float(ts)` **在 try 之外**。`json.loads` 会把 400 位数字字面量解析成 Python `int`(不是 inf),而 `math.isfinite(10**400)` / `float(10**400)` 抛 **OverflowError**——不属被捕获三类,直接逃出 `authenticate`。
- **后果两重**:① [login.py:71-79](../../app/rest/login.py) 对 `authenticate` **无 try 包裹**(其唯一 try 只裹 `load_user_for_login`),异常冒到 FastAPI → **500**,破了本模块 docstring 明写的 fail-closed 铁律(同文件 line 63 注释正是「不让原始异常冒成 500 泄故障 vs 认证之别」);② 构成**密钥确认预言机**——错 `K_user` 解密得乱码 → `json.loads` 抛 ValueError → 被捕获 → 401;**正确 `K_user` + 巨整数 ts → 500**。攻击者据「500 vs 401」判定 K_user 猜测是否正确。
- **主审计者实跑复现**(非仅静态推演):正常 ts → `LoginProof`;`ts=9×400` → `OverflowError: int too large to convert to float` 逃逸;错 K_user + 同 blob → `None`(401)——三点齐备,预言机成立。
- **修复**:ts 校验改为「先 `isinstance` 拒 bool/非数值 → `try: float(ts) except OverflowError: return None` → 再 `isfinite`」,一次转换同时挡住巨整数与 NaN/±Inf。测试 `test_huge_integer_ts_rejected_without_escaping`(±巨整数两向),**变异验证**:去掉 OverflowError 保护 → 该测必红。

### ~~0074·B~~ · `_disconnect` 不重算免盲投票 —— **误报,非缺陷;已回滚**

- **我当时的判断**:`_voters()` 要求 `READY_TO_PLAY`,故掉线转 `OFFLINE` 即退出投票人集,与「离场/坐出/起身」同为减员,而 `_disconnect` 是唯一没挂 `_maybe_resolve_entry_vote` 的路径 → 判为漏挂,改成一行挂上,并做了对照实验(坐出通过 vs 掉线悬挂)与变异验证。
- **事实**:[rules.md ①.15](../../rules.md) 明写 **「`voters` 每次实时重算(不缓存),故断线者(`OFFLINE` ≠ `READY_TO_PLAY`)在下一结算点自然不计;**不为断线单独触发通过**」**——这句与实现**同批**写于 0020(`git log -S` 确认),是**有意的设计决策**,不是漂移。
- **设计为什么对(我事后才想明白)**:断线**可逆**——占座窗口(`LIVENESS_TIMEOUT`)内可重连,重连后仍是 `READY_TO_PLAY` 投票人;免盲是**全票通过制**,若断线瞬间按「减员」结算,等于**剥夺一个可能三秒后就回来的玩家的否决权**(他回来会发现"我还没投,怎么就免了")。而离场/坐出/起身是**主动且不可逆**的退出,触发重算才合理。断线者真不回来时,`Cleanup` 走 `_begin_leave → _evict → _maybe_resolve_entry_vote` 自然重算——所以我原先写的「**永不**通过」也是错的,实为「延迟到 Cleanup 或下一结算点」。
- **处置**:代码回滚;原测试改写为**反向钉** `test_voter_disconnect_does_not_trigger_vote`,把「断线不单独触发」这条设计钉住,防日后再被当 bug 改掉;`_disconnect` 处补注释写明理由与出处。
- **根因**:见文末「反思」——我把本轮定位写成「不再查文档一致性」,于是改行为前没读 rules.md。

**测试基线**:712 → **713**(A 一条 +1;B 已回滚,其反向钉替换原测试,不增净数)。

## REFUTED(验证者实跑后推翻,记档避免重复报)

这批的共同模式是「**代码属性成立、但运行时不可达**」——正是本轮要求「验证者实跑 repro」筛出来的:

| 报告 | 为何 REFUTED |
|---|---|
| `sm4_cbc_dec` 对空/非 16 倍数密文抛异常;PKCS#7 去填充无校验(填充字节=0 不剥离、>长度时负索引截断) | **库级属实且实跑复现**,但**每个 app 入口都有防御**:`open_envelope` 先验 MAC 再解密(篡改的密文过不了 MAC)、`authenticate` 在喂 `sm4_cbc_dec` 前显式挡非 16 倍数 blob([credentials.py:31](../../app/auth/credentials.py),注释就写着「免把畸形输入喂给裸去填充」)。裸库脆弱但无可达路径 → 不算本轮缺陷(**若日后新增入口须自带同款守卫**) |
| codegen 对非字符串 `Literal` 无条件加引号(number→string、bool→"True") | `_ts_type` 属性成立,但当前代码库**无任何非字符串 Literal 字段**,产不出错误 TS |
| codegen 对 `list/dict/float/set` 抛 TypeError 无法生成 | 白名单设计**有意 fail-loud**:未支持类型立即报错而非静默生成错 TS,且漏生成有 `test_codegen_uptodate` 守门 |
| 新增消息忘登记 `SERVER_MESSAGES` 被静默漏生成 | 机制属实,但属「流程/守门」非运行时缺陷;且被引用类型有 `missing` 检查 |
| 前端 `evaluateHand` 漏判 A-2-3-4-5 轮子 / 6-7 张不做 best-5 | **死代码**:`grep evaluateHand frontend/src` 仅定义处,零调用;牌型判定在服务端 core。逻辑瑕疵真实但无运行时后果(一票 PARTIAL 一票 REFUTED,按不可达取 REFUTED) |

## 中断与续跑

第一批因会话额度中断 6 个**核心** finder(reduce-crash / betting-semantics / start-hand-edge / connection-race / timer-lifespan / kuser-admin)与 critic——**这几个恰是最可能出真 bug 的面**。已另起 `code-defect-hunt-r6b` 续跑(同款靶向 + 单验证者要求实跑 + critic),结果回填下节。

> 中断批的 `enum-domain` finder 结果由主审计者从 `journal.jsonl` **抢救**并亲自复核 → 即上文 0074·B。

## r6b 续跑结果(19/19 agent 零失败)

6 个核心 finder 全部产出,**9 条经验证为真**(7 CONFIRMED + 2 PARTIAL)、2 条 REFUTED。其中 **4 条报告指向同一根因**(改昵称 REST 的 await 窗口),已合并。下面两条**已修**,其余登 TODO。

### 0074·C · 改昵称的「仅大厅可改」检查与内存联动之间隔着两次 DB await(**high** · data-loss)

- **机理**:[profile.py:122](../../app/rest/profile.py) 读 committed world 判「不在房」→ 放行;其后 126 `nickname_taken` / 129 `update_nickname` 两次 DB await;141-143 **无条件**做内存联动(会话表 + 连接键),**没有重新判是否已进房**。窗内 GameLoop 完全可以提交这个用户的 `JoinRoom`——此时 world 以 **old_nick** 为键,而 shell 不写 world(不变量 2),于是「world / DB / 会话表 / 连接键」四处永久发散。
- **连锁后果**(验证者以真 sqlite + 真 `GameLoop.run` + 真 reduce + 真 `_build_join`、**零 monkeypatch app 代码**实跑逐条观察到):
  1. `Broadcast` 按 `users_in_room` 的 old_nick 查 `conns.get` → None,该用户此后收不到任何房内消息;
  2. 他的一切命令 `origin=new_nick` → `_target_room` 查不到房 → **一律 NOT_IN_ROOM,连 `LeaveRoom` 都发不出去,无法自救**;
  3. `Disconnect(nick=new_nick)` 同样落空 → 幽灵 old_nick 永不转 OFFLINE → `_cleanup` 不回收 → **房永不为空、座位与筹码永久滞留**(空房销毁归一再也触发不了);
  4. 再 `JoinRoom` 时单房间约束查的是 `nick in work.users`(对 new_nick 不成立)→ 放行,`_build_join` 按 uid 重读同一笔积分 → **world.users 里同 uid 出现两份**,分处两房各 100 分。
- **0065 的自 review 防住了「窗内键被他人 rename 占走」(rekey 按对象 `is` 判定),却没想到「窗内自己进房了」。**
- **修复**:内存联动前**窗后复查** `presence.current_room(old_nick)`;已进房则用同款 CAS 把 DB 改回 old_nick(四处回到一致)、返 403(与窗前在房同码);回滚未命中则 CRITICAL + 500(留人工介入)。测试 `test_join_room_during_await_window_reverts_and_403`(在 `sm` getter 第 2 次调用即窗内塞 JoinRoom),**变异验证**:删掉复查块 → 必红。
- **验证者修正(已采纳进上文表述)**:原报告称「两份 PointsWrite 同键互相覆盖 → 已结算积分立即被毁」不准——幽灵半份收不到任何命令、自身产不出 `PointsWrite`;要真正支配两份筹码需再走一步(先 `LeaveRoom` 结算、再把昵称改回去接管幽灵 `UserState`)。定性与严重度不变。

### 0074·D · `PersistWriter.drain()` 的 deadline 罩不住 flush 本身(**high** · 进程无法退出)

- **机理**:[persist.py `drain()`](../../app/shell/persist.py) 只在**循环顶部**判 deadline;若 `await self.flush_once()` 内部的 DB commit 挂起(无响应/锁等待),永远走不到下一次判断 → drain 无限挂 → `DevShell.stop()` 永不返回、进程无法优雅退出。db.md 承诺的「有界,超 `DB_DRAIN_TIMEOUT_MS` 放弃并 CRITICAL」对这一情形**形同虚设**。
- **主审计者实跑**:`drain_timeout_s=0.3` + 永不返回的 persister → 3.0s 后仍未返回。
- **非 0073 引入**:`git show HEAD~1` 比对确认 drain 逐字未变,是 0025 起的原有缺陷。
- **修复**:`await asyncio.wait_for(self.flush_once(), timeout=remaining)`(remaining 由 deadline 实时算),超时 CRITICAL + 返回;节流 `sleep` 也收进 deadline 约束(防大 interval 拖过上限)。超时被取消的批由 `flush_once` 既有 `CancelledError` 臂回灌、不静默丢。测试 `test_drain_bounded_when_flush_hangs`,**变异验证**:去掉 `wait_for` → 必红。

**测试基线**:714 → **716 全绿**(C/D 各 +1)。

### 0074·G · 改昵称(rekey)落在 DM 路由的 DB await 窗内(**medium** · data-loss)

- **机理**:[messaging.py](../../app/shell/messaging.py) 的 `uids = await load_uids_by_nicks(sm, (conn.nick, to_nick))` 用**调用时**的 `conn.nick` 建表;await 期间改昵称的 `ConnectionManager.rekey` 会**就地改写** `conn.nick`;await 返回后 `uids.get(conn.nick)` 拿**改写后**的新 nick 去查**用旧 nick 建的表** → 必然 miss → 私信**静默不落库** + 回发假 `INTERNAL`。`route_dm_mark_read` 同款(已读游标同样丢失)。
- **修复**:进路由即快照 `sender_nick`/`reader_nick`,建表、查表、实时投递的 `from_nick`/`reader_nick` **全程用同一快照**——键与表天然一致,且落库身份与投递身份同源。测试 `test_rename_during_uid_lookup_does_not_lose_dm`(在 sessionmaker getter 里触发真 `rekey`),**变异验证**:查表行改回 `conn.nick` → 必红。

### 0074·H · `_buy_in` 的「局中」判据看状态而非「是否本手 Player」(**medium** · correctness)

- **机理**:[reduce.py `_buy_in`](../../app/core/reduce.py) 判 `users_in_room[nick] is PLAYING`,而真正要守的不变量是「筹码是否已锁入本手」。二者在**手内掉线**时分叉:`_disconnect` 置 OFFLINE 但玩家仍留在 `hand.players`,按状态判即放行买入;而 `_start_hand` 早已 `in_game_points = points; points = 0`,此时加筹使手尾 `final - initial` **凭空多出买入额**,落库 HandRecord 与 REST `/hands` 的 `net` 全错。
- **主审计者实跑**:在线买入 → `HAND_IN_PROGRESS`;掉线后同一买入 → **放行**,`seat.points` 0→50 而 `in_game_points` 仍 100。
- **修复**:判据换成 `_player_in_hand(room.hand, nick) is not None`(该 helper 本就是干这个的)。测试 `test_buy_in_rejected_for_offline_player_still_in_hand`,**变异验证**:判据改回状态 → 必红。
- **对验证者结论的修正**:原报告称「`_set_user_status` 同款错位」——**主审计者实跑推翻**:OFFLINE 者起身(→WATCHING)被 `userself_can_change_to` 挡住(`INVALID_STATUS_TRANSITION`),那处有兜底、不可达(我原本怀疑它能释放座位致 `_finalize_hand` 的 `assert s is not None` 崩溃,实跑证伪)。**只有 `_buy_in` 真漏**。
- **可达性如实标注**:OFFLINE 态下发命令需幽灵连接(N2)或旁路,独立可达性弱;本条按「判据错位 + 防御性加固」记 medium。

**测试基线**:716 → **718 全绿**(G/H 各 +1)。

### 已确认但**未修**(登 TODO;修复涉及连接层交错或既有防御设计权衡,留作单独批次)

| ID | 严重度 | 缺陷 |
|---|---|---|
| **0074·E** | high | **顶替链 A←B←C**:B 在 `_displace(A)` 的 await 窗内被 C 顶掉,恢复执行后仍 `cancel_cleanup` + 投 `Connect` → 把已 OFFLINE 的用户**复活成在线**并抹掉占座清理表 → 座位/筹码永久泄漏 |
| **0074·F** | medium | **改昵称窗内 ws 重连(顶替)**:119 行捕获的 `live_conn` 在窗内已被顶替,`rekey` 落 `else` 分支只改**死对象**的 `.nick`,活连接永久挂在 old_nick 键 → 用户在线却收不到任何消息。(0074·C 的复查**不覆盖**此路径:那是「窗内进房」,这是「窗内顶替」) |
| **0074·I** | medium(PARTIAL) | `_cancel_and_await` 吞掉 `stop()` **自身**的取消 → 优雅关闭超时/强制中止失效 |
| **0074·J** | medium(PARTIAL) | lifespan 的 `yield` 无 `try/finally` → 关闭路径异常/取消时 `shell.stop()` 被整体跳过 → 未落库积分全丢 + engine/协程泄漏 |

### r6b 的 REFUTED(实跑后推翻)

- 「不足额 all-in 错误重开了已行动者的加注权」——`betting.py` 的 `_reopen` 逻辑经实跑核对,与真扑克 incomplete-raise 规则一致,不成立。
- 「`conns.register()` 与 `_displace()` 在 try/finally 之外,窗内被 cancel 留僵尸连接」——被更早的结构挡住,不可达。

## 反思:「换靶」的表述如何直接导致了一次误改

本篇初稿把定位写成「换靶——**不再查文档一致性**」。这句话在「找什么」的层面没错(本轮不去主动挖文档漂移),但它被我自己在「**改什么**」的层面误用了:改 `_disconnect` 行为前,我没有去读 rules.md 里关于免盲投票重算的那一节,于是把 0020 明确写下的设计决策当成了漏挂的 bug。

对照实验、变异验证、718 全绿——**这些都拦不住它**,因为它们只能证明「行为被我改成了我想要的样子」,证明不了「我想要的样子是对的」。唯一能拦住的是**读那段行为的设计文档**,而那恰恰是我给自己免掉的一步。

**规则(适用于任何轮次,不因本轮"换靶"而豁免)**:

1. **凡动一处行为,先读该行为的设计文档**——尤其当"缺陷"表现为「A/B/C 路径都做了 X,唯独 D 没做」时:**先假设 D 是有意的**,去文档里找有没有写明理由,找不到再判为漏挂。不对称往往是设计,不是疏漏。
2. **可逆状态 ≠ 不可逆状态**:本例的分水岭是「断线可重连」vs「离场/坐出主动且不可逆」。把可逆态并进不可逆态的处理路径,就会剥夺当事人回来后的权利。类似判断在本仓库还出现在占座保留、`leaving`/`sitting_out_next` 延到手尾等处。
3. **给有意的不对称留反向钉**:0020 把决策写进了 rules.md,但没有测试钉住它,所以我改掉后全绿。已补 `test_voter_disconnect_does_not_trigger_vote`。**设计决策若只写在文档里而无测试保护,迟早会被"顺手修好"**。