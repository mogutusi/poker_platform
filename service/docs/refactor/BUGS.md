# 未修缺陷登记册(bug registry)

> **这是所有「已确认为真、但还没修」的缺陷的唯一集中清单。** 建于 0076。
>
> 在此之前,缺陷散在 [TODO.md](TODO.md) 的各轮小节里,和「还没做的功能」混排,容易漏掉。本篇只收**缺陷**(已存在的代码会错),不收待办功能。功能仍在 TODO.md。
>
> 规则:
>
> - 每条给出**症状 → 机理 → 修法**,以及台账出处。修完在这里划掉并注明改动号,不要直接删行——删了就看不出它曾经存在。
> - 新发现的缺陷,如果当场修掉,只进 changes/,不必来这里;**只有「确认为真但本次不修」的才登记到这里**。
> - 每条都经过对抗验证(默认先试图反驳,驳不倒才算数),不是猜测。

## 怎么读严重度

- **high**:会造成数据/资金损失、或让进程无法正常工作。优先修。
- **medium**:特定交错或边界下才触发,后果明确但范围有限。
- **low**:边角、体验或整洁性问题,择机修。

---

## high

### ~~BUG-1 · 顶替链 A←B←C 复活已离线用户,座位筹码永久泄漏~~ —— **0083 已修**

- **来源**:[0074·E](changes/0074-code-defect-hunt.md) · **修于 [0083](changes/0083-shell-lifecycle-hardening.md)**(`_displace` 后复查 `is_current`,不是当前连接就地退出;A←B←C 交错回归测钉住「不复活 + 清理照常触发 + 筹码退回」)
- **症状**:座位被永久占住、里面的筹码再也回不到全局积分。
- **机理**:B 在 `_displace(A)` 的 await 窗口内被 C 顶掉;B 恢复执行后不知道自己已经不是当前连接,仍然去 `cancel_cleanup` + 投 `Connect`,于是把已经 `OFFLINE` 的用户复活成在线,同时抹掉了占座清理表里的定时项。清理再也不会触发。
- **修法**:`_displace` 之后复查 `is_current`,不是当前连接就直接返回,不做后续动作。
- **要补的测试**:构造 A←B←C 三连顶替的交错,断言 B 恢复后不复活用户、不抹清理表。

### BUG-2 · 手牌记录跨房间世代撞键,新记录被静默丢弃

- **来源**:[0072·R1](changes/0072-architecture-audit.md) · **状态:用户定案暂缓(2026-07-28「先不修」)**
- **症状**:同名房间销毁重建、或进程重启之后,新手牌的记录写不进 DB,且不报错。
- **机理**:`dedupe_key = "room:seq"`,`seq` 随房重建而归零,于是与旧世代的键相撞;幂等 INSERT 见到重复键就跳过,新记录被静默丢弃。
- **修法**:两案留档在 0072,尚未选定。修的时候必须带「销房重建」和「进程重启」两条路径的回归测试。
- **注意**:这条是**已确认的缺陷、用户主动决定暂不修**,不是「被判定为不用修」。

---

## medium

### ~~BUG-3 · Timeout 的 staleness 校验跨手失效~~ —— **0090 已修**

- **来源**:[0072·R2](changes/0072-architecture-audit.md) · **修于 [0090](changes/0090-timeout-identity.md)**(`Timeout` 改带三元身份 `(room, hand_seq, epoch)`,三项全等才算新鲜;并入 0072·N4 的跨房那半——补 `seq` 只堵跨手,`seq` 在房内单调,两个房的第 1 手同为 1)
- **症状**:上一手的超时命令可能被当成本手的有效超时执行,导致本手玩家被误判超时。
- **机理**:`epoch` 每手归零,所以「上一手的 epoch」和「本手的 epoch」会重号,单靠 `epoch` 区分不出跨手的陈旧命令。
- **修法**:`Timeout` 补带 `hand.seq`,与 `epoch` 双键校验。可与 BUG-2 同批(都动手牌标识)。
- **登记时的修法不完整,0090 已更正**:双键**不够**——`seq` 只在房内单调,而 `Timeout` 的目标房是按「他现在在哪」解析的,人换房之后两个房的第 1 手都是 `seq=1`,照样撞。实际落地的是**三**键。BUG-2(手牌记录撞键)与本条同源但**未一并修**:那条是 dedupe_key 跨房间世代撞,用户已定案暂缓。
- **要补的测试**:构造跨手交错,让上一手的 `Timeout` 在新手开始后才到达。

