# 0035 · 实现:聊天表情目录(单一事实源 + codegen + 前端渲染)

日期:2026-06-24 · 范围:`app/wire/emoji.py`(新:`EmojiCode`/`EmojiMeta`/`EMOJI_CATALOG`)、`scripts/gen_wire_ts.py`(+`_emit_emoji_catalog` 无条件吐目录)、重生成 `frontend/src/types/wire.gen.ts`、`frontend/src/utils/emoji.ts`(新:`tokenizeChat`/`chatToPlainText` 消费目录)、`tests/wire/test_emoji.py`(新)、文档(`messaging.md`/`wire.md`/`TODO.md` 标落地)。落地 [0034](0034-emoji-catalog-design.md) 设计。

## 背景 / 为什么

[0034](0034-emoji-catalog-design.md) 定了聊天表情设计:**前端渲染约定 `[code]` + 后端单一事实源封闭目录(codegen 到 TS),后端纯透传、不加协议字段**。本批落地之。

## 关键设计决策(承 0034,实现细化)

1. **目录落 `app/wire/emoji.py`**(与 wire 单源同处):`EmojiCode`(封闭 `StrEnum`,值即 code、自文档化)+ `EmojiMeta`(frozen dataclass:`label` 中文名 + `glyph` 默认 Unicode 字形)+ `EMOJI_CATALOG: dict[EmojiCode, EmojiMeta]`。起始 12 项(偏扑克:`smile`/`poker_face`/`all_in`/`fold`…)。code 限 `[a-z0-9_]+` 对齐前端令牌正则。
2. **codegen 无条件吐**:`gen_wire_ts._emit_emoji_catalog` 产 `EmojiCode` 联合 + `EmojiMeta` 接口 + `EMOJI_CATALOG: Record<EmojiCode,EmojiMeta>` 常量(实际数据)。**不被任何 wire 消息引用**,故**不走** `_discover`/`ref_set` 断言路径(不进 `_ENUM_ORDER`/`_VALUE_OBJECT_ORDER`),在 `generate()` 末尾独立 block 直吐。复用 `_emit_enum`/`_emit_interface`;const 用 `json.dumps` 安全转义键/值(中文 `ensure_ascii=False`)。
3. **后端纯透传不变**:`ChatMessage`/`_room_chat`/wire 报文/隐私红线/身份**全不动**——目录只作共享词汇 + 前端渲染,reduce 不感知 `[code]`(承 0021/0033 只读)。
4. **前端消费 `frontend/src/utils/emoji.ts`**:`tokenizeChat(text) → ChatSegment[]`(文本/表情段,供 React 渲 glyph 或按 code 换自定义贴纸图;**未知 `[foo]` 留作文本段、绝不吞**);`chatToPlainText(text)`(纯文本 glyph 替换便捷版)。只 import 生成的 `EMOJI_CATALOG`,不手写表情集。

## 实际改了什么

- **`app/wire/emoji.py`(新)**:`EmojiCode`(12 项 StrEnum)+ `EmojiMeta(label, glyph)` + `EMOJI_CATALOG`(全覆盖)。
- **`scripts/gen_wire_ts.py`**:import emoji 目录;`_emit_emoji_catalog`(联合 + 接口 + const);`generate()` 末尾加 emoji block;import `json`。
- **`frontend/src/types/wire.gen.ts`**:重生成(+ `EmojiCode`/`EmojiMeta`/`EMOJI_CATALOG`)。
- **`frontend/src/utils/emoji.ts`(新)**:`ChatSegment` 类型 + `tokenizeChat` + `chatToPlainText` + `isKnown` 守卫;import `@/types/wire.gen`。
- **`tests/wire/test_emoji.py`(新,4 测)**:目录键 == `EmojiCode` 全集(无漏无孤儿)、code 形制 `[a-z0-9_]+`、meta label/glyph 非空、`generate()` 吐 `EmojiCode`/`EmojiMeta`/`EMOJI_CATALOG` + 每个 code/glyph(防生成器回归漏特性)。
- **文档**:`messaging.md`「表情」节 + 待定标已落地 0035;`wire.md` 共享词汇目录标已落地;`TODO.md` 勾项。

311 全绿;codegen `--check` 干净。

## 自 review

