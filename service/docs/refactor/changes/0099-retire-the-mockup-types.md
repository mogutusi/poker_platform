# 0099 · 退役 mockup 残留类型:`chips`/`phase` 漂移原来只活在死代码里(0076·M7)

日期:2026-08-26 · 性质:**死代码清理 + 口径改实(纯前端)**· 触发:[TODO.md](../TODO.md) 0076·M7「协议面换 `wire.gen.ts`,`poker.ts` 退回纯 UI 用途」。

## 开工前先核实,结论和登记的不一样

M7 一直被记成「协议面还在用 `poker.ts`,得换成 `wire.gen.ts`」,听起来是一次改组件的重构。**实际去数了一遍引用,发现协议面早就换完了**:

| 谁 | 用什么类型 | 判定 |
|---|---|---|
| `store/room.ts` / `dm.ts` / `joinFlow.ts` | `@/types/wire.gen`(`StateSnapshot`/`ServerMessage`/`DMDelivered`…) | **已经是对的**——协议边界在 store,类型来自 codegen 产物 |
| `app/game/page.tsx` 的 `Seat`/`SeatPlayer` | 页面内本地 interface | **不是协议类型**:字段是 `id/name/avatar/points/isReady`,`avatar` 根本不在 wire 上。这是喂给渲染的视图模型,正是 [frontend/docs/architecture.md](../../../../frontend/docs/architecture.md) 允许的那类 |
| `PokerCard.tsx` / `utils/card.ts` / `game/page.tsx` | `poker.ts` 的 `Card` | **合法 UI 类型**:`suit/rank/value` 服务于 54 张牌面图的渲染,architecture.md 明写「`poker.ts` 是 UI 自己的展示类型,不是协议类型」 |

那么被反复记档的 `chips`/`phase` 漂移在哪?**在没有任何人 import 的接口里。**

```
poker.ts 的 6 个导出           引用数
  Card                          3   ← 真 UI,留
  Player        (含 chips)      1   ← 只被 PlayerSeat.tsx 用,而 PlayerSeat 自己 0 引用
  GameState     (含 phase)      0
  GameAction                    0
  ApiResponse                   0
  WebSocketMessage              0
```

`PlayerSeat.tsx` 是上游 mockup 合进来的孤儿组件(0076 合入 79 文件时一起来的),牌桌页从来没用过它——真正在渲染的是 `game/page.tsx` 自己的座位实现。`Chip.tsx` 又只被 `PlayerSeat` 用,跟着一起是死的。

**所以 M7 不是重构,是删除。** 漂移的两个字段(`Player.chips`、`GameState.phase`)恰好全在死代码里,删掉它们,`poker.ts` 自然就变成 architecture.md 描述的那个「只剩纯 UI 用途」的样子,漂移一并消失。

## 打算怎么改

1. `src/types/poker.ts`:删 `Player`/`GameState`/`GameAction`/`ApiResponse`/`WebSocketMessage`,只留 `Card`;文件头注释改成「UI 展示类型,协议类型一律来自 `wire.gen.ts`」。
2. 删 `src/components/PlayerSeat.tsx`(0 引用)与 `src/components/Chip.tsx`(唯一引用者是前者)。
3. 口径改实——**这几处现在都会变成假话**:
   - [service/docs/architecture.md](../../architecture.md)「客户端协议契约」拿 `poker.ts` 的漂移当**现存反例**举例,漂移没了就得改成过去式,但**要留住那个教训**(手写第二份协议类型必然漂移),不能直接删。
   - [frontend/docs/architecture.md](../../../../frontend/docs/architecture.md) 的「历史包袱」段同理。
   - [TODO.md](../TODO.md) 0076·M7 勾掉,并写清「登记的判断与实际不符」。

**不动的**:`Card` 与它的三个消费者;`game/page.tsx` 的本地视图模型(它们是对的);任何协议面代码。后端零改动。

## 实际改了什么

按计划落地,没有偏离。

