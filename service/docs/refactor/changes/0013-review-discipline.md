# 0013 · 提交前复审纪律:新增 review.md + 接入工作流

日期:2026-06-18 · 范围:`service/docs/review.md`(新)、`docs/coding_principle.md`、`docs/refactor/README.md`、`docs/dev.md`、`docs/testing.md`。**纯文档/流程,无代码改动。**

## 背景 / 打算改什么

按用户要求,把 P0–P1 重构里反复用到的 review 经验**提炼成一篇 [review.md](../../review.md)**,并立为硬纪律:**每次 submit(commit/push)前必做对抗式自 review,结论记进当前 `changes/NNNN`「自 review」段,无此段不 push。**

依据来自实战——每次 review 都在「测试全绿」之上抓到东西、无一空手:

- [0003](0003-p0-review.md):P0 对照文档 9 处漂移(类型放松不变量、wire 码大小写、缺事件字段)。
- [0006](0006-p0-review-followup.md):push 前多视角 review 4 处,含两处与「已同步」自我声明矛盾。
- [0009](0009-holistic-review-cleanup.md):reduce 前整体复审 16 候选 / 9 确认(34 死链、FOLD 条件文档↔代码矛盾、奇数零头环绕漏测)。
- [0010](0010-p1-reduce-start-hand.md)/[0011](0011-p1-player-action-showdown.md):push 前自 review(bootstrap 防躲盲、短牌堆守 Err、money path 守恒/退还边界)。

这些都是「绿测覆盖不到」的类别(文档同步、文档一致、数据建模、流程账本),证明 review 与测试不可互替——故立为门槛。

## 实际改了什么

- **`docs/review.md`(新)**:一句话定位(提交门槛)+ 为什么(实战例)+ 方法(范围聚焦 / 风险面优先 / 对抗核实 / 结论入账)+ **七维复审表**(每维附 changes/ 实战例)+ **core 正确性红线**(纯同步/回滚/守恒/隐私/身份顺序新鲜度/落库)+ 提交门槛契约 + 与其它文档关系。
- **接入工作流(同步到对应文档)**:
  - `refactor/README.md` §5「收工前」加「push 前对照 review.md 自 review、记进变更记录」必经步;§6 导航加 review.md。
  - `dev.md` 提交(commit)段加「commit/push 前先复审(提交门槛)」首条;日常流程 cheatsheet 在 `git commit` 前加 review 提示行。
  - `coding_principle.md` 阅读顺序 §6 加 review.md;「提交前自检」加引语——硬规则速查之上,push 前须走 review.md 完整复审。
  - `testing.md` 契约加第 5 条「绿测不等于可提交,push 前另做完整复审」。

## 自 review(本篇 · dogfood 新规)

按 review.md 七维对本次纯文档改动自查:

1. **③ 文档↔文档一致(本篇最高风险面)**:review.md 在 `docs/`,链 `changes/` 用 `refactor/changes/NNNN`、链兄弟 doc 用 `x.md`;接入方在 `docs/refactor/` 用 `../review.md`、在 `docs/` 用 `review.md`。已 `grep` 复验所有新增 review.md 引用的相对深度(见下「验证」),0 死链。
2. **⑦ 流程账本**:本篇即「讨论产物落文档」;提交将引用 `0013`、全英文。
3. **②④⑤⑥ 不适用**(无代码/数据模型/测试改动);**① 不适用**(未碰 core)。
4. **一致性**:review.md 与 coding_principle「提交前自检」分工明确(后者硬规则速查 = 前者维度①子集,已在两篇互链注明),不重复、不冲突。
5. **无过度**:review.md 表格 + 契约式,无散文堆砌(守 [0005](0005-chinese-comments-and-doc-debloat.md) debloat);接入处各加 1–2 行,不膨胀。

## 验证

- 相对链接复验:review.md 内所有 Markdown 链接目标按 `docs/` 为基解析均存在(`coding_principle.md`/`testing.md`/`dev.md`/`architecture.md`/`refactor/README.md`/`refactor/changes/0003..0011`),0 死链。
- 无泄漏的生成器标签;无代码改动,测试不受影响(沿用 0011 的 100 passed)。

## 待办 / 下一步

- 自本篇起,每个 `changes/NNNN` 落「自 review」段(0010/0011 已有,后续一律照办)。
- 回到 P1:局中离桌 + `_timeout`(rules.md ④),见 [TODO.md](../TODO.md)。