### ~~BUG-4 · 改昵称窗内发生 ws 顶替,活连接永久挂在旧 nick 键上~~ —— **0083 已修**

- **来源**:[0074·F](changes/0074-code-defect-hunt.md) · **修于 [0083](changes/0083-shell-lifecycle-hardening.md)**(连接改为全部 await 之后**当场**按 old_nick 查 + 归属校验;两个方向各一条回归测:窗内顶替要重挂到活连接、窗内他人占键不许误挂)
- **症状**:用户明明在线,却收不到任何消息。
- **机理**:改昵称流程里捕获的 `live_conn` 在 DB await 窗口内已被顶替;`rekey` 因此走 `else` 分支,只改了那个**已死对象**的 `.nick`,真正的活连接还挂在 `old_nick` 键下。
- **修法**:`rekey` 前后按当前连接对象重新解析,而不是用窗口前捕获的引用。
- **注意**:0074·C 的窗后复查**不覆盖**这条路径——那修的是「窗内进房」,这条是「窗内顶替」。

### ~~BUG-5 · 优雅关闭可能整体跳过,未落库积分全丢~~ —— **0083 已修**

- **来源**:[0074·I / 0074·J](changes/0074-code-defect-hunt.md)(两条同源,合并登记)· **修于 [0083](changes/0083-shell-lifecycle-hardening.md)**(`yield` 包 `try/finally`;`_cancel_and_await` 按 `current_task().cancelling()` + `t.cancelled()` 区分两种取消)
- **症状**:进程关闭时,还在写缓冲里没落库的积分变更全部丢失,DB 连接与协程泄漏。
- **机理**:两处:
  - `_cancel_and_await` 会吞掉 `stop()` **自身**收到的取消,导致关闭超时和强制中止失效;
  - lifespan 的 `yield` 没有 `try/finally`,关闭路径上一旦抛异常或被取消,`shell.stop()` 被整体跳过,drain 根本不执行。
- **修法**:`yield` 包 `try/finally` 保证 `stop()` 必被调用;`_cancel_and_await` 区分「被取消的是子任务」还是「是我自己」,后者要向上传播。

### ~~BUG-6 · 慢客户端被丢弃时只 unregister,不关 ws、不取消协程~~ —— **0083 已修**

- **来源**:[0072·N2](changes/0072-architecture-audit.md) · **修于 [0083](changes/0083-shell-lifecycle-hardening.md)**(`Connection.receiver_task` + drop 时 cancel Sender 与 Receiver;**修法与本条原「修法」不同**:只关 ws 堵不住「读慢写健」的客户端,关闭帧和数据一样发不出去)
- **症状**:出现幽灵命令源;同一个 nick 同时挂着两个 Receiver。
- **机理**:`dispatch._drop_connection` 只把连接从表里摘掉,既不关 ws 也不 cancel Sender/Receiver 协程。那条连接还能继续往 `inbox` 投命令。需要非对称慢客户端才触发(读慢、写健康)。
- **修法**:对齐 `receiver.py` 的退出清理路径——drop 时一并关 ws + cancel 协程。

### ~~BUG-7 · GameLoop 的异常兜底范围太窄,唯一状态写者可能被杀~~ —— **0083 已修(并降级为「潜在缺口」)**

- **来源**:[0072·N3](changes/0072-architecture-audit.md) · **修于 [0083](changes/0083-shell-lifecycle-hardening.md)**(兜底提到罩住 checkout/commit/派发 + 三条常驻协程挂 watchdog)
- **登记时的定性偏重,更正如下**:0083 的对抗核实把 `commit`/`_audit_applied`/`dispatch`/`checkout` 逐条走了一遍,**当前代码里没有可达的抛出路径**(commit 是一次属性赋值加一次 dict 操作;dispatch 的两处 `QueueFull` 早已各自兜住;checkout 深拷的全是普通 dataclass)。所以它是**潜在缺口**而非活的崩溃路径——0072 当初也只给了结构性论证、没给出触发者。修它的理由是防御性的:兜底缺口 + 无告警的组合,会让**日后新增的任何一行**(例如 0083 自己给 `Connection` 加字段)把一个 AttributeError 变成永久哑掉的服务器。
- **症状**:某条命令处理中途抛异常,整个 GameLoop 协程退出,服务器不再处理任何命令,且无人察觉。
- **机理**:`handle` 的 `except Exception` 只裹住 `reduce()` 一行;`commit` / `_audit_applied` / `dispatch` 抛出的异常会冒出去杀掉唯一的状态写者协程,而且没有 watchdog。这与 [architecture.md](../architecture.md)「接住 → 继续处理下一条」的承诺不符。
- **修法**:把 `except` 提到裹住 commit/dispatch;另外给 run task 加 done-callback 做重启或告警。