- **`src/types/poker.ts`**:只剩 `Card`,三个字段各带一句含义注释(花色/点数/排序值,并写明「牌力判定一律在服务器」,免得下一个人又拿 `value` 去算牌)。文件头写清「协议类型一律来自 `wire.gen.ts`」,并把那五个接口的来历与去向留成一段注释——**教训要留住,不能随代码一起消失**。
- **删** `src/components/PlayerSeat.tsx`(0 引用)与 `src/components/Chip.tsx`(唯一引用者是前者)。
- **三处口径改实**:[service/docs/architecture.md](../../architecture.md)「客户端协议契约」、[service/docs/wire.md](../../wire.md) 开篇、[frontend/docs/architecture.md](../../../../frontend/docs/architecture.md)「历史包袱」段——三处都拿这份漂移当**现存反例**举例。改法是**保留教训、把状态改成过去式**(「这条规矩是有代价换来的」),而不是把例子删掉:规矩本身正是被这个例子挣来的。
- **[TODO.md](../TODO.md) 两处登记都勾掉**:0076·M7 与 W 段那条「前端消费 wire.gen.ts(延后)」说的是同一件事(0072·C1 的注也指着它)。0093 的教训是同一件事登记在两处、只清一处就是新漂移。
- **[README.md](../README.md) §2 的历史表格不动**:那是「原型有哪些问题」的历史快照,不是现状描述,改它等于改写历史。

## 验证

| 层 | 结果 |
|---|---|
| 前端 `tsc --noEmit` | 通过 —— **这是删除类改动的第一道判据**:有任何引用漏网,类型检查就会红 |
| 前端 `npm run build` | 通过(dev server 未运行,不踩 `.next` 冲突那个坑) |
| 前端 vitest | 93 passed |
| 浏览器 `npm run test:e2e` | **16 passed** —— 删的是组件,真正能证明「没删坏渲染」的是这一层 |
| 三条冒烟 | 全部通过 |
| 后端 pytest | 779 passed(后端零改动,复跑确认) |

**引用审计**(删除类改动没有「反向变异」可做,改用穷举核实):

- `tsc` 全绿 ⇒ 静态引用为零。
- 全仓 grep `PlayerSeat` / `Chip` / 四个死接口名,覆盖 `src/` `e2e/` `scripts/`:除了我自己在 `poker.ts` 里写的那段说明性注释,**零命中**。
- `poker.ts` 现存三个 importer,全部只取 `Card`(`PokerCard.tsx` / `utils/card.ts` / `game/page.tsx`)。
- 复审补充核实:`git log --all --name-only` 确认历史上**从未有过** barrel 文件(`components/index.ts` / `types/index.ts`),所以不存在「经桶文件再导出」这条 grep 看不见的路径;`components.json` / `tailwind.config.js` / `tsconfig.json` 全是目录 glob,不逐个列组件;无 storybook。

**自己踩的一个小坑,记下来**:补 §8 那段时凭印象把 0078 的文件名写成 `0078-frontend-ws-client.md`,实际是 `0078-frontend-table-wiring.md` —— 链接扫描当场逮住 2 条死链。0092 记过同款(写死链前先 `ls` 一眼),这次又犯,说明**凭记忆写变更记录编号的文件名就是不可靠**,扫描不能省。

## 自 review

按 [review.md](../../review.md) 七维。本批是**删除 + 口径改实**,最高风险面只有两个:「删掉的东西真的没人用吗」与「M7 是不是被提前宣布完成了」。

- **① 分层 / 不变量**:后端零改动。前端不变量 4「协议类型只用 `wire.gen.ts`,不手写第二份」**由破转立**——`poker.ts` 只剩 `Card`,而 `Card` 是 architecture.md 明确许可的 UI 展示类型(服务 54 张牌面图,`value` 只用于展示排序)。store 那条协议边界一行未动。**注**:这句话第一版写的是「手写件删干净了」,复审查出 `rest.ts` 里还活着一份 `RoomStatus` 的手抄件,已改引 codegen 后这句才成立(见下「独立复审」第 3 条)。
- **② 代码↔文档同步**:本批的另一半。三处文档拿这份漂移当**现存反例**,漂移没了就必须改状态——但**改法是过去式,不是删例子**:这条「不手写第二份」的规矩正是被这个例子挣来的,把例子删掉,规矩就退化成一句没有来历的教条。同样地,`poker.ts` 文件头留了一段注释交代那五个接口的来历与去向。
- **③ 文档↔文档一致**:TODO.md 的**两处**登记(0076·M7 + W 段「前端消费 wire.gen.ts」)同批勾掉,并写清「登记的判断与实际不符」;README §2 的历史表格**有意不动**(它描述的是原型当年的问题,不是现状)。链接扫过 0 死链。
- **④ 数据模型正确性**:删掉的五个接口里有两个是「可表达的错误状态」的来源(`Player.chips`、`GameState.phase` 与后端 enum 不同名),删除即消除。留下的 `Card` 三个字段各自标注含义与边界。
- **⑤ 规范合规**:兑现「不留死代码」——这是本批的正题。注释讲「为什么」(为什么 `value` 只能用于排序、那五个接口为何存在过)。
- **⑥ 测试充分**:删除类改动**没有反向变异可做**(没有新行为可以被改坏),所以改用穷举核实,三条独立证据:`tsc --noEmit` 全绿(静态引用为零)、全仓 grep 四个接口名与两个组件名零命中(覆盖 `src/`/`e2e/`/`scripts/`,含动态与字符串引用面)、**16 个浏览器用例全绿**(真正证明渲染没被删坏的那一层)。另核过 `components.json` 只有目录别名、不逐个列组件。**缺口如实记**:没有任何自动化能防住「日后又有人手写一份协议类型」——`wire.gen.ts` 的漂移守门(`test_codegen_uptodate.py`)只保证生成产物与后端一致,**管不着有人在旁边另写一个 interface**。这正是这次漂移当初能长出来的原因。
- **⑦ 流程账本**:变更记录先行。**开工前先数引用,结论推翻了登记的判断**(M7 不是重构而是删除),这一点写进了记录正文而不只是收工备注——照着登记直接开干,就会去改一堆本来就对的协议面代码。

