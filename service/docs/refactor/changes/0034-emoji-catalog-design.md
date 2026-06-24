# 0034 · 设计:聊天表情(emoji)目录 —— 单一事实源 + 前端渲染

日期:2026-06-24 · 范围(**文档/设计,不含代码实现**):`messaging.md`(+「表情(emoji)」节 + 契约)、`wire.md`(表情目录作另一份 codegen 单一事实源)、`TODO.md`(+实现项)。**实现留作后续 TODO 单元**。

## 背景 / 讨论缘起

需求:聊天想加**表情**。提案——做一份**表情配置(枚举)**,前后端同步一份;消息文本里用 `#表情1` 或 `[xxx]` 这类定界格式引用,只要 code 在表情目录枚举内,**前端渲染时把占位符换成表情**。让我选定格式 + 评估是否是新设计 + 同步设计文档 + 加 TODO。

**现状确认**:房间聊天已落地(`_room_chat` → `Broadcast(ChatMessage{from_nick, text})`,0021 + 0033 shell 文本防护),**纯文本**;私聊(DM)仅在 [messaging.md](../../messaging.md) §私信**设计、未实现**,亦纯文本。故聊天目前只能发文字。**表情是新设计**——messaging.md/wire.md 无表情概念。

## 关键设计决策(批判性)

1. **格式定为 `[code]`(定界括号),不用 `#code`**。理由:`[code]` 边界无歧义(`#smile happy` 不知 code 到哪结束;`[smile]` 明确),且贴合微信/QQ 习惯(`[微笑]`);正则简单 `\[([a-z0-9_]+)\]`。`code` 是稳定 **ASCII snake_case** 键(= 目录枚举成员,如 `[smile]`/`[thumbs_up]`/`[poker_face]`),避免编码/换名问题;**显示名(label)可中文**。
2. **后端纯透传,渲染在前端**。`ChatMessage.text` 与 `_room_chat`(及未来 DM)**完全不变**:`[code]` 占位符就当普通文本随 `text` 流转;前端按目录把**已知** code 换成字形,**未知 `[foo]` 原样显示为文本**(绝不因含方括号而拒收)。⇒ reduce 维持只读零配置(承 0021/0033)、零后端耦合、无新增 wire 字段/消息。
3. **表情目录 = 单一事实源,codegen 到 TS**(同 `ErrorCode`/wire 枚举,见 [wire.md](../../wire.md))。后端写一份**封闭目录**,生成器吐 TS,前端只消费、不手写第二份(杜绝 FE/BE 漂移)。目录每项:`code`(ASCII 键 = `[code]` 令牌 + 枚举成员)+ `label`(中文显示名)+ 默认 `glyph`(一个 Unicode 表情字符,给无素材时的兜底字形)。**前端可按 code 覆盖为自定义贴纸图**(故同一目录既支持 Unicode 表情、也支持自定义贴纸,后者是前者超集)。
4. **跨聊天面通用**:房聊(现)+ 私聊(将来)共用同一 `[code]` 约定 + 目录——它是聊天**渲染约定**,不是某条消息的字段。
5. **边界 / 取舍(v1)**:
   - **长度**:`[code]` 是文本,计入 `ROOM_CHAT_MAX_TEXT_LEN`(全表情消息仍有界),无需特殊处理。
   - **转义**:用户想发**字面量** `[smile]` 而非表情 → 转义(如 `\[smile]`)是后续 nicety;v1 已知 code 总渲染。
   - **校验**:**不**在后端校验 code 合法性(透传);合法集由前端目录把关(渲染未知即按原文)。要做服务端校验/统计再议。
   - **脱敏**:表情 code 非敏感;聊天正文不写日志的红线不变([log.md](../../log.md))。

## 目录落点 + 实现要点(留作 TODO)