### ~~BUG-8 · 会话无法吊销~~ —— **0097 已修(部分:进程内那半;跨进程那半改为如实记档)**

- **来源**:[0072·N5](changes/0072-architecture-audit.md) · **修于 [0097](changes/0097-revocation-that-actually-bites.md)**
- **症状**:`K_user` 泄露后即使用 `issue --reset` 换了钥,已经建立的会话仍然有效,直到 `SESSION_TTL` 自然到期。
- **机理**:`SessionStore.revoke` 全仓零调用者——既没有登出端点,也没有管理员吊销通道。
- **登记时漏了两件事,0097 查出来并一并修了**:
  1. **`revoke` 就算有人调也不生效**。它只 `pop` 表项,而活 ws 连接持有的是 `Session` 对象与从它派生的 `SecureChannel`,收发两侧都只比对 `conn.session.expires_at`、从不回头查表——已经连着的人照样收发。修法是 `revoke` 就地把对象判死(`expires_at=0`),复用 0070 那条既有强制路径,下一帧即 4401 关连接。
  2. **前端「退出」是假的**:只清本地,一个字都没告诉服务器。现已真的调 `POST /user/logout`。
- **登记给的修法有一处不成立**:「建 `name→sessions` 索引」不需要——同类的 `rename_nickname` 一直是线性扫 `_by_id`,在线 ≤20 的规模下索引只是第二份要维护的事实源。
- **有一半在架构上做不到,已改为如实记档**:`issue --reset` 走的是 `kuser_admin.py`,**独立进程**,伸不进服务器内存里的会话表。`K_user` 泄露场景下要立刻掐断,唯一手段是重启服务器;这条已写进 [auth.md](../auth.md) §吊销,不再是隐性缺口。

### ~~BUG-9 · 重连/顶替后免盲投票面板消失~~ —— **0088 已修**

- **来源**:[0072·N9](changes/0072-architecture-audit.md) · **修于 [0088](changes/0088-betting-floor-and-vote-on-the-wire.md)**(`StateSnapshot` 加 `free_entry_vote` 投影;**并补上登记时没看到的另一半**:投票人集合变了要补发 `FreeEntryVoteUpdated`,否则「重连回来的人再点 Ready」这件事没有任何事件承载,他的面板一直显示「你不是本次的投票人」,票照样卡死)
- **症状**:重连或顶替后,进行中的免盲投票在客户端消失;重连回来的必需投票人根本不知道有一张票在等他。投票因此卡住。
- **机理**:`StateSnapshot` 不投影 `room.entry_vote`。
- **修法**:给 `StateSnapshot` 加投票公开态的投影;或在 reduce 的重连臂补发一条 `FreeEntryVoteUpdated`。

### ~~BUG-10 · 离场者收不到自己参与的那手的结算事件~~ —— **0091 已修**

- **来源**:[0072·N-e32](changes/0072-architecture-audit.md) · **修于 [0091](changes/0091-settlement-reaches-the-leaver.md)**(对「本手参与者 ∩ 本手末尾被驱逐者」各补一份 `Personal(HandShowDown/HandEnded)`)
- **症状**:玩家 `LeaveRoom` 触发了「只剩一人」的终手结算,但他本人收不到 `PlayerActed`/`HandShowDown`/`HandEnded`——看不到自己投入的底池是怎么结算的。
- **机理**:`Broadcast` 的收件人取的是 commit **之后**的成员表,而离场者此时已被移出。
- **修法**:结算事件对离场者改用 `Personal` 补发;~~或调整「驱逐」与「结算广播」的先后顺序~~。
- **登记的第二条备选走不通,0091 已更正**:dispatch 对**整批**事件用的是同一份 commit 后的成员表,而 commit 是原子的——在 reduce 里把 `_evict` 挪到广播之后,派发时看到的成员表一模一样,毫无作用。别再照它试一遍。

