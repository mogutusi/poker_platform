# 0005 · 注释改中文 + 设计文档去 Python 臃肿(问答记录)

日期:2026-06-17 · 范围:`service/app/core/*`、`shell/world.py`、`docs/coding_principle.md`、`docs/dev.md`、`docs/architecture.md`、`docs/core.md`

## 缘起(用户反馈两点)

1. **设计文档太臃肿,塞了太多 Python**——别把成段 Python 搬进设计文档。
2. **注释用中文(代码里也是)**,之前忘了说,写进文档。

## 讨论结论 + 落地

### 点 2:注释用中文

- 把 `app/core/` 全部文件 + `shell/world.py` 的注释从英文改成中文(字段注释、函数内注释)。**标识符仍英文**。
- 写进 [coding_principle.md](../../coding_principle.md):「注释一律用中文;只有标识符和提交信息用英文」。
- **修正冲突**:[dev.md](../../dev.md) 原写「代码与 commit 用英文」与此冲突,改为「标识符 + 提交信息用英文,代码注释用中文」——避免又留 doc≠intent(守 0004 立的双向同步规则)。

### 点 1:设计文档去 Python 臃肿

- 写进 [coding_principle.md](../../coding_principle.md):「设计文档讲设计(散文 + 表格 + 极简伪码),不堆成段 Python 类定义/实现;精确字段/签名以代码为准(代码已有中文字段注释),文档**引用**代码而非复制」。
- 应用,撤掉我之前加的 Python 臃肿:
  - [core.md](../../core.md) 域模型:把整段 `World/Room/EntryVote/Seat/Hand/Player` 的 Python 类定义**换成一张实体职责表 + 指向 `app/core/domain.py`**。一举两得:既去臃肿,又**消除字段级 doc↔code 双份维护**(domain.py 是唯一事实源)。关键不变量(底牌隐私、墙钟外移)保留为散文。
  - [architecture.md](../../architecture.md) GameLoop:把整段 `class GameLoop` Python(我之前还加了 import 行)换成 5 行极简伪码,保留「checkout/commit 为何是模块函数」的散文说明。
- **没有**回头重写所有早于我的 Python 块(那是原作者的设计内容,用户未要求、且风险大);本轮只去掉我新增的臃肿 + 立下原则,今后新写文档照此执行。

## 验证

- 23 测试全绿;core 只改注释,纯度不变。

## 待办 / 下一步

- 进 P1 时:新代码中文注释、设计文档不堆 Python(用表格/伪码 + 引用 domain.py)。
- 历史遗留:其余设计文档里成段 Python(原作者所写)暂留;若后续也要瘦身再单开一篇,不在本轮。