- **后端目录**:新 `app/wire/emoji.py`(与 wire 单一事实源同处)——`EmojiCode`(封闭 `StrEnum`,值 = code)+ `EMOJI_CATALOG: dict[EmojiCode, EmojiMeta]`(`label` + `glyph`)。
- **codegen 扩展**:[scripts/gen_wire_ts.py](../../scripts/gen_wire_ts.py) 现仅吐「被 wire 消息引用」的枚举(`generate()` 的 `ref_set` 过滤);表情目录**不被任何消息引用**,需加一处「无条件吐表情目录」钩子——产 `EmojiCode` 联合 + `EMOJI_CATALOG` 常量(`code→{label,glyph}`)。漂移由 `test_codegen_uptodate` 一并兜。
- **前端**:从 `wire.gen.ts`(或同目录新产物)读 `EMOJI_CATALOG`,据 codes 构正则,聊天渲染时把 `[code]` 替换为 `glyph`/贴纸;未知原样。
- **起始目录(提案,实现时定稿)**:`smile 😊 微笑`/`laugh 😂 大笑`/`cry 😭 哭`/`cool 😎 酷`/`thinking 🤔 思考`/`poker_face 😐 扑克脸`/`thumbs_up 👍 赞`/`clap 👏 鼓掌`/`fire 🔥 火`/`gg 🎉 打得好`/`fold 🏳️ 弃牌`/`all_in 🟢 全下` 等约 10–12 个。

## 同步了哪些文档

- **[messaging.md](../../messaging.md)**:新增「## 表情(emoji)」节(格式 `[code]` / 后端透传 / 前端渲染 / 目录单一事实源 / 跨聊天面)+ 契约项;§待定补「转义 / 服务端校验」。
- **[wire.md](../../wire.md)**:codegen 管线节补「表情目录」作另一份单一事实源产物;§待定标实现待补。
- **[TODO.md](../TODO.md)**:P7 messaging 下新增实现项。

## 自 review

方法:文档/设计变更,无代码 → 聚焦 review.md 维度 ②(代码↔文档)③(文档↔文档)⑦(账本);并自我反驳设计取舍。结论:无存活缺陷。

- **②/③ 文档一致**:表情设计与既有契约**不冲突**——后端透传 ⇒ `ChatMessage`/`_room_chat`/wire 隐私红线/身份不进报文**全不变**(无需改 server.py/reduce);目录 codegen 复用 wire.md「单一事实源 + codegen」原则(新增产物,不破现有)。messaging.md「表情」节 + 契约、wire.md 管线节、TODO 三处口径一致(格式 `[code]`、后端透传、前端渲染、目录单源)。
- **⑦ 账本**:本篇记讨论缘起 + 决策 + 同步清单;TODO 加项;提交 docs-only,引用 0034、全英文。
- **对抗自评 / 驳回的备选**:① 「后端解析文本成结构化 emoji 字段」——驳回:增后端耦合 + 改 wire 字段,与用户「前端渲染」意图相悖、违反 reduce 只读简洁;② 「`#code` 格式」——驳回:边界有歧义;③ 「后端校验未知 code 拒收」——驳回:会误拒含方括号的正常文本,且渲染本就前端事;④ 「单独 emoji.md 文档」——驳回:表情是聊天渲染约定,归 messaging.md 一节即可,不另起文档(避免文档碎片)。
- **批判性自评**:此设计的价值在「**几乎零后端改动**」——表情纯前端渲染 + 一份 codegen 目录,后端只多产一份共享词汇表;最大风险是 FE/BE 目录漂移,已用「codegen 单一事实源 + 漂移测」消解(同 ErrorCode/wire 既有做法)。

## 待办 / 下一步(实现单元)

- 实现:`app/wire/emoji.py` 目录 + `gen_wire_ts.py` 吐表情目录 + 前端渲染 `[code]` + 起始目录定稿 + 测(codegen 漂移 / 目录非空 / code 形如 `[a-z0-9_]+`)。
- 私聊(DM)落地后,表情约定同样适用(无需额外工作,文本即带 `[code]`)。