---

### ~~BUG-19 · 前端自己编了一个 min-raise 下限,别人大额加注之后就发不出合法的加注~~ —— **0088 已修**

- **来源**:[0085](changes/0085-raise-and-sidepot-verification.md)(写加注冒烟时**实测**出来的,不是推断)· **修于 [0088](changes/0088-betting-floor-and-vote-on-the-wire.md)**(上 wire 的是**下限本身** `min_raise_to`,不是 `last_raise_size` —— 给原料等于请客户端重算规则;校验与投影共用 `betting.min_raise_target` 一份公式)
- **症状**:有人大额加注之后,点「Raise」而不手动改金额,发出去的注会被服务器以 `ILLEGAL_ACTION` 拒掉;金额输入框上的 `min` 也是个假下限,照它填一样被拒。
- **机理**:[rules.md](../rules.md) ② 的合法下限是 `last_bet + max(last_raise_size, BB)`,而 **`last_raise_size` 只在 `core/domain.py` 的 `Hand` 里,从来没上过 wire**。前端够不着它,于是自己编了两个式子:输入框 `min={callAmount * 2}`(规则里根本没有这个式子),留空时回退 `state.lastBet + state.bigBlind`(只在 `last_raise_size ≤ BB` 时才等于真下限)。
- **实测证据**(`npm run smoke:raise`):`last_bet=2` 时加注到 10 ⇒ `last_raise_size=8`;此后下限是 `10+8=18`,而前端那个式子给的是 `10+2=12` —— 12 被 `ILLEGAL_ACTION` 拒,18 被接受。
- **这是 [0084](changes/0084-new-here-channel.md) 那类病的第二例**:客户端需要的一个规则输入没有传达渠道,于是前端只好猜。修法同款——把下限(或 `last_raise_size`)放上 wire,让服务器说,前端只显示不推算。要碰的消息是 `last_bet` 会变的那几处:`HandStarted` / `PlayerActed` / `HandStatusChanged` / `StateSnapshot`。
- **为什么本批不修**:0085 是验证批,它的职责是把这条路走通并留下证据;改协议要动 4 条消息 + codegen + 前端,值得单独一篇变更记录。**已有回归护栏**:`smoke:raise` 会一直钉住服务端这一侧的下限语义。
- **0087 先补了 `last_bet` 那一半**:同一批消息里——`HandStatusChanged` 现在带 `last_bet` + 本街 `players[]`,`HandStarted` 带 `pot`(修的是另一个缺陷:前端自己推「换街即清零」,把开局盲注也清了,整轮 preflop 的跟注因此发成 `bet(0)` 被拒)。**本条剩下的是 `last_raise_size`(加注下限)那一半**:`HandStarted` / `PlayerActed` / `StateSnapshot` 仍不带它,前端那两个自编式子(`min={callAmount*2}`、回退 `lastBet+bigBlind`)也还在。

---

## low(择机,可并入任意批次)

来源均为 [0072](changes/0072-architecture-audit.md) 的 N 系列低危项。

