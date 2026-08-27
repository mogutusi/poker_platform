# 0100 · 扫掉 0086 / 0094 / 去-JWT 三件事的尾巴(DEBT-3 + DEBT-4 + DEBT-5)

日期:2026-08-27 · 性质:**文档与注释改实(纯 truth-up)**· 触发:[BUGS.md](../BUGS.md) 的 DEBT-3(陈旧「待定」段)、DEBT-4(四处陈旧注释含两处 JWT 反事实)、DEBT-5(其余文档小项)。0092 当初把这三条推后,理由是「要逐处核对现状,不该和一行修混着草草了事」。

## 病根不是随机腐烂,是三次改动没扫尾

四路并行审计(两路查 DEBT-3/5、一路查代码注释、一路做全仓 truth 扫)之后,把确认项一摊开,**93% 归三个源头**:

| 源头 | 当时改了什么 | 留下的尾巴 |
|---|---|---|
| **[0086](0086-retire-plaintext-endpoint.md)** 退役明文 `/dev/ws?nick=` | 改了退役当事文档 | **8 处**仍断言「两个 ws 端点并存 / dev 走明文」——[auth.md](../../auth.md)、[connection.md](../../connection.md) ×3、[dev.md](../../dev.md) ×2、[frontend/docs/dev.md](../../../../frontend/docs/dev.md)、[wire-protocol-guide.md](../../wire-protocol-guide.md) ×2 |
| **[0094](0094-envelope-covers-everything.md)** 全部收进信封 | 改了 auth/rest/guide 的当事段 | [QUICKSTART.md](../../../QUICKSTART.md) 的验活 `curl` 现在一律 405、[lobby.md](../../lobby.md) 说它「明文无鉴权」、[architecture.md](../../architecture.md) 不变量 2 的名单里写着 `GET`、[rest.md](../../rest.md) 把「REST 加密」列在**待定**里 |
| **P5 定案不用 JWT**([0057](0057-p5-unified-encrypted-channel-design.md)) | auth.md 写清了 | [config.md](../../config.md)、[dev.md](../../dev.md)、`app/config.py` 仍拿 JWT 当「未来要加的东西」举例 |

**这三类的共同形状**:改动发生时,人只改了「讲这件事的那一篇」,而**「顺带提一句」的地方全留在原地**。更说明问题的是——8 处 0086 尾巴里有 4 处,同一个文件的**别处已经写对了**(connection.md 第 90 行说「已于 0086 退役」,第 153 行说「并存」)。所以这不是「不知道」,是「没回头扫」。

危险度最高的是 0086 那一类:它们告诉读者**存在一条无鉴权的明文 ws 后路**。`auth.md` 是安全设计文档,它第 258 行说明文端点并存,和它自己的威胁模型直接打架。

## 打算怎么改

按上表三类逐处改实,外加两组独立小项:

- **DEBT-3 真正的陈旧待定段**:`connection.md`「动态建房仍待定」(**反了**——0049 已落地,反倒是它说「已设计」的静态预置房被删了)、「房聊环形缓冲章节待补」(那个组件 0071 就删了)、背压上限「进 config.md」(早进了);`lobby.md` 的「离桌中途在局待定」(0014 已落地,同文件第 57 行自己讲完了)、「presence 只读视图待单列」(0037 已单列)。
  **仍然真开着的待定要原样留下**:`LobbyBroadcast` 实时推送、「首帧验证前不登记」硬化、建房自定参 —— 逐条验过确实没做,留着才对。
- **DEBT-5 两条硬伤**:[error.md](../../error.md) 的示意块里有个**根本不存在的** `ErrorCode` 成员 `CANT_CHANGE_NICK_IN_ROOM`(lobby.md / presence.md 还各有一句说它「保留着」,读起来像枚举里已有);[timer.md](../../timer.md) 的伪码用 `cmd.nickname`,而字段叫 `nick` —— 照抄必 `AttributeError`,同文件第 7 行自己写对了。
- **dev 账号名册**:三处文档说「6 个 dev 用户」,实际种 10 个。这条不只是算术错:`smoke1/2/3` 归冒烟、`gina` 归浏览器用例是**专用**的,信了「只有 6 个」的人会去复用,撞上 0089 已经付过学费的账号串扰。

## 有意不做,并更正审计的一个结论

**BUG-15 / BUG-16 不进本批。** 审计给的结论是「两个都删」,我核实后**不同意其中一个,另一个也需要一次判断**,而把判断混进 truth-up 正是 0092 警告过的:

- **BUG-15(`NullPersister` 无生产消费者)**:它有**两个测试消费者**(`test_persist_writer.py`)。「只被测试用」不等于死代码——它是 `Persister` 协议的空实现/测试替身。**真正错的是文档**:[db.md](../../db.md) 说「dev 用 `NullPersister` 直接丢弃」,而 0029 之后 `DevShell` 无条件用 `OrmPersister`。所以这条八成该改判成「文档说假话」而不是「删死代码」——但改判一条登记在册的缺陷,值得单独说清。
- **BUG-16(`Presence` 三方法零消费者)**:核实为真——`current_room` 有生产调用(改昵称流程 ×2),`is_online`/`room_headcount`/`online_nicks` 只有测试。但「删掉」之前得先回答「**有没有谁本该用它**」(例如 `rest/lobby.py` 是不是在手算 headcount),这是设计判断,不是清理。

两条都留在 BUGS.md,并把上面这些核实结论写进去,免得下一轮重新查一遍。

## 要动的文件(预期)

文档:`auth.md` `connection.md` `lobby.md` `rest.md` `architecture.md` `config.md` `dev.md` `error.md` `timer.md` `presence.md` `wire-protocol-guide.md` `db.md`、`QUICKSTART.md` `README.md`、`frontend/docs/dev.md` `frontend/BACKEND_GUIDE.md`。
代码注释:`app/config.py`(JWT 反事实)、`app/shell/persist.py`(「留 P4 三」早已落地)、`app/rest/lobby.py`(头注仍写 `GET`)、`app/wire/client.py`(`FetchRoomChat` 的理由已过时)。

**零行为改动**:不动任何逻辑、不动协议、不动测试断言。

## 实际改了什么

**24 个文件、零行为改动。** 按三类源头 + 两组小项落地,另有两处比预期多。

### 三类源头

- **0086 尾巴 8 处**:`auth.md`(安全文档里那句「dev 明文端点并存」)、`connection.md` ×3(第 153 行「并存」、第 264 行标题「明文脚手架」、第 266 行启动序「挂 `/dev/ws`」——挂的其实是 `/ws`)、`dev.md` ×2、`frontend/docs/dev.md`、`wire-protocol-guide.md` §9 的「一句话」。
- **0094 尾巴 5 处**:`QUICKSTART.md` 的验活 `curl`(现在一律 405,已改成「用 `smoke-client.mjs` 的 `restCall()`,或直接跑冒烟」)、`lobby.md`「明文无鉴权」、`architecture.md` 不变量 2 名单里的 `GET`、`rest.md` 把「REST 加密」列在**待定**、`rest.md`「REST DTO 经 OpenAPI 生成」(不但没生成,0094 之后**这条路等于关掉了**:OpenAPI 里只剩信封,DTO 在密文内层)。
- **JWT 反事实**:登记说「两处」,**实际 6 处** —— `app/config.py` ×2、`app/gameconfig.py`、`app/poker.env.example`、`docs/config.md` ×2、`docs/dev.md` ×2(含 `JWT_SECRET` 出现在「秘密不进 git」清单里,而那个变量根本不存在;换成真实存在的 `DEV_KUSER`/`k_cur`)。**有意保留一处**:`auth.md` 末尾「日后上 wss 可用标准 JWT」是假设语气的终局设想,不是现状断言。

### 两组小项

- **DEBT-3 陈旧待定段 5 处**。最离谱的一处是**反的**:`connection.md` 说「动态建房仍待定」,而 0049 早已落地;反倒是它并列为「已设计」的**静态预置房已被删除**。另外「房聊环形缓冲章节待补」待补的是一个 **0071 就删掉的组件**;「背压上限进 config.md」早就进了(真正还开着的只有「没做压测校准」)。**真开着的待定原样留下**:`LobbyBroadcast` 推送、「首帧验证前不登记」硬化、建房自定参——逐条验过确实没做。
- **DEBT-5 两条硬伤**:`error.md` 示意块里那个**根本不存在**的 `ErrorCode.CANT_CHANGE_NICK_IN_ROOM`(`lobby.md`/`presence.md` 还各有一句说它「保留着」,读起来像枚举里已有,前端会为一个永不触发的分支写文案);`timer.md` 伪码通篇 `cmd.nickname`,而字段叫 `nick`——**照抄必 `AttributeError`**,同文件第 7 行自己写对了,11 处一并对齐。

### 计划外补的两处

