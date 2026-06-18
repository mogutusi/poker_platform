# 德州扑克规则细则(rules)

## 定位

本文把 [core.md](core.md) 标为「待定/高风险」的三块**钉死成可实现、可单测的精确规则**:**①座位与盲注(含 heads-up)· ②下注轮关闭判据 · ③边池分配**。这是 reduce 里最易出钱(积分)错的地方,**先按本文写穷举测试,再写实现**。

> 本文是 core 纯逻辑,不碰 IO(见 [core.md](core.md) 不变量 1)。所有金额单位是积分;`SB` = `small_blind`,`BB` = `2*SB`(大盲)。

## 两处简化决策(本平台选定,可改)

1. **入局「付盲即玩 / 等大盲免费」二选一**:错过上一手的 ready 玩家,下一手可投一个大盲**立刻入局**(默认、体验正常),或不付而**等大盲位轮到自己**免费入局。要点是「开始玩必付一个大盲」——既好用又防躲盲(见「入局与防躲盲」)。不做赌场式按位置精确记 dead/live 的死盲账。
2. **all-in 超过当前注一律重开行动**:任何 all-in 只要总额 > `last_bet` 就重置其他人的"已行动",允许再加注。绕开赌场「不完整加注不重开」的复杂规则;本规模公平且确定(见「下注轮关闭」)。

---

# ① 座位与盲注

## 定庄与盲位(每手开局)

记 `N` = 本手 `READY_TO_PLAY` 且坐下的人数(`≥2` 才能开)。`button_position` 推进到下一个合格座位。然后:

| 人数 | 小盲 SB | 大盲 BB | 庄家(button) | preflop 首个行动 | postflop 首个行动 |
|---|---|---|---|---|---|
| **2(heads-up)** | **button 本人** | 另一人 | = SB | **button/SB** | 大盲(非 button) |
| **≥3** | button 下一位 | SB 下一位 | button | BB 下一位(UTG) | button 下一位(SB 位起第一个未弃牌) |

> **heads-up 是唯一特例**:庄家就是小盲、preflop 先行动、postflop 后行动。务必单独测。

排座:把合格玩家按「庄之后 → 庄」顺序排成 `players`,使 `players[0]=SB`、`players[1]=BB`(heads-up 时 `players[0]=button=SB`)。

## 下盲

- SB 投 `SB`,BB 投 `BB`:置各自 `bet_amount`,从 `Player.points` 扣。
- `last_bet = BB`、`last_raise_size = BB`(供 min-raise,见②)。
- 盲注**不置 `has_acted`**——SB/BB 都还没"自愿行动";尤其 BB 保留 preflop 选择权(见②)。
- 投盲后 `points==0` 的人置 `ALLIN`(短码玩家盲注即 all-in)。
- 行动者 = preflop 首个行动位(见上表)。

## 入局与防躲盲(付盲即玩 / 等大盲免费)

「错过上一手」的 ready 玩家——涵盖**首次入座、换座、退房再进、中途 `SITTING_OUT` 后回来**——下一手有两种入局方式:

- **付盲即玩(默认)**:投一个大盲,**下一手立刻被发牌、马上能玩**。
- **等大盲免费(可选)**:不想现在付,就等大盲位轮到自己那一手,免费入局。

**关键不变量:任何"开始玩"的路径都要付一个大盲——要么现在 post、要么等到当大盲付。没有"免费空降到好位置"。** 这同时给到好体验 + 防躲盲:

- 新人/回来的人**不必干等一圈**,付个大盲马上玩(你担心的"干等"只是不付费者的可选项)。
- **躲盲不划算**:轮到大盲想跑?跑了回来还得 post 一个大盲才能玩 ⇒ 照样付、还白少打几手。每手都跑了重进的 serial 躲盲要**每手付一个大盲**,远比正常 ~1.5 BB/圈 贵。
- 这覆盖所有离开方式(换座 / 退房再进 / `SITTING_OUT` 后回来),判据是「上一手是否参与」,不是只看 `new_here` 标志。

**post 算 live 还是 dead(决策,可改)**:

- **live(推荐、简单)**:post 的大盲算作本手 `bet_amount = BB`,等于你多当一个"大盲",随后正常行动。
- **dead(更公平、略繁)**:post 进池但不计入 `bet_amount`,你仍要跟注才能看牌(消除"在好位置 post 还拿 BB 抵注"的微弱便宜)。本规模 live 够用。

**其它**:

- **入局者占盲位(实现简化,0010)**:付盲即玩的入局者本是非盲位投一个 BB(live);若排座后恰好落在 SB/BB 位,则**以结构盲注充作入局付费、不重复 post**(SB 位入局者因此本手只付 SB)。这属本节明示「不做精确死/活盲记账」的可接受近似——精确账要按位置补欠,本规模不值当。
- **bootstrap 例外**:开桌第一手 / 桌上还没有任何已入局玩家时,所有 ready 玩家**正常入局、只下常规盲注**(无盲注结构可躲,也不收入局 post),否则会死锁。
- **位次**:button/盲注在已入局座位间轮转;选"等大盲免费"的人,按其座位被 BB 路过时入局(精确判定属实现细节,下方测试钉行为)。
- **默认**:坐下并 ready 即视为选"付盲即玩";要免费等就显式设"等大盲"(一个 wire 标志,见 [wire.md](wire.md))。
- `new_player_seat_list` 不需要(本方案不依赖死盲记账)。

## 免盲投票(全票通过则免费入局)

有 `new_here` 玩家时,**已入局玩家可投票免掉这次的入局盲**——让新玩家不 post、不等大盲,直接正常入局。**全票通过才免**;任一人反对(或开局时还没全票)→ 回退到上面的「付盲即玩 / 等大盲」常规。

- **投票人** = 当前**已入局(非 `new_here`)的 `READY_TO_PLAY`** 座位——他们的 EV 被"放人免费进来"影响,所以由他们定。`new_here` / 观战 / `SITTING_OUT` 不投票。
- **命令**:`OpenFreeEntryVote()` 开一次投票;`VoteFreeEntry(approve)` 各投票人表态(见 [core.md](core.md))。状态挂 `room.entry_vote`。
- **结算**(每票后判):
  - 全部投票人都 `approve` → **快照**当前 `new_here` 集合到 `room.waive_entry_for`,下一手他们免费正常入局;清空 `entry_vote`。
  - 任一 `reject` → 投票失败、清空,候选回到常规付盲即玩 / 等大盲。
- **StartHand**:`waive_entry_for` 里的新玩家**直接发牌、不 post 不等**(正常参与、盲注照常由 SB/BB 下);其余 `new_here` 走常规。开局后清 `waive_entry_for` 并清这些人的 `new_here`。
- **非阻塞**:投票不卡 `StartHand`;开局时若还没全票通过,就按常规处理(没投到不算免)。
- **不算躲盲**:免盲是全票自愿让利(consensual),不是钻空子。`waive_entry_for` 用**快照集合**,防"投票通过后才坐下"的人蹭免费。
- **bootstrap 时本就免费**(开桌第一手无盲注结构),不需要投票。

## 测试 ①

