# 0092 · 单一事实源清理:一处不实的不变量 + 三条一行缺陷(DEBT-2 + BUG-14/17/18)

日期:2026-08-25 · 性质:**文档改实 + 低危清理**· 触发:[BUGS.md](../BUGS.md) 的契约债 DEBT-2 与 low 组里三条一行就能修的。

## 为什么合成一批

四条都是**同一件事的不同表现:某个事实在仓库里存了两份,或者文档说的和代码做的不是一回事。**

| 条目 | 问题 |
|---|---|
| **DEBT-2** | [architecture.md](../../architecture.md) 不变量 2 写「读 DB、不读 `world`」,但已有**三处记档合规**的只读豁免(presence 0037、`GET /lobby/rooms` 0048、`FetchRoomChat` 0071)。按字面读,这三处合规代码会被后来者判成违规——**不变量本身没描述这个豁免家族**。 |
| **BUG-17**(N-e36)| `rest/profile.py` 手抄了一份 `_NICKNAME_MAX_LEN = 50`,而真正的约束在 `db/models.py` 的 `Field(max_length=50)`。改 schema 不改这里,就是「DB 收得下、接口先拒掉」或反过来。 |
| **BUG-18**(C3)| `rest/lobby.py` 写 `big_blind=2 * room.small_blind`,而 `blinds.BIG_BLIND_MULTIPLE` 就是为这件事存在的。 |
| **BUG-14**(N-e26)| `scripts/scripts.py` 是原型遗留的孤儿脚本(把全仓 `.py` 拼成一个 txt),零调用者。[coding_principle.md](../../coding_principle.md)「不留死代码」。 |

DEBT-3/4/5(陈旧「待定」段、四处陈旧注释、其余小项)**不在本批**:它们要逐处核对现状,值得单独一轮,不该和一行修混在一起草草了事。

## 先读设计文档(本仓纪律)

- [architecture.md](../../architecture.md) 不变量 2 的**意图**是「唯一写者」:除 GameLoop 经 commit 之外没人写 `world`。「不读 `world`」是当初为 Receiver 写的一句具体化,不是不变量的本体——三处豁免读的都是**已 commit 的状态**,不写、不据它做载入决策,所以并不破坏唯一写者。
- 0037 / 0048 / 0071 三篇变更记录里各自论证过自己那处豁免,但**顶层不变量没有收编这个家族**,这正是 DEBT-2 说的缺口。

## 打算怎么改

1. **不变量 2 补一条豁免判据**:说清「只读已 commit 的 `world` 且不据此做写决策」是允许的,并点名现存三处;同时保留原意——**写**永远只有 GameLoop 一条路。
2. `rest/profile.py` 改引 schema 的单一事实源,不再手抄字面量。
3. `rest/lobby.py` 改引 `blinds.BIG_BLIND_MULTIPLE`。
4. 删 `scripts/scripts.py`。

## 要动的文件(预期)

- `app/rest/profile.py`、`app/rest/lobby.py`、删 `scripts/scripts.py`
- 文档:[architecture.md](../../architecture.md)、[BUGS.md](../BUGS.md)、[TODO.md](../TODO.md)

协议面不变。

## 实际改了什么

四条都按计划落地。

### 1. 不变量 2 改写(DEBT-2)

原文只有一句「Receiver 读 DB、不读 `world`」,把一条**具体化**当成了不变量本体。改后分三层说清:

- **本体是「唯一写者」**:`reduce` 只改副本,写永远只有 GameLoop 一条路。
- **只读豁免是一族**,判据三条:①只读 ②读的是已 commit 的状态 ③**全程同步**(无 `await`,所以读到的一定是某条命令的完整结果,不会撞见半改)。现存三处点名列出并链到各自的变更记录(0037 / 0048 / 0071)。
- **Receiver 那条仍然成立而且更严**,并写清为什么更严:「要不要从 DB 载入积分」是个**写决策**,不是投影,所以决定权必须留在 reduce。
- 末尾加一句流程要求:新增豁免要按判据论证 + 回来补名单——DEBT-2 的成因正是「顶层没收编这个家族」,只补一次名单而不定规矩,下次还会长出没人知道的第四处。

### 2. `_NICKNAME_MAX_LEN` 改从 schema 取(BUG-17)

`rest/profile.py` 不再手抄 `50`,改成从 `db/models.py` 的 `User.nickname` 字段元数据里取 `max_length`。
配一条回归测 `test_nickname_max_len_follows_the_schema`——注意它断言的是**「跟随 schema」**而不是「等于 50」:写死 50 的话,schema 改了它会红,但红的是测试而不是缺陷,方向正好反了。