方法:对照 [review.md](../../review.md) 跑聚焦对抗式 review **子代理工作流**(4 维:TS 有效性 / codegen 正确性 / 分层+透传 / 测试+文档)。结论 **go,0 must-fix**:4 候选全 nit,三大高风险面经核实均 sound——(1) 后端纯透传:`grep app/` 确认目录模块外**零 emoji 引用**,`ChatMessage`/`_room_chat` 不触目录;(2) codegen:`_emit_emoji_catalog` 读核对生成产物逐字匹配(含多码点 🏳️ 的 `json.dumps` 转义),`--check` + 字节比对兜;(3) 前端 TS(无 node 不可执行,全仓约束)经审读正确(未知 code `continue` 不进位 → 末段 `slice` 原样留存、绝不吞;`isKnown` 经 `hasOwnProperty` 收窄;`chatToPlainText` 未知回退 `whole`)。**已采纳 1 test nit**:`test_codegen_emits_emoji_catalog` 由「裸子串」升级为**逐项整行精确**(glyph 绑定到其 code + JSON 形制)+ 断言每 code 是联合成员。逐维:

- **① 分层 / 不变量**:`emoji.py` 在 `app/wire`(非 core);**不触 reduce / 不进报文**——`grep` 确认 `ChatMessage`/`_room_chat`/wire 报文未改,核心红线(隐私/身份/只读)零影响。codegen emoji block 走独立路径,不干扰既有消息发现/断言。
- **② 代码↔文档**:实现与 0034 设计一致(`[code]`/透传/单源 codegen/前端渲染);messaging.md「表情」节、wire.md、TODO 三处由「待 TODO」改「已落地 0035」,口径统一。
- **③ 文档↔文档**:0034(设计)↔0035(实现)交叉链;messaging/wire/TODO 与 `emoji.py`/`wire.gen.ts` 字段一致(`EmojiCode`/`EMOJI_CATALOG`/`label`/`glyph`)。
- **④ 数据模型**:`EmojiMeta` frozen dataclass(label/glyph 带注释);`EmojiCode` 自文档化值枚举(一行说明取值编码,符合 coding_principle 例外);目录全覆盖由测守门。
- **⑤ 规范**:无裸字面量(起始集是目录数据本身);中文注释 / 英文标识符;`json.dumps` 转义防注入畸形 TS;前端 TS 注释讲「为什么」(透传 / 未知原样 / 单源不手写)。
- **⑥ 测试**:目录完整性(==全集,杀「加 code 漏配 meta」)、code 形制(对齐前端正则)、codegen 吐目录(杀「生成器回归漏 emoji」);**前端无 node/无测试运行器**(同全仓 frontend 为未构建源),`utils/emoji.ts` 纯函数经审读、逻辑简单(tokenize/replace),未知 code 不吞——未由工具验证,记此局限。
- **⑦ 账本**:打算↔实际一致;TODO 勾项 + 计数;提交引用 0035、全英文。

**对抗自评 / 驳回**:① 「前端 util 不可由工具验证 = 风险」——接受为已知局限(全仓 frontend 皆未构建源、无 node;函数纯且简单、已审读;后端目录正确性由测钉死);② 「emoji 进 core ErrorCode 那种共享枚举?」——否,emoji 是 wire 层共享词汇(`app/wire/emoji.py`),非 core 游戏规则,放 wire 正确;③ 「未知 `[foo]` 该否被后端拒/清洗?」——否(0034 决策:透传,前端渲染未知为文本),`tokenizeChat`/`chatToPlainText` 均保留未知原样(测覆盖语义)。

> 批判性自评:本批价值是「**几乎零后端表面积**新增表情能力」——`ChatMessage`/reduce/wire 报文一字未改,只多一份 codegen 共享目录 + 前端纯函数。最大风险是 FE/BE 目录漂移,已由「单源 + `test_codegen_uptodate` 字节比对 + `test_emoji` 吐目录断言」三层兜死。

## 待办 / 下一步

- 前端把 `tokenizeChat` 接进真实聊天 UI 组件(随聊天界面落地;现 frontend 仅 mockup)。
- 转义字面量 `[code]`(`\[code]`)/ 服务端 code 校验 / 富文本 @提及 — 后续 nicety。
- 私聊(DM)落地后,表情约定自动适用(文本即带 `[code]`,无需额外工作)。