| ID | 缺陷 |
|---|---|
| ~~BUG-11(N-e9)~~ | ~~DM 读游标无单调防护,游标可能被旧值回拨~~ —— **0098 已修**(唯一写者处只前进不后退)。**登记只说了一半**:同一处缺口还有**指向未来**那一半,后果更硬(此后到达的私信永不补收,过保留期还会被 `cleanup_dms` 真删 = 数据丢失),已一并钳在路由层。顺带修了一个**修它才会踩到的坑**:游标列 `DateTime(timezone=True)` 在 sqlite 读回丢 tz,与 aware 值直接比会 `TypeError` 毒死整批状态写(见 [0098](changes/0098-read-cursors-only-move-forward.md))|
| ~~BUG-12(N-e10/N-e11)~~ | ~~db-migrations.md 的示例配置照抄会启动崩溃,且违反自家铁律~~ —— **0095 已修**(文档层)。**顺带更正登记的措辞**:0072 原文说的是违反自家 **create_all-vs-Alembic** 铁律,不是「配置铁律」——config.md / coding_principle.md 的配置铁律这篇一条都没违反。N-e10:sqlite 示例 URL 照抄进 `.env` 会崩(两个消费方方言形不同),现已写明并改掉示例;N-e11:那条「生产绝不靠 create_all」在代码里无路径可守(lifespan 无条件 `create_all`),现已把口径改实并给出正确顺序 |
| ~~BUG-13(N-e16)~~ | ~~`_evict` 不清 `waive_entry_for` → 离房再进仍享免盲~~ —— **0096 已修**(`_evict` **只**剔除该 nick,离房各路一并覆盖;回归测钉到筹码面:离房重进要真付入局 BB,同批被免的其他人不受牵连。在座者掉线/起身不弃权,观战者掉线即离场故随离房弃权,论证见 [0096](changes/0096-leaving-forfeits-free-entry.md))|
| ~~BUG-14(N-e26)~~ | ~~`scripts/scripts.py` 孤儿脚本~~ —— **0092 已删** |
| BUG-15(N-e34) | `NullPersister` 无生产消费者 —— **0100 核实后建议改判**:它有两个**测试**消费者(`test_persist_writer.py`),是 `Persister` 协议的空实现/测试替身,「只被测试用」不等于死代码。真正错的是文档:`db.md` 说「dev 用 `NullPersister` 直接丢弃」,而 0029 起 `DevShell` 无条件用 `OrmPersister`(该注释 0100 已改实)。**是删类还是改判成「文档说假话」,留待决定** |
| BUG-16(N-e35) | `Presence` 的三个方法零消费者 —— **0100 核实为真**:`current_room` 有生产调用(改昵称流程 ×2),`is_online`/`room_headcount`/`online_nicks` 只有测试调用。删之前要先答「有没有谁本该用它」(例如 `rest/lobby.py` 是否在手算 headcount),那是设计判断、不是清理,故未并入 0100 |
| ~~BUG-17(N-e36)~~ | ~~`profile.py` 手抄 `_NICKNAME_MAX_LEN`~~ —— **0092 已修**(改为从 `db/models.py` 的 schema 取,带「跟随 schema」的回归测)|
| ~~BUG-18(C3)~~ | ~~`rest/lobby.py` 手抄 `big_blind=2*`~~ —— **0092 已修**(改引 `blinds.BIG_BLIND_MULTIPLE`,断言改成派生关系)|

---

## 契约债 / 文档债(不是代码会错,但会误导人)

| ID | 事项 | 出处 |
|---|---|---|
| ~~DEBT-1(C2)~~ | ~~architecture.md/wire.md 声称 codegen「进 CI/pre-commit」,实际只有 pytest 守门~~ —— **0086 已把口径改实**(两处都改成「pytest 守门,仓库没有 CI 也没装 pre-commit,提交规约见 dev.md」)。**要不要真搭 CI 是独立决策,未做**:仓库的提交流程本来就写在 [dev.md](../dev.md)「提交」,缺的是自动化而不是约定 | 0072·C2 |
| ~~DEBT-2(D2)~~ | ~~不变量 2 没描述只读豁免家族~~ —— **0092 已修**:不变量 2 改写成「本体是唯一写者」,补上三条判据(只读 / 读已 commit / 全程同步)+ 现存三处名单 + 「新增豁免要按判据论证并补进名单」 | 0072·D2 |
| ~~DEBT-3(D3)~~ | ~~connection.md / lobby.md 的「待定」段陈旧~~ —— **0100 已修**:5 处改实(最离谱的一处是**反的**——connection.md 说「动态建房仍待定」,而 0049 早已落地,反倒是它列为「已设计」的静态预置房被删了)。**真正还开着的待定原样留下**:`LobbyBroadcast` 推送、「首帧验证前不登记」硬化、建房自定参 | 0072·D3 |
| ~~DEBT-4(D4)~~ | ~~四处陈旧注释,含两处 JWT 反事实~~ —— **0100 已修**:JWT 反事实实际有 **6 处**(`app/config.py` ×2、`gameconfig.py`、`poker.env.example`、`docs/config.md` ×2、`docs/dev.md` ×2),另修 `persist.py`「留 P4 三」、`rest/lobby.py` 头注仍写 `GET`、`wire/client.py` 的过时理由。注:`auth.md` 末尾「日后上 wss 可用标准 JWT」是假设语气的终局设想,**有意保留** | 0072·D4 |
| ~~DEBT-5(D5)~~ | ~~其余文档小项~~ —— **0100 已修**:`error.md` 示意块里那个**根本不存在**的 `ErrorCode.CANT_CHANGE_NICK_IN_ROOM`(lobby.md / presence.md 还各有一句说它「保留着」,读起来像已有此成员)、`timer.md` 伪码用 `cmd.nickname` 而字段叫 `nick`(照抄必 `AttributeError`,11 处) | 0072·D5 |

