# 0072 · 全量架构符合性审计(docs ↔ 代码双向 + 架构合理性)

日期:2026-07-14 · 性质:**审计记录**(本篇零代码/零设计文档改动;修复各随后续编号落地,完成后**回填本篇状态位**)· 触发:用户点名「检查代码是否符合 docs 架构设计、架构本身是否合理,可讨论后改架构」。

> 上一轮同类审计:0070/0071 承接的 A1-A3/B4(连接生命周期 + 房聊历史)。本轮是**全量第二轮**;发现编号避开 A/B 系列,用 **R(代码/设计缺陷,需改代码)· D(文档漂移,需改文档)· C(契约债,需排期)**。外部引用写作 `0072·R1` 形式。

## 结论一句话

**架构本身不需要改**:分层(shell/core)、单写者 GameLoop、工作副本 commit-or-discard、delayDB、加密信道的组合在「单进程、内网、≤20 人、积分非货币」前提下成立且实现纪律极好;9 条并发不变量经通读 + 机械核查全部成立,698 测试全绿。发现 **2 个架构级缺陷(R1/R2,均为「两个正确设计组合时的遗漏」)**、一批文档漂移(D 系列)、两条契约债(C 系列)。

## 方法

1. **通读比对**:service/docs 24 篇设计文档中 15 篇全文通读(见覆盖矩阵),与 app/ 对应实现逐条双向比对(代码违反文档 / 文档落后代码)。
2. **不变量机械核查(grep 全仓)**:
   - core 层 import 全集 = 标准库 + treys + `app.wire.server`(models.md 许可)——无 shell/FastAPI/SQLAlchemy/config;
   - core 无 `async`/`await`/`time.time`/`datetime.now`/`monotonic`(墙钟零读取);
   - `ws.send*` 全仓仅 [sender.py](../../app/shell/sender.py)(timer.py 命中为注释);
   - `create_task` 仅 receiver.py:54(Sender)+ lifespan.py:126-128(GameLoop/Timer/PersistWriter)四个许可点;
   - `world.rooms[...] =` 赋值仅 [shell/world.py](../../app/shell/world.py) `commit`。
3. **测试基线**:`pytest` **698 全绿**(2026-07-14,6.06s)。
4. **Alembic 链核查**:6 个迁移单链无分叉(`d07cf4b8828c → 79d1fd60fc7f → 7ff9cb0a8db1 → 010d8e8a08d7 → 49417b108733 → b8ca88a687af`)。
5. **多智能体工作流**(15 专项比对/红队 + 每发现 2 名对抗验证者):第一轮(run `wf_a6051bfe-bb7`)因会话额度耗尽 16 agent 全部中途失败、结果未回收;**第二轮已重跑**,结果见文末「工作流第二轮」。

## 覆盖矩阵(诚实记录深度;「未覆盖」项由工作流第二轮补)

| 专项 | 文档(读法) | 代码(读法) | 结论 |
|---|---|---|---|
| 并发不变量 1-9 | architecture.md、coding_principle.md(通读) | gameloop/world/dispatch/timer/sender/receiver/connection/lifespan(通读)+ 全仓 grep | ✅ 全部成立;唯不变量 2 措辞过时(D2) |
| 存储 / delayDB | storage.md、db.md(通读) | persist.py、orm_persister.py、models.py(通读),engine.py/queries.py(未逐行) | ✅ 高度一致;**R1** 除外 |
| core 规则 | core.md、models.md(通读),rules.md(经 core.md 与实现间接,未逐条) | reduce.py、rules/betting·blinds·sidepot、domain/commands/events/errors(通读),enums/records/cards/deck(未逐行,经测试间接) | ✅;**R2** 除外 |
| 连接 / 生命周期 / 定时 | connection.md、timer.md(通读) | connection.py、receiver.py、sender.py、lifespan.py、timer.py(通读) | ✅;文档侧 D3/D4 |
| 消息(房聊/DM) | messaging.md(通读) | messaging.py(通读)、dm_records(经 orm_persister/persist) | 代码 ✅;文档侧 **D1** |
| presence | presence.md(未读,经 presence.py 头注间接) | presence.py(通读) | ✅ |
| wire 契约 | wire.md(通读),wire-protocol-guide.md(未读) | client.py(通读)、server.py(类清单核对)、wire.gen.ts(存在性+头部核对) | ✅;**C2** |
| auth / 加密信道 | auth.md(通读) | channel.py(通读,与 auth.md §加密信道逐条对上);passwords/session/credentials/nonce/kuser(未逐行,经 auth.md 落地记录 + 测试间接) | channel ✅ |
| REST | rest.md、lobby.md、user.md(通读) | rest/lobby.py(通读);hands/leaderboard/login/profile/secure(未逐行) | ✅;**C3** |
| 配置 / 错误 | error.md(通读),config.md(未读) | gameconfig.py、config.py、errors.py(通读) | ✅;D4/D5 |
| 日志 | log.md(未读) | logsetup.py(未读;经 gameloop 消费面间接) | 未覆盖 → 工作流补 |
| 前端契约 | frontend/BACKEND_GUIDE.md(§1-3 抽查) | frontend/src 类型 import 关系(grep) | **C1**(已有 TODO 项) |
| dev / testing / 迁移用法 | dev.md、testing.md、db-migrations.md、review.md(未读) | — | 未覆盖 → 工作流补 |

