# 0102 · 把 BUG-15/16 那个设计问题答完(Presence 删三留一,NullPersister 改判结案)

日期:2026-08-28 · 性质:**死代码清理 + 缺陷改判**· 触发:[BUGS.md](../BUGS.md) 的 BUG-15 / BUG-16 —— low 组里最后两条(除用户暂缓的 BUG-2 外,BUGS.md 至此清空)。

0100 核实了事实但**没有拍板**,因为卡在一个设计问题上:「**有没有谁本该用它?**」把那个问题答完,是本批的全部内容。

## 先答那个问题:没有

| 方法 | 有没有人本该用它 | 依据 |
|---|---|---|
| `room_headcount(room)` | **没有** | 唯一像消费者的 `rest/lobby.py` 算的是 `seated`(占座数)与 `watching`(观战数)**两个更细的量**;`room_headcount` 是 `len(users_in_room)`(含 OFFLINE 保座的总成员数)。它要的不是这个数,不是在手算同一个东西 |
| `online_nicks()` | **没有** | 它是**纯转发** `conns.online_nicks()`,一行,不加任何投影。`lifespan.py` 关连接时直接调 `ConnectionManager` 是**对的**——那是连接生命周期,不是 presence 投影 |
| `is_online(nick)` | **没有** | `= conns.get(nick) is not None`。`messaging.py` 判在线用 `conns.get(...)`,但它要的是**连接对象本身**(拿去投递),不是布尔;换成 `is_online` 反而要查两次 |
| `current_room(nick)` | **有,且不可替代** | `rest/profile.py` 改昵称流程 ×2。它是 REST 层**唯一被批准**的 world 只读窗口(architecture.md 不变量 2 的三处豁免之一) |

所以三个是投机性通用化(speculative generality),不是「还没接上的功能」。

## 决定

**Presence 删三留一。** 保留 `current_room` 与类本身——类不是多余的:它是 REST 够到 `world` 的那扇合规窗口,architecture.md 不变量 2 点名的三处豁免之一。删到只剩一个方法之后它**仍然成立**,只是名副其实了。

**BUG-15(`NullPersister`)改判结案,不删。** 它有两个测试消费者,是 `Persister` 协议的空实现——「只被测试用」不是死代码,是测试替身。**真正的缺陷是文档说假话**:`db.md` 与 `persist.py` 头注都写「dev 用 `NullPersister` 直接丢弃」,而 0029 起 `DevShell` 无条件用 `OrmPersister`。那两处 [0100](0100-sweep-the-tails-of-0086-0094-and-no-jwt.md) 已经改实,所以这条**其实已经修完了**,只差在册子上结掉并写清为什么不是「删死代码」。

## 打算怎么改

1. `app/shell/presence.py`:删 `is_online` / `room_headcount` / `online_nicks`,留 `current_room`;类注释改成名副其实的说法。
2. `tests/shell/test_presence.py`:删掉那三个方法的用例;`current_room` 的用例保留(含「在线 ⊥ 在房」那条要改写——它同时断言两者)。
3. [presence.md](../../presence.md):重写成「它现在是什么」,并写明**更宽的投影是投机设计、已删、要用时从 git history 取回**——把设想留在文档里、把代码删掉,比反过来好。
4. [BUGS.md](../BUGS.md) 划掉 BUG-15(改判)与 BUG-16(已删);[TODO.md](../TODO.md) 的 N-低危那条同步收尾(**两处登记都要清**)。

不动:`Presence` 类、`current_room`、`lifespan` 的接线、`architecture.md` 不变量 2 的豁免名单(仍是三处)。

## 实际改了什么

按计划落地,**外加一处计划里没有、但删完必然要跟上的**。

- **`app/shell/presence.py`**:删 `is_online` / `room_headcount` / `online_nicks`,留 `current_room`。
- **计划外:构造签名收成 `Presence(world)`**。三个方法一删,`self._conns` 就没人用了——那是新的死状态,不清就等于用一次清理换来另一处。连带改 `lifespan.py` 的构造点与测试夹具。
- **`tests/shell/test_presence.py`**:删掉三个已删方法的用例(含那条同时断言两者的「在线 ⊥ 在房」),`current_room` 的用例与「只读不改 world」「见提交后变化」两条契约测保留。
- **`tests/rest/test_change_nickname.py`**:夹具跟新签名。
- **[presence.md](../../presence.md)**:重写成「它现在是什么」,把三个方法**为什么不该有人用**逐条写进去(而不是只说「删了」),并明说要用时从 git history 取回。
- **两处台账**:[BUGS.md](../BUGS.md) 划掉 BUG-15(改判)与 BUG-16(已删);[TODO.md](../TODO.md) 的 N-e34/N-e35 同步清掉,那条 low 组从 `[~]` 转 `[x]`(只余两条纯记档项)。