### 3. `big_blind` 改引规则常量(BUG-18)

`rest/lobby.py` 的 `2 * room.small_blind` → `blinds.BIG_BLIND_MULTIPLE * room.small_blind`。
既有的投影用例补一条**派生关系**断言(`meta.big_blind == BIG_BLIND_MULTIPLE * meta.small_blind`),而不是只对着字面量 10 断言——只断字面量的话,把倍数改成 3 依然绿。

### 4. 删 `scripts/scripts.py`(BUG-14)

原型遗留,把全仓 `.py` 拼进一个 txt,零调用者。删前全仓 grep 过引用:只有 BUGS/TODO 两处「应删除」的登记提到它。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **755 passed**(754 → 755) |
| 前端 vitest | 90 passed(未改前端) |
| 浏览器 `npm run test:e2e` | 16 passed |
| 冒烟 | 通过(守恒 1920 → 1920) |
| 后端改完重启 uvicorn 再跑前端各层 | 是 |

**反向变异验证 2 处**(两条一行修各一):

| 变异 | 变红的 |
|---|---|
| `_NICKNAME_MAX_LEN` 退回手抄字面量(写成 40,模拟 schema 漂移)| `test_nickname_max_len_follows_the_schema` |
| `big_blind` 倍数手抄成 3 | `test_list_rooms_projection_fields` |

删脚本与改文档没有可变异的行为面,如实记下:它们靠 grep 复核(零引用)与阅读复核。

## 自 review

按 [review.md](../../review.md) 七维。本批性质是**清理**,最高风险面是「改文档时把不变量改松了」——那比留着不实描述更糟。

- **① 分层 / 不变量**:**不变量 2 的强度没有被放松**,这一条专门核过:改后仍然是「写只有 GameLoop 一条路」,豁免只覆盖**只读投影**,而且加了「全程同步」这条此前没写、但三处现存实现都满足的判据。Receiver 那条更严的约束原样保留并解释了为什么更严。代码侧两处改动都是「同一个值换个来源」,行为不变。
- **② 代码↔文档同步**:本批的正题就是这个。[architecture.md](../../architecture.md) 不变量 2 重写;`profile.py`/`lobby.py` 两处注释写清「为什么不手抄」。
- **③ 文档↔文档一致**:[BUGS.md](../BUGS.md) 划掉 DEBT-2 与 BUG-14/17/18(按本篇规矩划掉不删行);[TODO.md](../TODO.md) 的 N-低危项从 `[ ]` 改 `[~]` 并列出余项。**链接实地核过**:0037 的文件名是 `0037-presence.md`(不是我一开始写的 `0037-presence-online-list.md`),已改正——写死链再多一条,就是下一轮 truth-up 要清的死链。
- **④ 数据模型正确性**:`_NICKNAME_MAX_LEN` 从 `int` 字面量变成从字段元数据取,类型仍是 `int`,取不到会在**导入期**就炸(`next()` 无默认值),不会静默退化成 `None` 或某个错值。
- **⑤ 规范合规**:无死代码(删掉了一处);无魔法数(减少了两处);注释讲「为什么」。
- **⑥ 测试充分**:两处变异确认。**两条断言的方向都特意选过**:昵称那条断言「跟随 schema」而非等于 50;大盲那条断言「派生关系」而非等于 10——两者都是「只断字面量就抓不到这个缺陷」的形状。**缺口如实记**:删脚本与文档改写没有自动化守门。
- **⑦ 流程账本**:本篇即账本;开工前写清了「DEBT-3/4/5 不在本批」及理由(要逐处核对现状,不该和一行修混着草草了事),收工无偏离。

### 未做,留档

- **DEBT-3/4/5**:陈旧「待定」段([connection.md](../../connection.md)/[lobby.md](../../lobby.md))、四处陈旧注释(含两处 JWT 反事实)、其余文档小项。它们要逐处对照现状核实,单独一轮。
- **low 组余下六条**:N-e9 / N-e10·N-e11 / N-e16 / N-e34 / N-e35 / N-e38·N-e40。其中 **N-e16(`_evict` 不清 `waive_entry_for`,离房重进仍享免盲)是唯一会真的影响筹码的**,修它要动 reduce 并配回归测,不属于「一行修」,没有塞进本批。

### 收工后补记(账本对齐)

本批修掉的项在 [BUGS.md](../BUGS.md) 当时就划掉了,但 [TODO.md](../TODO.md) 里 **0072 那一节的镜像条目**当时漏了——同一件事在两处登记,只清一处就是新的漂移。已于 0093 一并补齐(见 [changes/0093](0093-ledger-alignment.md))。