1. **3 人定位**:button=座 0 → SB=座 1、BB=座 2、UTG(首行动)=座 0(button)。
2. **6 人定位**:button=2 → SB=3、BB=4、UTG=5、首行动=5、postflop 首行动=3。
3. **heads-up**:两人座 0/1,button=0 → SB=0(=button)、BB=1;preflop 首行动=0;postflop 首行动=1。
4. **庄推进跳过非 ready**:button=0,座 1 是 `SITTING_OUT` → SB 落到座 2。
5. **短码盲注 all-in**:BB 玩家只剩 1(<BB=2)→ 投 1 即 `ALLIN`,`bet_amount=1`、`last_bet=2`。
6. **新玩家付盲即玩(默认)**:座 3 新入座、未设"等大盲" → 下一手投一个 BB 入局、`bet_amount=BB`(live)、`new_here=False`,立刻能玩。
7. **新玩家选等大盲(免费)**:座 3 设了"等大盲" → 本手不发牌;BB 推进到座 3 那手免费入局。
8. **换座躲盲被堵**:座 0 玩家(本手该 SB)换到座 4(BB 刚扫过)想立刻打 → 要么 post 一个 BB 才发牌、要么等大盲;拿不到免费好位。
9. **退房再进躲盲被堵**:轮到 BB 前 `LeaveRoom`、稍后 `JoinRoom`+`SitDown` → 想玩仍要 post 一个 BB(或等大盲);照样付了盲。
10. **坐出再坐回躲盲被堵**:轮到 BB 前 `SITTING_OUT`、过后回来 → 算"上一手没参与" → post 或等大盲。
11. **bootstrap**:空桌两人坐下 → 无已入局玩家 → 第一手**直接都发牌、只下常规盲注**(不收入局 post,不死锁)。
12. **全票免盲**:3 个已入局玩家全 `approve` → 新玩家进 `waive_entry_for` → 下一手免费正常入局(不 post、不等),`new_here=False`。
13. **一票否决**:任一投票人 `reject` → 投票失败,新玩家回到「付盲即玩 / 等大盲」。
14. **投票后蹭车被挡**:投票通过后才坐下的新玩家不在 `waive_entry_for` 快照里 → 仍走常规。
15. **投票人离场重算**:开票后某投票人 `LeaveRoom` → 投票人集合重算;若剩余投票人已全 `approve` → 通过。

---

# ② 行动规则与下注轮关闭判据

## 每个玩家的本街状态

- `bet_amount`:本街已投入(街结束并入 `contributed`、清零)。
- `has_acted`:本街是否已自愿行动过(街开始、被加注重开时置 False)。
- `status`:`ACTIVE`(可行动)/ `FOLDED` / `ALLIN`(不能再行动)。

## 三种动作(`PlayerAction`)

校验:有 `Hand`、`acting_position` 指向发起人、其 `status==ACTIVE`。

- **FOLD**:仅当 `bet_amount < last_bet`(有注要跟才允许弃;无注该 check)。置 `FOLDED`。
- **CHECK**:仅当 `bet_amount == last_bet`(无需跟注)。置 `has_acted=True`。
- **BET(amount)**:`amount` = **本街目标总额**(合并跟注/加注)。记 `stack = points + bet_amount`(本街可达上限):
  - `amount > stack` → 非法(`Err`)。
  - `amount == stack` → **all-in**:投到 `amount`,置 `ALLIN`。
  - `amount < stack` 且 `amount < last_bet` → 非法(不够跟又不 all-in)。
  - `amount == last_bet` → **跟注**:`has_acted=True`。
  - `amount > last_bet` → **加注**:见下 min-raise。
  - 从 `points` 补足到 `amount`,更新 `bet_amount=amount`。

## min-raise 与重开

- **自愿加注**(非 all-in)合法下限:`amount ≥ last_bet + max(last_raise_size, BB)`。否则 `Err`(加注不够)。
- **加注 / all-in 超过 `last_bet`** ⇒ **重开**:
  - `last_bet = amount`;`last_raise_size = amount - 旧 last_bet`(取 `max` 不缩小);
  - 加注者 `has_acted=True`;**其余所有 `ACTIVE` 玩家 `has_acted=False`**(必须回应)。
- **all-in 但 `amount ≤ last_bet`**(短 all-in,跟不满):`ALLIN`、`has_acted=True`、**不重开**、不改 `last_bet`。

> 简化决策 2:all-in `amount > last_bet` 即便不足一个完整 min-raise 也**重开**(允许再加注)。比赌场规则宽松,确定且公平。

## 下注轮关闭判据(核心)

```
def street_closed(hand) -> bool:
    can_act = [p for p in hand.players if p.status == ACTIVE]   # 未弃牌、未 all-in
    return all(p.has_acted and p.bet_amount == hand.last_bet for p in can_act)
```

- `can_act` 为空(剩下都 all-in / 只剩一人)→ 真空为真 ⇒ 关闭。
- **preflop BB 选择权天然落地**:开局 BB `has_acted=False`,所以即使人人跟平,`street_closed` 仍为假,直到 BB 自己 check/raise。

**推进**:某人行动后,若 `street_closed` → 结算本街(见③前置);否则 `acting_position` = 从当前位起下一个 `ACTIVE` 玩家,`epoch += 1`,产出 `TurnChanged`。