### 独立复审

另起一个 agent 做对抗式核实(专攻「是否有 tsc 看不见的引用」「M7 是否被提前宣布完成」「三处文档改后是否属实」「两处登记是否一致」)。**可达性与账本两项判为干净**:全仓 grep 零命中、`git log --all --name-only` 确认历史上从未有过 barrel 文件、`components.json`/`tailwind.config.js`/`tsconfig.json` 都只用目录 glob、无 storybook;两处登记也一致。但抓到**三条实质**,已全部修掉:

1. **`wire-protocol-guide.md` §8 的「还没有」清单里挂着「前端 WS client / 组件消费本身……替换 mock 的 `poker.ts`」** —— 这条**直接与本批刚勾上的那个 ✓ 打架**:前端 WS client 自 0078 就交付了。而且它不是历史记录,§8 是**明确维护的现状节**(TODO 里还留着上一次 truth-up 它的痕迹)。我自称的方法是「grep 所有把漂移当现状讲的文档」,恰恰漏了它——因为我 grep 的是 `poker.ts` 与「漂移」,而这条的措辞是「替换 mock 的」。**教训:truth-up 时按关键词 grep 会漏掉换了说法的同一件事。** 已把它从「还没有」移走,并在「已交付」侧补一句写明何时交付、为何一直挂着。
2. **`wire-protocol-guide.md` §1 那句「旧的手写 `poker.ts` 是 UI mockup 聚合类型 + 本地 mock 牌局逻辑;协议一律改用 `wire.gen.ts`」两半都已作废**:mock 牌局逻辑 0078 就删了,聚合类型本批删了,而祈使句「一律改用」读着像还有活要干。这是前端最可能读到 `poker.ts` 说明的地方。已改实。
3. **`transport/rest.ts` 里还活着一份手写的 wire 枚举**:`RoomMeta.status: 'pending_start' | 'hand_started'`,而 `wire.gen.ts` 早就导出了 `RoomStatus`(`store/room.ts` 也一直在用)。这让我在 TODO 和自 review ① 里写的「协议面早就换完了 / 手写件删干净了」**说过头了**。它**不**受 REST DTO 那条未解项阻塞(`RoomStatus` 是 ws codegen 产物,今天就在),所以修法是一行 import 而不是把话说软。已改引,`tsc` 复验通过。

另外接受一条**措辞精确化**:「`chips`/`phase` 与后端 enum 漂移」把两种病混成一种——`Player.chips` 是**字段名**对不上(后端叫 `points`),只有 `GameState.phase` 是**枚举取值**漂移。三处文档都照此拆开写了;truth-up 批次里把话说准是本分。

### 有意没做,留档

- **`Chip.tsx` 一并删了**:它唯一的消费者是 `PlayerSeat`。牌桌页有自己的筹码渲染,不需要它;留一个零引用的组件只会让下一个人以为「这是现役的公共组件」。要是日后真需要一个通用筹码件,从 git history 取回比留着腐烂强。
- **防「再手写一份协议类型」的守门**:见 ⑥ 的缺口。要做得有个检查(例如禁止在 `types/` 之外声明与 wire 消息同名的 interface,或直接约定 `poker.ts` 不许再增导出),但那是一条新的工程约定,值得单独议。