已解决:

- ~~**D1** [messaging.md](../messaging.md) §房聊历史留有 0071 之前的整段旧文(四处反事实,内部自相矛盾)~~ —— **0075 的文档重写已连带解决**,四处反事实全部消失,现文与 `reduce._room_chat` / `receiver.py` 只读豁免 / 动态房模型一致。

---

## 已修复(留档,别再重复发现)

| ID | 缺陷 | 修于 |
|---|---|---|
| 0074·A | `authenticate` 巨整数 ts 触发 OverflowError 逃逸 → 500 破 fail-closed + 成 K_user 猜测预言机 | 0074 |
| 0074·C | 改昵称「仅大厅可改」检查与内存联动之间隔两次 DB await → 四处永久发散 | 0074 |
| 0074·D | `PersistWriter.drain()` 的 deadline 罩不住 flush 本身 → 进程无法优雅退出 | 0074 |
| 0074·G | 改昵称落在 DM 路由的 DB await 窗内 → 私信静默不落库 | 0074 |
| 0074·H | `_buy_in` 的「局中」判据看状态而非「是否本手 Player」 → 手牌记录凭空多筹码 | 0074 |
| 0074·E(BUG-1)| 顶替链 A←B←C 复活已 OFFLINE 用户 + 抹清理表 → 座位筹码永久泄漏 | 0083 |
| 0072·N2(BUG-6)| 慢客户端被丢弃只摘键,ws 与 Receiver 都还活着 → 幽灵命令源 + 同 nick 双 Receiver | 0083 |
| 0072·N3(BUG-7)| GameLoop 兜底只罩 `reduce()`,且常驻协程死了无人告警(潜在缺口,非活路径)| 0083 |
| 0074·F(BUG-4)| 改昵称窗内 ws 顶替 → `rekey` 只改死对象,活连接永久挂旧键 | 0083 |
| 0074·I/J(BUG-5)| `_cancel_and_await` 吞自己的取消;lifespan `yield` 无 `try/finally` → drain 整体跳过 | 0083 |
| 0072·N9(BUG-9)| `StateSnapshot` 不投影 `entry_vote` → 重连/顶替后投票面板消失、全票制下卡死 | 0088 |
| 0085(BUG-19)| 前端自编 min-raise 下限 → 别人大额加注之后发不出合法加注 | 0088 |
| 0072·R2(BUG-3)+ 0072·N4 | `Timeout` 身份不足(只带 epoch)→ 跨手/跨房撞号,误弃不该弃的人 | 0090 |
| 0072·N-e32(BUG-10)| 离场者收不到自己那手的结算(广播按 commit 后的成员表解析)| 0091 |
| 0072·N-e10/N-e11(BUG-12)| db-migrations.md 示例照抄会崩 + create_all/Alembic 铁律无路径可守(文档层)| 0095 |
| 0072·N-e16(BUG-13)| `_evict` 不清 `waive_entry_for` → 离房再进(或改名接盘旧 nick)凭残留快照免入局 BB | 0096 |
| 0072·N5(BUG-8)| 会话无法吊销:`revoke` 零调用者,且只 pop 表项挡不住已连着的人;前端「退出」只清本地 | 0097 |
| 0072·N-e9(BUG-11)| DM 读游标可被回拨;连带查出「可指向未来」致私信永不补收 + 被保留清理真删 | 0098 |

## 误报留档(别再「发现」一次)

- **0074·B** `_disconnect` 不重算免盲投票 —— **不是缺陷**。[rules.md](../rules.md) ① 与实现同批(0020)明写「`voters` 每次实时重算,断线者在下一结算点自然不计,**不为断线单独触发通过**」,是有意设计:断线可逆、占座窗口内可重连,全票制下按减员结算等于剥夺其否决权;离场/坐出才不可逆。已补反向钉 `test_voter_disconnect_does_not_trigger_vote`。
- **`ttxsgm` 裸库脆弱面**:SM4 去填充无校验、非对齐密文抛异常(实跑复现),但当前所有入口都有守卫挡住(MAC 先行 / 长度预校验),故非缺陷。**日后新增任何直喂 `sm4_cbc_*` 的入口,必须自带同款长度守卫。**