---

## R 系列 · 架构级缺陷(需改代码;方案待用户定案)

### R1 · 动态房同名重建后,手牌记录被幂等逻辑静默丢弃

- **严重度:高**(手牌记录数据丢失;dev 固定房名下几乎必然复现)· 类别:0049(动态房)× 0011(dedupe 幂等键)两个各自正确的设计**组合时的遗漏**。
- **机理**:`dedupe_key = f"{room}:{hand.seq}"`([core.md:150](../core.md) / [reduce.py:470](../../app/core/reduce.py));`hand_seq` 是 `Room` 字段,**空房销毁 → 同名 `JoinRoom` 重建 → 新 `Room` 的 `hand_seq` 从 0 重数**([world.py:51](../../app/shell/world.py) 销毁、[reduce.py:617](../../app/core/reduce.py) 重建)。落库幂等走「SELECT by dedupe_key,在则跳过」([orm_persister.py:111-115](../../app/db/orm_persister.py))——新世代的 `"room:1"` 撞上上一世代已落库的同键行,**INSERT 被当成重放跳过**,记录静默丢失,直到新世代 seq 超过旧世代最大值。
- **佐证**:0049/0052 变更记录通篇未考虑跨「房间世代」撞键(0052 只处理了 room 过滤的 LIKE 脆弱);0071 修的恰是同构问题(chat_history 跨世代泄露),但没有回头看 dedupe_key。测试无「销房 → 同名重建 → 再打一手 → 断言落库」用例。
- **修复方向(二选一,待定案)**:
  1. **推荐 · dedupe_key 掺入世代标识**:`f"{room}:{seq}:{start_time 的 epoch 毫秒}"`。`start_time` 本就由 shell 盖入、存于 `Hand`(core 只携带不读),同一手重试/回灌时 key 不变 ⇒ **幂等性完整保留**;它是记录元数据,不参与任何游戏分支,不违「不引入 wall-clock 判据」红线(该红线管的是 staleness 判定,见 core.md「墙钟外移」)。改动:`_finalize_hand` 一行 + `dedupe_key` 相关文档/测试。
  2. 备选 · 建房时继承历史 seq:Receiver 建房前查 DB 该房名历史最大 seq,经 `RoomCreate` 带入初始化 `hand_seq`。代价:JoinRoom 多一次 DB 读、需给 seq 单独建列(现只在 dedupe_key 字符串里)、迁移;不如方案 1 收敛。
- **对抗验证:CONFIRMED(2/2)**——工作流第二轮两名独立反驳者均未找到可反驳点(逐行核了键构造/销毁/重建/幂等跳过四环,grep 证实无任何 seq 继承代码;db.md:86 把 dedupe_key 定位为「防重放保险」,其「同键=重放」假设正是被撞键打破的那条)。并给出**两条比本条原表述更宽的可达路径**:① **进程重启即撞**——房态只存内存,重启后同名房从 seq=1 重数,与重启前记录撞键,**不需要**「空房销毁」这一步;② 同世代内缓冲不去重(WriteBuffer 事件写逐条追加),两条同键写先后到 DB 时后者被跳过属预期,但跨世代撞键复用了同一条「跳过」路径故无任何日志。严重度维持**高**,且触发面上调:「重启或销毁重建皆触发」。
- **状态**:`[~]` **用户定案暂缓(2026-07-28)**:「R1 先不修」——保持已确认未修,不作接受取舍;修复方向(两案)与回归测试要求(销房重建 + 重启两路径)留档待启。

### R2 · Timeout staleness 判据(epoch)跨手会失效

- **严重度:中**(正确性缺陷;需多条件叠加,概率低但可构造)· 类别:staleness 方案设计盲区——timer.md/core.md 只论证了**手内**过期,未覆盖 **epoch 每手归零**。
- **机理**:`Timeout` 只带 `(nick, epoch)`([commands.py:103-106](../../app/core/commands.py)),`_timeout` 校验三条:有手 / `hand.epoch == cmd.epoch` / 行动者是 `cmd.nick`([reduce.py:547-555](../../app/core/reduce.py))。`epoch` 每手从 0 重数 ⇒ 三条都可能被**下一手**满足。
- **可构造交错**(FIFO 内):inbox 已排队 `[alice 的 fold(终结本手), bob 的 start_hand, Timer 本 tick 触发的 Timeout(alice, epoch=0)]`——fold 结束旧手(ClearAction 扑空,Timeout 已发出);start_hand 开新手且 alice 恰为首行动者(epoch=0);随后处理过期 Timeout:全部匹配 → **新手开局瞬间 alice 被自动 check/fold**。所需巧合:旧手 armed epoch 与新手行动者 epoch 相同(最易在 0)+ 同 nick + Timeout 在同一 tick 窗口挤进队列。
- **修复方向**:`TurnChanged` / `_ActionDeadline` / `Timeout` 各加 `seq: int`(= `hand.seq`,房内单调、非墙钟,完全合规),`_timeout` 比对 `(seq, epoch)` 双键。注:若 R1 采纳方案 1,seq 跨世代仍会复位,但叠加「同名房销毁重建 + 同 nick + 同 epoch + tick 窗口」后实际不可达;若要绝对封死可在 Timeout 校验里同带世代标识,属过度设防,不建议。
- **状态**:`[ ]` 未处置(小改动 + 构造该交错的回归测试)。

