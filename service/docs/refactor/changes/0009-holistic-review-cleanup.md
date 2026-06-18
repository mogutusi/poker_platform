# 0009 · 整体复审:已重构 core/shell + 全部 docs 一致性清理

日期:2026-06-18 · 范围:`service/docs/*`(core/architecture/TODO + changes/0001-0008 链接)、`service/tests/core/test_sidepot.py`。**无 core 代码改动**(审计未发现正确性 bug)。

## 背景 / 做了什么

进 reduce.py 之前,应要求对**已落地的全部重构产物**(P0 `core/*` + `shell/world.py`、P1 `rules/{betting,sidepot,blinds}` + `deck`、`tests/*`,以及全套设计文档 + `refactor/`)做一次整体复审,重点查:代码↔代码内聚、**代码↔文档同步**、**文档↔文档一致**、规范合规、测试充分、流程账本(changes/、TODO)准确。

方法:6 维 finder(core 内聚 / code-doc / doc-doc / 规范 / 测试 / 流程账本)× 每条候选 2 个「默认反驳」核实者(字面 + 影响)双签确认。**16 条候选、9 条确认、7 条驳回**。**9 条确认全是文档/流程/测试卫生,零 core 正确性 bug**——三块规则模块 + 工作副本 API 经整体核对仍稳。

## 确认并已修

1. **core.md「dead blind」残留**(死盲机制已被「付盲即玩 / 等大盲」取代,见 [rules.md](../../rules.md) ①;core.md 自身 L99 也说「不依赖死盲记账」):
   - §1 step 4(下盲)括注 `(含 dead blind 处理,见下)` → `(新玩家入局「付盲即玩 / 等大盲」见下)`。
   - §测试「必须覆盖」清单 `dead blind` → `入局付盲即玩/等大盲`。
2. **core.md §2 FOLD 条件失同步**:原写 `last_bet>0 才允许`,与权威 [rules.md](../../rules.md) ② 及实现 `betting.apply_action`(`bet_amount < last_bet`)在「已跟平者(如 preflop 大盲)」矛盾——改为 `仅当 bet_amount < last_bet`,对齐代码。
3. **architecture.md §测试**清单同样的 `dead blind` → `入局付盲即玩/等大盲`。
4. **changes/0001-0008 跨文档链接深度错**:changes/ 在 `docs/refactor/changes/`,设计文档在 `docs/`(上两级),但链接写成单 `../`(解析到不存在的 `docs/refactor/rules.md`)。**34 条死链**统一 `../X.md → ../../X.md`、`../../app → ../../../app`;`../README.md`/`../TODO.md`(确在上一级)保持不动。复审后全 docs 相对链接 0 死链。
5. **TODO.md L26 测试计数标注**:`deck/betting/sidepot 58 测试` 把累计数(58)误标成这三个文件的数(34)→ 改 `34 测试,共 58`,与同行 `blinds 7 测试,共 65` 体例一致。
6. **`test_sidepot.py` 奇数零头环绕未测**:原 ③.5 只用 `button=0`,`(seat-button)%seat_size` 退化成普通减法,漏取模也能过。补 `test_odd_chip_wraps_around_nonzero_button`(`button=4`、赢家座位 1 vs 5,钉死「庄左最近」取模)。代码本就正确,此为补漏。**66 测试全绿**。

## 驳回(7 条,均非缺陷)

核实者对每条给出反例/组合证明:`events.py` 占位基类的单行 class docstring 属 coding_principle L41 例外(非模块头、无字段);其余 6 条是**尚未落地的 reduce/shell 上的覆盖空缺**(`street_closed` 真空真分支、③.6/③.7 的跑公共牌/无摊牌控制流、`new_here=False` 入局路径、`world.py` 平凡分支、min-raise 拒绝臂/连投守恒的更强断言)——消费方 reduce 未建,按审计基线不算缺陷,留待 reduce 落地时随其测试补。

## 待办 / 下一步

- reduce.py 落地时,把上述驳回的 reduce 级覆盖随其测试补齐(`street_closed` 真空真、跑公共牌/无摊牌控制流、入局 `new_here` 分支)。
- 继续 P1(三)reduce.py(或先补 blinds 入局资格/免盲投票),见 [TODO.md](../TODO.md)。