## 测试 ②

1. **preflop 全跟 + BB check 收街**:3 人,UTG 跟 2、SB 补到 2、BB `has_acted=False` 未关 → 轮到 BB,check → 关闭。
2. **preflop 全跟 + BB 加注重开**:同上到 BB,BB 加到 4 → 其余 `has_acted=False`、`last_bet=4`,继续。
3. **postflop 全 check**:`last_bet=0`,依次 check,最后一人 check 后全 `has_acted` 且 `bet_amount==0` → 关闭。
4. **下注+跟注收街**:postflop A 下 4(重开)、B 跟 4、C 跟 4 → 回到 A 时全 `has_acted` 且都=4 → 关闭(A 不再行动)。
5. **加注重开**:A 下 4、B 加到 10(重开,A/C 置 False)、C 跟 10、A 跟 10 → 关闭。
6. **min-raise 非法**:`last_bet=4`、`last_raise_size=2`,有人 BET 5(<4+2)且非 all-in → `Err`。
7. **短 all-in 不重开**:`last_bet=10`,A all-in 总额 7(<10)→ `ALLIN`、不改 `last_bet`、B/C 不被重开。
8. **all-in 超注重开**:`last_bet=10`,A all-in 14 → `last_bet=14`、B/C 重开,即便 14-10=4 不足完整加注也允许再加(决策 2)。
9. **heads-up preflop**:button/SB 先行动,跟到 2;BB 选择权 → BB check 关闭 / BB 加注继续。

---

# ③ 街道推进、摊牌与边池

## 街道结算与推进

街关闭后:

1. 各 `Player.bet_amount` **并入 `contributed[nick]`**、清零;`last_bet=0`、`last_raise_size=BB`、所有 `has_acted=False`。
2. 判分支:
   - **只剩 1 个未弃牌者** → **直接结束**(无摊牌、不亮牌),该玩家赢下全部 `contributed`(见「无摊牌结束」)。
   - **能行动者 `≤1`(其余 all-in)** → **跑完剩余公共牌**(flop/turn/river 一次发齐),直接进摊牌。
   - **否则** → 进 `next_status`:发该街公共牌(flop 3 / turn 1 / river 1),`acting_position` = postflop 首行动位(庄后第一个 `ACTIVE`),`epoch += 1`。
   - `RIVER` 关闭 → `SHOWDOWN`。

## 无摊牌结束(一人未弃)

- 不亮任何底牌;赢家收走 `Σ contributed`(含弃牌者投入)。
- 仍要走「未叫注退还」?——不用:若所有人弃给一个加注者,那笔加注里**超出他人跟注的部分本就是他自己投的**,边池算法(下)会把它作为未叫注退回给他;直接对全 `contributed` 跑边池算法,赢家是唯一 eligible,自然全收。**统一走边池算法即可**,无需特判金额。

## 摊牌(SHOWDOWN)

- 补齐未发公共牌;产出 `Broadcast(HandShowDown)`——**底牌唯一合法公开点**,显式带未弃牌者 `hole_cards`(见 [core.md](core.md) 不变量 3 / [wire.md](wire.md))。
- 牌力:treys `Evaluator.evaluate(board, hole)`,**分数越小越强**;在每个子池的 eligible 间比较。

## 边池分配算法(精确)

输入:`contributed[nick]`(每人本手总投入,含弃牌者)、未弃牌集合 `live`、各 `live` 的牌力、`button_position`。