---

## D 系列 · 文档漂移(按 coding_principle「文档≠代码即缺陷」,应一批 truth-up,仿 0047/0067)

### D1 · messaging.md §房聊历史留有 0071 之前的整段旧文(内部自相矛盾)

[messaging.md](../messaging.md) 66-75 行(「房聊:房内内存环形历史」小节的正文)与同文件的节标题(0071 迁入 `Room.chat_history`)及契约 7 **直接矛盾**,全部是 0071 未清理的旧文:

- 68 行「决策·放 shell 不放 world……备选『chat_log 放 world.rooms[room]』——**否决**」——0071 用户定案恰是进 Room,此句现为反事实;
- 69 行「写入:dispatch 派发 Broadcast 时 `buffer.append`」——实际已移入 `reduce._room_chat`([dispatch.py:55](../../app/shell/dispatch.py) 注释明说不再在此追加);
- 71 行「**shell 协程不得读 world(不变量 2)**……shell 据报文房名直读缓冲,不读 world」——实际 [receiver.py:193-199](../../app/shell/receiver.py) `_serve_room_chat_history` 直读 `world.rooms`(0071 记档的只读豁免);
- 74 行「清理:**v1 房静态预置(lobby.md)→ 不销毁 → 无需清理**」——动态房自 0049 已是唯一模型。

0071 的「文档(计划)」写的是「§房聊持久化/环形缓冲**整节改写**」,自 review ② 也声称七处对齐——实际只改了节头与契约 7,正文段漏改。**状态**:`[ ]`。

### D2 · architecture.md 不变量 2 措辞与「只读 committed world 豁免家族」冲突

[architecture.md:130](../architecture.md) 不变量 2:「Receiver 从 DB 读数据(shell IO)是允许的,但它**读 DB、不读 `world`**」。现已有三处**记档合规**的只读豁免:presence([presence.py](../../app/shell/presence.py),0037)、`GET /lobby/rooms`([rest/lobby.py](../../app/rest/lobby.py),0048)、`FetchRoomChat`([receiver.py](../../app/shell/receiver.py),0071)——共同前提:只读已提交态、纯同步无 await(对唯一写者原子)、展示用可滞后、不做载入决策/实时裁定。顶层不变量没有描述该豁免家族,读者按字面会把三处合规代码误判为违规。**修法**:不变量 2 补一句豁免判据(把散在 presence.md/rest.md/0071 的口径收拢到顶层)。**状态**:`[ ]`。

### D3 · connection.md / lobby.md 的「待定」段陈旧

- [connection.md:206](../connection.md)「大厅/房间管理……**静态预置房;动态建房仍待定**」——动态房 0049 已落地,且同文件 lifespan 节自己写着「动态房——谁都可创建/空则消失」;
- [connection.md:207](../connection.md)「shell 侧的 DM 路由/房聊环形缓冲写读/登录补收**尚缺本文正式章节(待补)**」——是否仍要补,应显式决定(补齐或改指 messaging.md);
- [lobby.md:92](../lobby.md)「完整 **presence** 只读视图……**仍待单列**」——presence.md + presence.py 已落地(0037)。

**状态**:`[ ]`。

### D4 · 四处陈旧头注/注释(其中两处与已废弃的 JWT 决策矛盾)

| 位置 | 现文 | 事实 |
|---|---|---|
| [rest/lobby.py:7](../../app/rest/lobby.py) | 「dev 无鉴权(**P5 上 JWT,与 ws 两套**,见 rest.md)」 | 0057 定案**无 JWT**、REST 与 ws 同一会话密钥信封;rest.md 本身口径已正确 |
| [config.py:4/9/12](../../app/config.py) | 「未来 JWT 等基础设施/密钥」「**JWT_SECRET(P5)必须无默认**」 | 同上,P5 已全部落地且无 JWT;该头注的「未来密钥 fail-closed」原则可留,举例须换 |
| [shell/persist.py:5](../../app/shell/persist.py) | 「真实现 to_orm+session **留 P4 三**;dev 用 NullPersister 丢弃」 | OrmPersister 已落地(0028)且 lifespan 已接入(0029) |
| [shell/lifespan.py:1-5](../../app/shell/lifespan.py) | 「dev-only:**无鉴权/无加密**……**P5 国密信道落地即替换**握手/帧;**P8 lifespan drain 收尾**」 | 同文件即有加密 `/ws?sid=` 端点(0061)与反序 drain(0046);明文 `?nick=` 只是并存待退役 |

