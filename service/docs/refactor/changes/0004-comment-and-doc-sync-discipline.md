# 0004 · 注释规范 + 文档↔代码同步纪律(问答记录)

日期:2026-06-17 · 范围:`service/docs/coding_principle.md`、`docs/architecture.md`、`docs/storage.md`、`docs/core.md`、`docs/error.md`、`service/app/core/*`、`shell/world.py`

## 缘起(用户反馈三点)

P0 review(0003)交付后,用户提出三条:

1. **class / 枚举的字段都要写注释注明含义**,改代码并把这条写进编码规范。
2. **若用了非文档的结构/写法,必须修正文档**——文档不能和已改的代码不一致,这条也写进规范。
3. 追问:重构规则里要求**记录我们之间的问答、把有意义的总结进对应文档**,我做到了吗?

## 讨论结论 + 落地

### 点 1:字段注释

- 给 `app/core/` 全部 dataclass 字段、枚举成员补了行内含义注释(`domain.py`/`enums.py`/`commands.py`/`events.py`/`errors.py`/`cards.py`),`shell/world.py` 的 `Work` 同理。
- 与 0002 的「注释克制」**不矛盾,是澄清边界**:禁的是**文件开头复述文档的大段 docstring**;要的是**字段级的「这是什么」**。两条一起写进 [coding_principle.md](../coding_principle.md)「通用规范」+「提交前自检」。
- 更新 memory `code-comment-style`(原本只说"少注释",现补全这个区分)。

### 点 2:文档必须与代码一致(双向同步)

- 把规则写进 [coding_principle.md](../coding_principle.md):**实现采用了与设计文档不同的签名/字段/结构/命名,必须在同一次改动里同步对应设计文档**;"文档≠已落地代码"是缺陷不是待办。
- **修了 0003 遗留的真实不一致**:0003 把 `checkout`/`commit` 做成 `shell/world.py` 模块函数(而非文档伪码的 `World.checkout()` 方法)却**留着文档没改**,还在 0003 里错误地写了"设计文档本轮无需改"。本次纠正:
  - `architecture.md` GameLoop 伪码:`self.world.checkout(cmd)`→`checkout(self.world, cmd)`、`commit` 同理,并加一句为何是模块函数(World 是 core dataclass,挂方法破坏分层)。
  - `storage.md`:主循环伪码 + `checkout`/`commit` 小节标题改成模块函数签名,补 `Work(room_name/room/users)` 形状。
  - `error.md`:`self.world.commit(work)`→`commit(self.world, work)`。
  - `core.md`:动态建房处 `checkout(cmd)`→`checkout(world, cmd)`。
- **同步 core.md 域模型块到实现**:补 `Player.has_acted`、`Hand.last_raise_size`、`Seat.wait_for_big_blind`、`Room.leaving`、新增 `EntryVote` 类——这些字段我在 P0 加了(依据散在 rules.md),但 core.md 的域模型代码块当时没补,属 doc≠code。
- **`error.md` ErrorCode**:点明权威清单以 `app/core/errors.py` 为准、文档块是示意,并列出实现已含的扩充码(避免"文档列得不全"被当成不一致)。
- 新增 memory `keep-docs-in-sync-with-code`。

### 点 3:问答记录做到了吗?——部分做到,已补齐并制度化

- **诚实回答**:0002 那次反馈(注释克制 + 别盲目执行计划)我**有**记录——写进了 changes/0002 的「复盘修正」、更新了 README §0/§5、存了 memory。但我**没有把"每次问答都建记录"当成稳定动作**,这次(0004 的三点反馈)直到现在才落账。
- **补救**:本篇即这次问答的记录;并把"记录问答 + 结论回灌文档"写进 memory `keep-docs-in-sync-with-code`(对应 README §5 已有的要求),作为今后每次反馈的固定收尾。

## 验证

- 23 个 core/shell 测试仍全绿;core 纯度不受影响(只加注释 + 改文档)。

## 待办 / 下一步

- 进 P1 时继续守:新字段带注释、偏离文档即同步文档、每次讨论留 changes 记录。
- 0003 的 D 节遗留项(`timeout_s` 归属、ErrorCode 随用随定、wire/db payload 收紧、`StartHand.seat` 取舍)仍带去 P1。