### 名字比行为宽,没改

删完之后 `Presence` 只答「在哪个房」,不答「在不在线」——**名字宽于行为**。没改名:[architecture.md](../../architecture.md) 不变量 2 的豁免名单、presence.md、多处交叉链接都点名 presence,改名要一起动而收益纯文字。已在代码注释与 presence.md 各写一句挑明,免得下一个人以为它还能答在线。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **775 passed**(779 → 775,恰好少掉被删方法的 4 条用例) |
| 前端 vitest | 93 passed |
| 浏览器 `npm run test:e2e` | 16 passed |
| 冒烟(`smoke` + `smoke:raise`) | 通过 |
| 改完杀 uvicorn、确认端口释放、重启并 grep 日志 | 是 —— **第一次重启就撞上了房规里那个坑**:旧进程没退干净,新进程报 `address already in use` 后自己退了(日志里 1 条),按 pid 清干净重来才起成功。要不是 grep 日志,后面所有前端层测的都是旧代码 |

**删除类改动没有反向变异可做**(没有新行为可被改坏),判据换成穷举核实:全仓 grep 三个方法名(含测试与脚本)**零残留**——只剩 `ConnectionManager.online_nicks` 那个同名但不同类的真消费者;`lifespan` 的构造点改完由端到端(冒烟 + 16 浏览器用例真跑改昵称之外的全流程)兜住。

## 自 review

按 [review.md](../../review.md) 七维。本批是**删代码 + 改判一条在册缺陷**,最高风险面是「删错了(其实有人该用)」与「改判是不是在给自己找台阶」。

- **① 分层 / 不变量**:`Presence` 仍是 [architecture.md](../../architecture.md) 不变量 2 点名的三处只读豁免之一,**豁免名单不变**(仍三处)。删完它连 `ConnectionManager` 都不碰了,只读 `world`,反而更贴「只读投影」的定义。core 零改动。
- **② 代码↔文档同步**:presence.md 与代码同批改;代码注释写清「为什么这三个不该有人用」,而不只是「删了」——下一个人最可能干的事就是把它们再造一遍。
- **③ 文档↔文档一致**:BUGS.md 与 TODO.md **两处**同批清(0093 教训)。**BUGS.md 至此只剩 BUG-2**(用户明确暂缓)。
- **④ 数据模型正确性**:`Presence(world)` 去掉了一个不再有意义的依赖,构造期就不可能再传进一个没人读的 `conns`。
- **⑤ 规范合规**:兑现「不留死代码」,且**连带清掉删除产生的次生死代码**(`_conns`)——只删方法不删依赖是半途而废。
- **⑥ 测试充分**:删掉的 4 条用例是**已删行为的测试**,不是覆盖损失。保留的三条恰好是真正的契约:「在房 vs 大厅」「见提交后变化(不持快照)」「只读不改 world」。**缺口如实记**:`current_room` 的两个生产调用都在改昵称流程里,而那条流程的测试用的是自建夹具;没有一条端到端用例真的在浏览器里跑「在房时改昵称被拒」——`e2e` 覆盖的是别的路径。
- **⑦ 流程账本**:变更记录先行。**这一批的正题就是把 0100 留下的问号答完**:0100 核实了事实但拒绝拍板,理由是「混进 truth-up 不合适」;本批单独开一篇把设计判断做完,并逐条写出判据。

### 关于「改判」是不是在找台阶

BUG-15 我判「不删」,得说清这不是回避:**它的登记事实(无生产消费者)完全成立**,我只是不同意由此得出「删」。判据是——`NullPersister` 有测试消费者,而「只被测试用的协议空实现」是测试替身,不是死代码;真正说假话的是文档,而那两处 0100 已经改实了。**如果哪天有人认为测试替身不该住在生产模块里,那是另一条独立的意见**(可以把它挪进 `tests/`),但那与「零消费者」这条登记无关。

### 有意不做

- **不给 `Presence` 改名**:见上。
- **不把 `NullPersister` 挪进 `tests/`**:见上,那是另一个独立判断,不该借这批顺手做掉。