**状态**:`[ ]`(与 D1-D3 同批 truth-up)。

### D5 · 小项(低危,顺手清)

- `CANT_CHANGE_NICK_IN_ROOM`:[error.md:40](../error.md) 示意块与 [lobby.md:75](../lobby.md)「ws 侧的码**保留**给未来 ws 形态」都提及,但权威枚举 [errors.py](../../app/core/errors.py) 无此成员(error.md 自标「示意、以代码为准」,勉强合规;lobby.md 的「保留」易误读为已存在);
- [wire/client.py:89](../../app/wire/client.py) `FetchRoomChat` 字段注释「shell 不读 world 无法解析当前房,故带房名」——0071 后 shell 已直读 `world.rooms`,「带房名」仍对但**理由已过时**(现理由:免读 `world.users` 解析当前房 + 允许拉任意房历史);
- timer.md 伪码字段名 `nickname` vs 代码 `nick`(文档自标伪码,极低危)。

**状态**:`[ ]`。

---

## C 系列 · 契约债(非违规,需排期)

### C1 · 前端仍消费手写漂移的 poker.ts(**已有 TODO 项,非新发现**)

`PokerCard.tsx`/`PlayerSeat.tsx`/`utils/poker.ts` 仍 import 手写 [frontend/src/types/poker.ts](../../../frontend/src/types/poker.ts)(`chips`/`phase` 与后端 enum 漂移,architecture.md/wire.md 点名的反例);`wire.gen.ts` 现仅 emoji.ts 消费;前端尚无 ws client 与加密帧实现。**TODO W 段已登记**(「前端消费 wire.gen.ts:延后,随前端 WS client 集成」,0017 决策 8),本篇只确认现状未变,不重复登记。**状态**:`[~]` 既有排期。

### C2 · 「codegen 进 CI / pre-commit」只兑现了一半

[architecture.md:161](../architecture.md) 与 [wire.md 契约 2](../wire.md) 都写「生成步骤进 CI / pre-commit」;仓库实际**无任何 CI 配置、无 .pre-commit-config.yaml**,守门只有 `tests/wire/test_codegen_uptodate.py` 骑 pytest(wire.md 20-23 行如实记录了这一现状,architecture.md 口径偏乐观)。**修法二选一**:补一份最小 `.pre-commit-config.yaml`(调 `gen_wire_ts.py --check` + pytest),或把两文档口径改为「pytest 守门;CI/pre-commit 待基础设施」。**状态**:`[ ]`。

### C3 · rest/lobby.py 硬编码 `big_blind = 2 * small_blind`

[rest/lobby.py:36](../../app/rest/lobby.py) 内联 `2 *`,而权威常量 `blinds.BIG_BLIND_MULTIPLE` 已存在且 reduce 全部引用之——「大盲=2×小盲」的事实源出现第二份,违「无魔法数字/单一事实源」的精神(lobby.py 是 shell,import core.rules.blinds 合法)。一行改。**状态**:`[ ]`。

---

## 观察(非缺陷,记档供后续判断)

1. **`chat_history` 进 Room 的量变点**:0071 已记档「每命令深拷 ≤N 条消息」的代价(N=`ROOM_CHAT_HISTORY_SIZE`,现 50,Field 上限 1000)。若日后调大该配置,`PlayerAction` 级高频命令的 checkout 深拷成本随之线性涨——到时优先考虑 storage.md 预留的 `uRead`/`uWrite` 路径或把历史移回 shell,现在不动。
2. **加密信道实现与 auth.md 逐条一致**:encrypt-then-MAC、先验后解、常量时间比对、IV 每帧新鲜、ws 严格单调 seq / REST 滑动窗、ws/REST 密钥分域。已记档残余风险(sid 顶替 DoS、REST 反射 nuisance、重启 nonce 窗)评估合理,未发现新漏洞。
3. **grep 级不变量核查全部干净**(方法 §2),此结论可作为后续改动的回归基线:任何 PR 后重跑同组 grep 应保持零新增命中。
4. **poker.db 已被 gitignore**,工作区无泄漏。

## 跟进计划(登记进 [TODO.md](../TODO.md)「审计跟进(0072)」段)

| 项 | 载体 | 前置 |
|---|---|---|
| R1 修复 + 回归测试 | 单独 change(编号后续) | 用户在两方案间定案 |
| R2 修复 + 交错回归测试 | 单独 change(可与 R1 同批) | 无 |
| D1-D5 文档 truth-up 一批 | 单独 change(仿 0047/0067) | 无 |
| C2 补 pre-commit 或改口径 | 并入 D 批或单独 | 用户定案取向 |
| C3 一行改 | 并入任意批 | 无 |
| 覆盖矩阵「未覆盖」项 | 工作流第二轮回填本篇 | 已在跑 |

