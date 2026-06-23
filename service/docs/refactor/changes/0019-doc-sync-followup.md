# 0019 · 文档同步补漏:0017/0018 遗留的设计文档漂移

日期:2026-06-23 · 范围:`docs/architecture.md`、`docs/error.md`、`docs/config.md`、`docs/dev.md`、本记录。**纯文档,无代码改动。**

## 背景

用户提问「这两次 push(0017/0018)有没有改设计文档,是不是没什么可补充的」。复核(`grep` 全 docs)发现**并非无可补充**:0017/0018 改了代码事实,却有几处对应设计文档未同步(违反 [README §0/§5](../README.md) 的双向同步纪律 + [keep-docs-in-sync] 自留经验)。本篇把这些漏补齐。

> 澄清:0017 **确实**改了设计文档(`wire.md` +21 行 / `models.md` +3 行);0018 只改了 `connection.md`(2 行 review 修复)。漏的是下面几处。

## 漂移清单 + 修复(对抗 `grep` 核出)

| 文档 | 漂移(代码已变、文档未跟) | 来源 | 修 |
|---|---|---|---|
| [architecture.md](../../architecture.md):160 | 仍称 TS 由 `pydantic2ts` 生成 | **0017**(已改为自包含 Python 生成器、无 node) | 改为「ws 走 `scripts/gen_wire_ts.py`(无 node)、REST 走 `openapi-typescript`(P7)」,指向 wire.md |
| [error.md](../../error.md):25/43 | `INVALID_MESSAGE` 全 docs 无提及 | **0018**(新增该码) | 解析错误行写明 `ErrorMessage(INVALID_MESSAGE)`;`ErrorCode` 示意清单补 `INVALID_MESSAGE` |
| [config.md](../../config.md):14/36 | 链接旧 `app/pokertable/gameconfig.py`;且本文「**不写代码内默认值**」原则与 0018 的 `app/gameconfig.py`(带默认值)**直接冲突** | **0018**(新建 dev gameconfig) | 链接改 `app/gameconfig.py`;加「当前状态」段:dev 阶段暂用带默认值常量、P8 收编时改 env 驱动/去默认/补 `poker.env`;旧文件是被取代原型物 |
| [dev.md](../../dev.md):10 | 配置表仍指旧 `app/pokertable/poker.env` + 旧 gameconfig | **0018** | 改指 `app/gameconfig.py`;注明 D 阶段用默认值、`poker.env` 随 P8 |

**为何是「记录偏离」而非「改代码就范」**:`app/gameconfig.py` 用带默认值常量是 [0018 决策 5](0018-d-dev-shell.md) 的有意选择(dev 脚手架 import 不依赖 env),且完整「配置收编」(env 单一事实源 + 去默认 + Field 边界)本就是 **P8** 阶段。它已满足 config.md「具名 / 集中 / 不散字面量」的一半,缺 env 那一半留 P8。故保留代码、把 config.md 改成「描述 P8 目标形态 + 标注当前 dev 状态」,而非现在提前做 P8。

## 实际改了什么

- `docs/architecture.md`:§客户端协议契约 一行 `pydantic2ts` → 自包含 Python 生成器(无 node)+ REST `openapi-typescript`(P7),指向 wire.md。
- `docs/error.md`:错误分类表「协议/解析错误」行 → `ErrorMessage(INVALID_MESSAGE)`;`ErrorCode` 示意清单补 `INVALID_MESSAGE`。
- `docs/config.md`:`gameconfig.py` 链接改新位 `app/gameconfig.py`;示例 import 改 `from app import gameconfig`;+「当前状态(D 阶段)」段(dev 默认值 vs P8 env 目标、旧文件勿用)。
- `docs/dev.md`:两套配置表的 gameconfig 行改指 `app/gameconfig.py` + D 阶段说明。

**偏离计划**:无(本篇即补 0017/0018 该同步未同步的文档)。

## 自 review

- **② 代码↔文档同步**:本篇就是补这一维之前的漏。修后再 `grep`:`pydantic2ts` 仅存于 wire.md(说明 node 缺失的那处,正确);`INVALID_MESSAGE` 进 error.md;`app/pokertable/gameconfig` 仅余「被取代原型物」语境的指称(config.md/dev.md 均已标注)。
- **③ 文档一致**:config.md 的「无代码默认」原则**保留为 P8 目标**,新增段明确当前 dev 偏离 + 收编时机,不制造「原则 vs 现实」的静默矛盾。
- **已知未改(非本批职责,记录在案)**:[log.md](../../log.md):59 仍引旧 `app/pokertable/models.py` 的 `field_serializer` 作日志脱敏来源——这是 **0017 之前就存在**的对旧原型代码的引用、且 log.md 是未实现的设计稿;wire 隐私已改结构性缺位(见 wire.md)。留待 log 模块实现时一并对齐,不在本 doc-sync 批内强改。
- **⑦ 账本**:本篇即账本;提交引用 `0019`、全英文。①④⑤⑥ 不适用(无代码/数据模型/测试改动)。

## 待办 / 下一步

- **P8 配置收编**:`app/gameconfig.py` 由「带默认值常量」改为 `pydantic-settings + poker.env + 无默认 + Field 边界`(config.md 目标形态),补 `poker.env.example`;届时回填 config.md/dev.md 的「当前状态」段。
- log 模块实现时对齐 log.md:59 的脱敏来源(结构性隐私 vs field_serializer)。
- (沿用)0017 的变更记录缺独立「实际改了什么」段(内容在自 review 内);如需统一格式可补,但 0017 已 push,低优先。