**第 1 步 · 退还未叫注**:设最高投入者唯一且其投入 `h1` > 次高 `h2`,则 `(h1-h2)` 是没人跟的注 → 把它的 `contributed` 降到 `h2`,这笔差额的去向看该玩家是否在局:**未弃牌 → 退回该玩家**(进其 `points`);**已弃牌 → forfeit,不退本人**,作为「死钱」归并到最高 live 子池由仍在局者赢取(见下第 2 步)。(只有单个最高者可能有未叫注;弃牌的唯一最高投入者只出现在 [④](#-手牌进行中的离桌--坐出--断线) 离桌/清理 auto-fold 折掉高注者的情形——守「离桌不能把已投池中的注捞回」。)

**第 2 步 · 分层削池**:对(退还后的)`contributed`:

```
levels = sorted(set(c for c in contributed.values() if c > 0))   # 升序
prev = 0
for L in levels:
    per         = L - prev
    contributors = [p for p in all_players if contributed[p] >= L]   # 含弃牌者
    pot_amount   = per * len(contributors)
    eligible     = [p for p in contributors if p in live]            # 只有未弃牌能赢
    sub_pots.append((pot_amount, eligible))
    prev = L
```

**第 3 步 · 判池归属**:每个子池,`winners` = `eligible` 中牌力最强者(可并列):

- 均分 `pot_amount // len(winners)` 给每个赢家;
- **奇数零头**给最接近庄家左手的赢家:`winners` 按 `(seat_position - button_position) % seat_size` 升序,第一个拿零头;
- 奖金进 `Player.points`。

> **空 `eligible` 子池(本档投入者全弃)**:正常下注里每个 level 至少由一个未弃牌者撑起,但 [④](#-手牌进行中的离桌--坐出--断线) 的 auto-fold(离桌/清理可折掉本可不弃者)会让某档投入者全弃。分两种,都不再视为 bug:
> - **弃牌的唯一最高投入者的未叫注**(第 1 步已摘出的 `h1-h2`):forfeit,**归并到最高 live 子池**——在局者本就面对这笔注,该由其赢取。
> - **各弃牌者互相匹配的边池**(并列最高全弃、或在局者投入够不着的高档):无 live 资格者能赢、在局者也够不着 → **按本档退回各 contributor**,守住守恒(这是合理退化,非 bug)。

## 结算与筹码守恒

- 每个 `Player.points`(赢得的 + 本街剩余)还回 `Seat.points`,`Seat.in_game_points=0`。
- **守恒断言**(测试期开):`Σ 退还 + Σ 各子池 == Σ 开局 contributed`;`Σ 还回 Seat.points == 开局锁入总额`。

## 测试 ③

记 `S(p)`=玩家 p 牌力名次(1 最强)。

1. **单池**:A/B/C 各投 100,live={A,B,C},`S(B)=1` → B 独得 300。
2. **基本边池**:A all-in 50、B 投 100、C 投 100。levels=[50,100]。主池=150{A,B,C}、边池=100{B,C}。若 `S(A)=1` → A 得主池 150;边池由 B/C 比,`S(B)=1` → B 得 100。
3. **未叫注退还**:A all-in 100、B all-in 60、C 弃(投 0)。h1=100(A 唯一最高)>h2=60 → 退 40 给 A、A 降到 60。单层 60{A,B},pot=120 比 A/B;A 另得退还 40。
4. **弃牌者投入计入低池**:A all-in 100、B 投 20 后弃、C 跟 100。levels=[20,100]。L=20 池=60(含 B 的 20),eligible={A,C};L=100 池=160,eligible={A,C}。B 的 20 在池里但 B 不能赢。
5. **奇数零头**:主池 5,A、B 并列最强,button=座 0,A=座 1、B=座 3 → `(1-0)%N < (3-0)%N` → A 拿 3、B 拿 2。
6. **全 all-in 跑公共牌**:3 人全 all-in 不同额 → 不再行动,一次发齐 board,按 2/3 的分层判池。
7. **无摊牌**:A 加到 50 全弃 → 走边池算法,未叫注退还把 A 多投的退回、A 作为唯一 eligible 收主体,等价全收 `contributed`。
8. **守恒**:每个用例后断言 `Σ 子池 + Σ 退还 == Σ contributed`。

---

# ④ 手牌进行中的离桌 / 坐出 / 断线

手牌进行中,身份转移**要么即时安全、要么延到本手结束**——已投进底池的筹码在手未结束前不能抽走(否则破坏筹码守恒/公平)。

## 主动离桌(`LeaveRoom` 在局中)

- **立即自动弃牌**(auto-fold;即便能 check 也按弃处理,他要走)+ 标记"离桌中"。
- **到本手结束才结算驱逐**:已投入底池的筹码留在池里(forfeit),剩余 `Seat.points` 退回全局积分,然后 `del world.users[nick]`、释座、`Broadcast(UserLeft)`。期间不发新手牌给他。
- **为什么不能立刻拿钱走**:下注已进池,手未结束抽不出;剩余栈是他的,手尾退还。

## 中途坐出(`SITTING_OUT` 在局中)

- **延到本手结束生效**:本手照常打完(想立刻停就自己 `FOLD`)。手结束 `PLAYING → SITTING_OUT`,下手不发牌。
- 之后回来要打,按入局规则(付盲即玩 / 等大盲,见①)——因"上一手没参与"。

## 断线 vs 主动离开(都复用手尾结算)

| 触发 | 弃牌 | 留座 | 驱逐时机 |
|---|---|---|---|
| **断线**(OFFLINE) | 轮到他时**行动超时**自动 fold | 留座(等重连) | `LIVENESS_TIMEOUT` 到期 `Cleanup`(见 [timer.md](timer.md)) |
| **主动 `LeaveRoom`** | **立即** auto-fold | 不留 | 本手结束 |

两者共用"手尾结算 + 退筹释座"那套,区别只在触发与是否留座。

> **实现机制(0014,reduce)**:局中标记落 `room.leaving`(离桌/清理待手尾驱逐)与 `room.sitting_out_next`(坐出待手尾转 `SITTING_OUT`);手结束 `_finalize_hand` 结算后才 `_evict`(退座位剩余筹码回全局积分 → 释座 → 移出 → `del users` → `UserLeft`)。**离桌/清理的 auto-fold 一律 fold(即便能 check)**,与超时默认动作「能 check 则 check,否则 fold」不同。ALLIN 者不能再 fold(只标 leaving、仍可赢、手尾带奖金被驱逐);**非行动者离桌**只即时 fold、不推进 turn(仅当因此只剩一人未弃才结束本手)。

## 决策(可改)

- 中途 `LeaveRoom` 即时 auto-fold + 手尾驱逐(上面的方案);若想更狠"立即走、池里的算输",效果几乎一样,本方案更顺(统一走手尾结算)。
- 中途 `SITTING_OUT` 延到手尾;不做"打到一半立刻消失"。

## 测试 ④

1. **中途离桌**:flop 后 A `LeaveRoom` → 立即 `FOLDED` + 离桌中;A 已投入留池;手结束 A 剩余栈退全局 + 驱逐释座。
2. **离桌致单人剩余**:中途 A `LeaveRoom` auto-fold 后只剩 B 未弃 → 手立即结束,B 收池,A 手尾驱逐。
3. **中途坐出**:turn 时 A 请求 `SITTING_OUT` → 本手继续;手结束 A 转 `SITTING_OUT`,下手不发;A 再 ready 按入局规则。
4. **断线 vs 离桌对比**:A 断线 → 轮到才超时 fold、留座;A 主动 `LeaveRoom` → 立即 fold、不留座。

---

## 与 core.md 的关系

- 本文取代 [core.md](core.md)「一手牌的生命周期」里标 *待定* 的:dead blind(改为等大盲)、下注轮关闭判据、边池细节。core.md 的状态机骨架、事件产出、staleness/epoch 不变。
- `epoch` 在每次行动推进 / 街切换自增,供 timer staleness(见 [timer.md](timer.md))。
- 底牌/牌堆隐私、`Persist(HandRecordWrite)` 存结果不存底牌:照 core.md/[db.md](db.md)。

## 待定 / 变体

- **赌场式死盲精确记账(更严变体)**:本平台的 post 默认 live、且"缺一手就要 post/等大盲"(略严于赌场)。若要完全照赌场:按位置区分 live/dead、精确记"缺了哪个 SB/BB"、只补欠的那部分,用 `new_player_seat_list` 之类记账。边角多、收益小,**本规模不做**。
- **不完整 all-in 不重开(赌场规则)**:若要严格赌场行为,短 all-in 不重置已行动者的 `has_acted`,但要处理"已行动者可跟增量不可再加"——比决策 2 复杂,本规模不建议。
- **跑马多次发牌(run it twice)**:不做。
- **行动超时默认动作**:能 check 则 check,否则 fold——见 [timer.md](timer.md),逻辑在本文的动作校验之上。