## 工作流第二轮(结果回填)

> 第二轮编排:① 对抗验证本篇 R1/R2/D1-D4/C2/C3 八条结论(每条 2 名独立反驳者);② 15 个专项 agent 在「已知发现」之外找新问题(重点覆盖矩阵中标「未覆盖」的 log/config/presence/dev/testing/rules 逐条/wire-protocol-guide/rest 端点细读/auth 组件逐行);③ 新发现每条 2 名对抗验证;④ 完整性批评者查漏。

**第一次执行(run `wf_6f9f02c7-71b`,2026-07-14)部分完成后再次撞会话额度**(32 agent 起跑、1.6M token 后 30 个中途失败):

- **R1:CONFIRMED(2/2)**,证据与「更宽可达路径」已回填至 R1 条目(进程重启即撞、无需销毁重建)。
- **R2 / D1-D4 / C2 / C3:未验证**(验证者全部因额度失败,零票——**不是被推翻**;首版编排脚本把零票误标 REFUTED,已修正为 UNVERIFIED)。
- 15 个专项 finder 与完整性批评者:全部未完成,覆盖矩阵「未覆盖」项**仍未补**。
- **第三次执行(resume,2026-07-27)**:再撞额度。R1 的两名验证者实际重跑(resume 缓存对大并行扇出未命中)并**再次 2/2 确认**——R1 累计 **4/4 全票 CONFIRMED**(两轮四名独立反驳者,均以反驳立场入场、均失败),且第二轮验证者补充:①「进程重启即撞」路径经 changes/0029 的端到端测试用例(固定房名 r1、dedupe_key=r1:1)佐证;② 即便绕过 SELECT 判重,dedupe_key unique 索引也会 IntegrityError 回滚**整批**(连累同批其它写)——撞键的伤害面比「丢一条记录」更宽。其余 7 条(R2/D1-D4/C2/C3)仍 UNVERIFIED。
- **第四次执行(改编排)**:三连败根因 = 验证者与重型 finder **并行**抢同一额度池 + resume 缓存不可靠。改为**串行分段**:先跑完 7 条结论验证(2×7,high)并即时落日志,再跑 15 个专项 finder(降 medium),新发现的对抗验证撤出工作流、由主审计者带完整上下文人工复核(定案后回填)。

**第四次执行(串行分段,run `wf_dab58ad5-e25`,2026-07-28)—— 首次完整跑通**(30 agent、~16min、1.67M token,零失败):

### 段1 · 7 条结论对抗验证:全部 CONFIRMED(各 2/2)

`R2=CONFIRMED D1=CONFIRMED D2=CONFIRMED D3=CONFIRMED D4=CONFIRMED C2=CONFIRMED C3=CONFIRMED`。加 R1 的 4/4,**本篇原报 8 条发现已全部经独立对抗验证坐实**。两处验证者带来的**修正**(已并入对应条目理解,修复时照此):

- **R2 可达性收窄(修正原「可构造交错」)**:原例 `[fold, bob 的 start_hand, Timeout]` 会被 `READY_TO_PLAY` 门挡死——手尾参与者一律转 `SITTING_IN/SITTING_OUT`([reduce.py:461-467](../../app/core/reduce.py)),开局只发且只准 `READY_TO_PLAY`([reduce.py:138-139](../../app/core/reduce.py)、236-239),被 armed 的 nick 在紧邻下一手必缺席或新手开不起来。**真实构造**须:① 在 fold 与 start_hand 间插入受害者自己流水线发的 `SetUserStatus(READY_TO_PLAY)`;② 叠加「新手中该 nick 恰为 epoch 0 首行动者」的**阵容变化**条件——同阵容 heads-up 免疫(庄位必轮转使新首行动者变对手),须旧 heads-up→新 4 人且旧庄位玩家恰成新 UTG(preflop 首行动 = players[2] = 原座 0);③ 需**失步/违规客户端**在旧手仍进行中的同一 `TIMER_TICK_MS` 窗口抢发 start_hand(诚实客户端序不可达)。**定性不变**:这是「服务端不得依赖客户端行为」意义上的正确性缺陷,严重度「概率低但可构造」成立;典型损害是 preflop UTG 被无辜折牌。**且见下方 N4:R2 拟议的 `(seq,epoch)` 双键仍封不住跨房场景,修复须一并处理。**
- **D1 范围收窄**:并非「66-75 行整段旧文」——66 空行、**65/67 行是 0071 的新文**(节头 + `Room.chat_history`/reduce 追加)、70/72/75 基本仍准;真正陈旧的是 **68/69/71/73(半句)/74** 五处。且 0071 台账(其 33/51/58 行)自称「§房聊节**整节改写**、七处对齐」——本条实为 **0071 声称完成却漏改**,性质是文档-代码分歧而非记档取舍。

### 段2 · 15 专项在已知发现外新找 40 条(**未对抗验证**,为省额度撤了验证者;下方 N 系列为主审计者去重后的待验证候选)