- **dev 账号名册**:三处文档说「6 个 dev 用户」,实际种 **10 个**。不只是算术错——`smoke1/2/3` 归冒烟、`gina` 归浏览器用例是**专用**的,信了「只有 6 个」的人会去复用,撞上 0089 已经付过学费的账号串扰。三处都改成 10 个并按用途分组、写明别混用。
- **`BACKEND_GUIDE` 的 `K_user` 自相矛盾**:它两处(§4.2 表格 + §常见坑)把「绝不存 localStorage」当契约,而前端**有意**把 `K_user` 存在 localStorage,`auth.md` 已于 0097 把这个分歧记档为**未定案**。前端的入口手册却把已上线的有意行为列成「坑」。改法是**与 auth.md 对齐到「未定案」**,不替用户选边。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | 779 passed(零行为改动,复跑确认) |
| 前端 `tsc --noEmit` / vitest | 通过 / 93 passed |
| 浏览器 `npm run test:e2e` | 16 passed |
| 冒烟 | 通过 |
| **全仓活文档死链扫描** | **0 条**(service/docs + frontend/docs + 四篇顶层指南) |
| 反事实复扫(`JWT` / 明文 ws 端点 / `并存`) | 只剩 auth.md 那句假设语气的终局设想(有意保留) |

**这一批没有可做的反向变异**——零行为改动,没有新行为可以被改坏。判据换成:每处改动都**先读代码定位真相**再改(例如 `timer.md` 的字段名是照着 `app/shell/timer.py` 的 `_ActionDeadline.nick` 与 `Timeout(nick=…)` 逐个核的,不是照我记忆),以及改完做一次全仓反事实复扫。

## 自 review

按 [review.md](../../review.md) 七维。本批是**纯 truth-up**,最高风险面只有两个:「有没有把还开着的待定误判成已完成」和「有没有改出新的假话」。

- **① 分层 / 不变量**:零代码逻辑改动,只动注释与文档。`architecture.md` 不变量 2 的只读豁免名单只改了标签(`GET`→`POST`),**豁免的三处一个没变**。
- **② 代码↔文档同步**:本批正题。改的方向全部是「文档追上代码」,没有反过来迁就文档。四处**代码注释**也一并改实(`config.py`/`gameconfig.py`/`persist.py`/`rest/lobby.py`/`wire/client.py`)——注释和文档一样会骗人,而且离读者更近。
- **③ 文档↔文档一致**:DEBT-3/4/5 在 **BUGS.md 与 TODO.md 两处**同批划掉(0093 教训)。死链 0 条。
- **④ 数据模型正确性**:不适用(无类型改动)。唯一沾边的是删掉 `error.md` 里不存在的枚举成员——那本身就是在消除一个「文档里可表达、代码里不存在」的假成员。
- **⑤ 规范合规**:注释仍讲「为什么」;删掉的是假话,不是解释。
- **⑥ 测试充分**:**文档没有自动化守门,如实记为缺口**——本批全靠人工核实 + 一次性脚本扫描。值得记的是:这批 24 处里,**8 处所在文件的别处已经写对了**(同一篇文档自相矛盾),说明「改的时候只改当事段」是个反复出现的失误模式,而**没有任何机制会发现它**。一个可行的守门是「文档里出现 `/dev/ws`、`GET /lobby/rooms`、`JWT` 等已退役符号即测试红」,但那需要先定一份退役符号表,值得单独议。
- **⑦ 流程账本**:变更记录先行;开工前先做了四路并行审计,**并在记录里更正了审计的一个结论**(见下)。

### 更正审计结论 + 有意不做

审计给 **BUG-15/16** 的判决是「两个都删」,我核实后**不同意其中一个**:

- **BUG-15(`NullPersister`)**:它有**两个测试消费者**。「只被测试用」不是死代码——它是 `Persister` 协议的空实现。真正错的是 `db.md` 说「dev 用 `NullPersister` 直接丢弃」(0029 起 `DevShell` 无条件用 `OrmPersister`),那句注释本批已改实。这条八成该**改判**成文档缺陷而非死代码,但改判一条在册缺陷值得单独说清。
- **BUG-16(`Presence` 三方法)**:核实为真(`current_room` 有生产调用,另三个只有测试)。但删之前得先答「**有没有谁本该用它**」——那是设计判断,混进 truth-up 正是 0092 警告过的做法。

两条都留在 BUGS.md,并把上面的核实结论写了进去,免得下一轮重新查一遍。

**另外记档、本批未动**:`changes/` 历史记录里有约 72 条死链,几乎全是同一个机械错误(`../` 少写一级)。它是**追加型历史账本**,批量 sed 改历史需要单独决定;其中 `0094:11` 指向一个不存在的文件名(`0057-p5-secure-channel-design.md`,实际是 `0057-p5-unified-encrypted-channel-design.md`),那一条因为 0094 是常被翻阅的近期记录,值得优先修。