### 段3 · 完整性 critic:覆盖矩阵唯一悬空格 review.md 已由 critic 代读,结论干净(流程文档,交叉引用均实存、与现行 9 条不变量口径一致、无技术漂移)→ 覆盖矩阵 review.md 行可标「✅ critic 通读」。

---

## N 系列 · 第四次执行新发现(去重后,**待第五次工作流对抗验证**)

> 40 条原始发现经主审计者去重/归并(3 组重复:N1=原#1/#15/#30、N2=原#2/#7、D4 扩展=原#19/#23/#33)。**均为 UNVERIFIED**——段2 未挂验证者。**下方两条高价值项(N1/N4)证据链已足够强、主审计者初判成立**,其余待验证。修复一律待验证定案后。

### N1 · 离房→快速重进房在 flush 窗口内静默回退积分(**HIGH · 3 个独立专项同时命中**)

- **机理**:离房驱逐 `del work.users[nick]`(内存权威连根删,[reduce.py:770](../../app/core/reduce.py)`_evict`),但该用户最后一笔 `PointsWrite` 可能仍压在 delayDB 缓冲未落库(窗口 = `DB_FLUSH_INTERVAL_MS`,默认 500ms)。若此窗口内同 nick 再 `JoinRoom`,`_build_join` 从**滞后的 DB** 读积分([receiver.py:207](../../app/shell/receiver.py)`load_user_by_nick`)、reduce **无条件安装**([reduce.py:625](../../app/core/reduce.py)),随后该用户的新 `PointsWrite` 把**陈旧值固化**——正常操作(非崩溃)下的静默积分丢失/凭空得分。
- **与已知发现关系**:**R1 同族**(都是「跨生命周期复位:内存权威生命周期结束 → 缓冲中最新值既不在内存也未到 DB」),但 R1 丢的是手牌记录、本条丢的是**积分**(更敏感)。storage.md 只声明接受**崩溃窗口**丢失,未接受**正常运行**丢失——故是真缺陷非取舍。
- **主审计者初判**:成立(三个独立专项 invariants/messaging-lobby-user/lens-concurrency 各自独立命中,机理一致)。**严重度 HIGH**。修复方向候选:载入前先 flush 该 uid 的待写 / 驱逐时把待写「钉」在一个短存活镜像 / JoinRoom 命中缓冲中有未落 uid 时用缓冲值而非 DB 值。待验证定案。
- **状态**:`[x]` **已修(0073,2026-07-28)**——用户定案「运行期强制等落库」原语:`JoinRoom` 载入前两步屏障(`inbox.join()` + `PersistWriter.barrier()`,任一步失败 fail-closed 回 INTERNAL);N1 主钉 e2e + 关屏障必红反证 + barrier 穷举 9 测,707 全绿。见 [changes/0073](0073-persist-barrier-join-load.md)。

### N4 · Timeout 跨房使 R2 的 (seq,epoch) 双键仍失效(**MEDIUM · 直接影响 R2 修复方案**)

- **机理**:`Timeout` 不带 room,目标房在**处理时刻**按 `world.users[nick].room` 重新解析([world.py:26-33](../../app/shell/world.py)`_target_room`)。为 A 房 armed 的过期 `Timeout` 若在该玩家已 `LeaveRoom(A)`+`JoinRoom(B)` 后才被处理,会被投进 **B 房**并可能通过全部 staleness 校验。R2 拟议的 `(seq,epoch)` 双键**封不住**——seq/epoch 都是房内计数,B 房也可能有匹配值。
- **主审计者初判**:成立且**重要**——它是 R2 修复的必要补丁。修复须让 Timeout 携带并校验**房间身份(或世代)**,而非只加 seq/epoch。**R2 与 N4 应合并为一个修复**。待验证。

### 其余 38 条候选(UNVERIFIED,摘要;第五次工作流逐条 2 名对抗验证 + 去重)

**medium 档(优先验证)**:N2 慢客户端 QueueFull 丢连只 unregister 不关 ws/不 cancel → 幽灵命令源 + 同 nick 双 Receiver(原#2/#7,invariants+connection 双命中)· N3 `GameLoop.run` 无兜底 try,仅包 reduce 一行,checkout/commit/dispatch 异常杀唯一状态写者且无 watchdog(原#3)· N5 `SessionStore.revoke` 全仓零调用者——无登出/吊销通道,泄露应对后已建会话存活至 TTL(原#13)· N6 log.md「谁记什么」表要求的 Receiver 解析失败/连接建立、Timer 投命令三处日志实现均缺(原#18)· N7 「每房一 GameLoop,core 不变」演进承诺被 users 表全局共享写模型堵死(原#37)· N9 StateSnapshot 不投影 `entry_vote`,顶替/重连快照清空投票面板(原#39)· dev.md 多处「未来/预告」口径描述已落地 P5/P4(原#22)。

**low 档 · 规则边角**:奇数零头庄家距离 0 优先、heads-up 恒归 button(原#4)· 唯一 ACTIVE 是 BB 时 born-all-in 跳过 preflop 选择权(原#5)。

**low 档 · 文档漂移(多与 D 系列同族,建议并入 truth-up 批)**:rules.md ④ 断线「自动 fold」vs 实现「能 check 则 check」(原#6)· presence.md「统一只读 API」未兑现(原#8,另见 N-simplicity)· wire-guide §9 dev 端点 `?nick=` 任意昵称必败无报文(原#12)· auth.md REST 信封待办与已定案矛盾(原#14)· messaging.md 保留期「再留 7 天」vs 按 created_at 起算(原#17)· **D4 外延**:hands.py/leaderboard.py/config.md 三处 JWT 残留 + pyproject 死依赖 pyjwt + core/records.py 头注引不存在的 messages.py(原#19/#23/#33)· log.md 毒丸 ERROR vs 实现 CRITICAL(原#20)· testing.md 三层测试漏 auth/rest/wire(原#24)· lifespan.py stop() 硬编码行号引用漂移(原#25)· BACKEND_GUIDE 三处(大厅重连仍收 DM/房列表标「落库滞后」实为内存实时/dev 端点「上线移除」无环境开关)(原#27/#28/#29)。

**low 档 · 设计边角**:DM 已读游标无单调性防护,客户端可倒退游标(原#9)· db-migrations.md 两处示例配置会致启动崩/违反自家 create_all-vs-Alembic 铁律(原#10/#11)· Err.detail 中文既进日志又下发,与 log.md「日志英文」冲突(原#21)· `_evict` 不清 `waive_entry_for`,离房重进凭残留 nick 免盲(原#16)· scripts/scripts.py 原型孤儿脚本(原#26)· NullPersister 自 0029 无生产消费者(原#34)· Presence 4 方法 3 个零消费者(原#35)· profile.py 手抄 `_NICKNAME_MAX_LEN=50` 二份事实源(原#36,同 C3 模式)· refcount 多房迁移面被低估(单房假设渗进 wire/路由/解析)(原#38)· 重连 PLAYING 快照缺 `last_raise_size`,客户端算不出 min-raise(原#40)· Broadcast 收件人取 commit 后成员表,离场者收不到自己参与的结算(原#32)。

### 第五次工作流(N 系列对抗验证,run `wf_7cf98523-46e`,2026-07-28)—— 完整跑通

编排:9 条 medium+ 各 2 名反驳者(high),低危 3 批各 1 名核验者(medium)。**40 条候选去重为 36 条 distinct,结果:~34 条真实(30 CONFIRMED + 4 PARTIAL-但问题真实),1 条 REFUTED,N4 重归类为 R2 修复约束,N7 重归类为文档过度承诺。** 逐条:

**medium+ 档(9 条,双验证):**

| ID | 结论 | 定案 |
|---|---|---|
| **N1** 离房→flush 窗内重进静默回退积分 | **CONFIRMED(2/2)** | 六环机理逐点坐实、无守护、非崩溃取舍。**两处修正**:① 严重度 HIGH→**MEDIUM**(自作、≤500ms 窄窗、单用户自身栈、积分非货币、非攻击面);② **删「与 R1 同族」**——R1 是 HandRecordWrite 事件写 dedupe 撞键,N1 是 PointsWrite 状态写 evict→滞后读 lost-update,载荷/路径/机理/修复全不同,**独立缺陷非 DUP**(R1 的世代标识修复对 N1 完全无效)。双向可达:上把赢→离→重进=丢赢利;上把输→重进=**凭空刷分可主动构造**。 |
| **N2** 慢客户端丢连成幽灵命令源 + 同 nick 双 Receiver | **CONFIRMED(2/2)** | `dispatch._drop_connection`(dispatch.py:86-100)只 unregister/arm/投 Disconnect,**全文无 ws.close、无 cancel**。可达前提:**非对称慢客户端**(慢在读出站灌满 outbound、仍能投入站帧)。medium。 |
| **N3** `GameLoop.run` 非 reduce 异常杀唯一状态写者 | **CONFIRMED(2/2)** | 机理坐实:外层 try(gameloop.py:51-68)的 finally 只 reset_log_context 不 catch,唯一 `except Exception`(53-58)只裹 reduce() 一行;commit/_audit_applied/dispatch 抛异常冒出 handle 杀 run 循环,无 watchdog,与 architecture.md「接住→继续下一条」不符。**表述修正**:非「try 只裹 reduce」,而是「外层 try 裹全但不 catch,catch 只裹 reduce」。medium。 |
| **N5** `SessionStore.revoke` 全仓零调用者 | **CONFIRMED(2/2)** | grep 证实 revoke 仅测试调用,无登出/吊销通道,kuser CLI 够不到内存会话表。auth.md 声明 token「可吊销」为红线、issue --reset 为泄露应对,但已建会话仍活至 SESSION_TTL(≤86400s),唯一终止=重启。medium 设计缺口。 |
| **N6** log.md 要求的 Receiver/Timer 三处日志实现均缺 | **CONFIRMED(2/2),降 low** | 三项(解析失败 WARNING/连接建立 INFO/Timer 投命令 DEBUG)确缺;但 log.md 自身定「日志是值的旁路、失败降级吞掉、绝不影响命令处理」→ observability 漂移非正确性缺陷。**low**。 |
| **N9** StateSnapshot 不投影 `entry_vote` | **CONFIRMED(2/2*)** | `_state_snapshot`(reduce.py:634-672)两分支字段止于 your_hole_cards、无投票投影;`FreeEntryVoteUpdated` 仅投票事件时广播 → 重连/顶替者错过进行中投票公开态,按「拿快照整桌重建」契约会清空投票面板。(*一票误标 DUP=把台账里 N9 自身条目当成「已登记」,实为自指误判。)medium。 |
| **N-dev22** dev.md 以「未来/预告」写已落地 P5/P4 | **CONFIRMED(2/2)** | .env 表「未来 JWT 随 P5 无默认」已被 0057 去 JWT + 密钥落 poker.env 推翻;「迁移预告」段列的迁移早存在。D4 同族、落点不同。low 文档漂移。 |
| **N4** Timeout 跨房使 R2 (seq,epoch) 双键失效 | **SPLIT → 归类为 R2 修复约束** | 两验证者**一致确认**:seq/epoch 均房内计数(domain.py:36/46),不编码房间身份,故 **R2 修复必须让 Timeout 携带并校验房间身份/世代,不能只加 seq/epoch**。分歧仅在「独立危害是否可达」——需失步/违规客户端(诚实序下 Timeout 必先于 JoinRoom(B) 出队、跨房不可达)。**定案:不作独立缺陷,合入 R2 修复作为其设计约束。** |
| **N7** 「每房一 GameLoop,core 不变」被 users 表全局写堵死 | **SPLIT → 归类为文档过度承诺** | 两验证者**一致 PARTIAL**:三条机理(Work 整份拷 users、commit 整份替换、JoinRoom core 内跨房判定)属实,但**今天没有东西被打破**;问题是 architecture.md:191「core 不变」表述过宽。**定案:改为文档 truth-up——把承诺收敛为「reduce 扑克状态机不变,但跨房成员判定与全局 users 写模型需配套改造」。** low。 |

**低危档(3 批,单核验):**

- **规则边角**:**N-r4 PARTIAL**(奇数零头:`(seat-button)%size` 让庄家本人并列赢家时 key=0 **首**拿零头,标准规则应庄家**最后**拿;heads-up 平分池零头恒归 button;真实但边角、无测试钉)· **N-r5 REFUTED**(BB born-all-in 跳过 preflop 选择权——被推翻,不成立)· **N-r6 PARTIAL**(rules.md:268 表格「断线自动 fold」应为「按超时默认:能 check 则 check、否则 fold」,doc-stale)。
- **文档漂移**:**12 条全 CONFIRMED**(N-d8 presence 统一 API 未兑现 / N-d12 wire-guide dev 端点 ?nick= 任意昵称必败 / N-d14 auth.md REST 信封待办与决策矛盾 / N-d17 messaging 保留期口径 / N-d19 hands/leaderboard/config JWT 残留 / N-d20 log.md 毒丸 ERROR vs CRITICAL / N-d24 testing.md 漏 auth/rest/wire / N-d25 lifespan 行号引用漂移 / N-d27 BACKEND_GUIDE 大厅重连仍收 DM / N-d28 房列表标「落库滞后」实为内存实时 / N-d29 dev 端点「上线移除」无环境开关 / N-d33 core/records.py 头注引不存在的 messages.py)。
- **设计边角**:**11 CONFIRMED + 1 PARTIAL**——**N-e32 CONFIRMED 升 medium**(Broadcast 收件人取 commit 后成员表,fold-to-one 终手时离场者收不到自己参与底池的结算)· N-e9/10/11/16/26/34/35/36/38/40 CONFIRMED(low)· **N-e21 PARTIAL**(Err.detail 中文进英文日志=真,违 log.md:97;但「直接下发玩家」不准——detail 按 wire 契约是调试上下文、前端按 code 渲染)。

### 审计终结(五次工作流,累计 100+ agent)

覆盖矩阵已无悬空格(review.md 由 critic 通读、干净)。8 条原发现 + 36 条新发现均经对抗验证,结论收敛。**新增实质缺陷(medium,登进 TODO)**:N1(积分回退)· N2(幽灵连接)· N3(GameLoop 崩溃传播)· N5(无会话吊销)· N9(快照漏投票)· N-e32(离场者漏结算)。R1/R2 仍是需用户定案修复方向的两个架构级缺陷(R2 修复须含 N4 的房间身份约束)。余为文档 truth-up 大批(D 系列 + N-d* + N-r6 + N7 + N-dev22 + N-d33)与若干 low 设计边角。
