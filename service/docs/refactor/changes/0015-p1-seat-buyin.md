# 0015 · P1:就座与买入 —— `SitDown` / `BuyIn` / 起身(`SetUserStatus → WATCHING`)

日期:2026-06-18 · 范围:`service/app/core/reduce.py`(+`_sit_down`/`_buy_in` + `_set_user_status` 补起身 + 抽取 `_release_seat`)、`service/app/core/errors.py`(+`INVALID_BUY_IN`)、`service/app/core/messages.py`(+`PlayerBoughtIn`)、`service/tests/core/test_seat_buyin.py`(新)、文档同步(user.md / core.md / error.md / TODO.md)。

## 背景 / 打算改什么

0014 落地了「围绕一手牌的退出/离桌」生命周期;本篇落地**对称的「进入座位 + 注资」路径**——这是端到端可玩流(进房 → 入座 → 买入 → ready → 开局)里 reduce 还缺的一段(目前测试用 builder 直接拼就座/有筹码的桌)。同时**补上 0014 故意留的起身(`→WATCHING`)`INTERNAL` 占位**。

**本篇范围(就座与筹码,操作对象 = 已在房的用户)**:

- `SitDown(seat)`:`WATCHING → SITTING_IN`,占一个空座;新建 `Seat(points=0, new_here=True)`(rules.md ① :新就座者 new_here → 下一手付盲即玩/等大盲,防躲盲)。产 `Broadcast(UserStatusChanged)`。
- `BuyIn(seat, amount)`:全局积分 → 座位筹码(user.md「内存权威 + delayDB」)。校验:自己的座位、`amount > 0`、`amount ≤ 全局积分`、非局中(`PLAYING` 拒,手内不改栈)。`user.points -= amount`、`seat.points += amount`,产 `Persist(PointsWrite)` + `Broadcast(PlayerBoughtIn)`。
- 起身 `SetUserStatus(WATCHING)`:`SITTING_IN/SITTING_OUT/READY_TO_PLAY → WATCHING`(局中 `PLAYING` 不可起身,由既有 PLAYING 臂拒)。**腾座位 + 退座位筹码回全局积分**(复用 `_release_seat`)、`Persist(PointsWrite)`、产 `Broadcast(UserStatusChanged(WATCHING))`。

**本篇不做(留后续)**:`JoinRoom`(大厅→房,载入 `world.users`)+ `Connect`(重连)——都依赖尚未设计的 `StateSnapshot` wire 报文(connection.md「未写」);`RoomChat`;免盲投票(①.12-15)、等大盲再入局时机(①.7-10);`SetSmallBlind`/`SetBuyIn`(0 号位配置)——它们的合法区间要 `gameconfig.MIN/MAX_*`,随**配置收编**(P8)一起接,本篇不引入 core→config 依赖。

## 设计决策(开工前定的)

1. **买入上下限(`MIN/MAX_BUY_IN`)本篇不校验**:新 core 尚无 config 依赖、`service/app/gameconfig.py` 未建(P8 才收编)。本篇只校验**正确性不变量**(`amount > 0`、`≤ 全局积分`、自己的座位、非局中),与 [user.md](../../user.md) 的 `BuyIn` 示例一致(它也只校验座位 + 积分)。区间校验留 P8 接 `gameconfig` 时补 `if amount > gameconfig.MAX_BUY_IN`(不引入裸字面量——本篇压根不写区间,故不违硬规则 9)。`amount ≤ 0` 用新增 `ErrorCode.INVALID_BUY_IN`(error.md:码随业务补,权威在 errors.py)。
2. **起身(`→WATCHING`)退筹回全局积分 = 新增第三个全局积分变动出入口**:0014 之前 [user.md](../../user.md) 称「只在买入(扣)和离桌/清理(还)两处变动」。起身要腾座,座位筹码必须有去处;最一致的做法是**和离桌一样退回全局积分**(起身=离座但留房观战)。故把不变量改述为「**买入(扣);离桌/清理/起身——任何腾座(还)**」——仍窄、仍易审计(唯一借记是买入,所有贷记都是腾座)。已同步 user.md。
3. **抽取 `_release_seat(work, room, nick) -> Persist | None`**:退座位筹码回全局积分 + 释座 + 产 `PointsWrite`(无座位则 None);`_evict`(0014)与起身共用,消除重复。`_evict` 的 `UserLeft.seat_position` 仍自行 `_seat_of` 取(释座前)。
4. **`SitDown` 新建 `Seat(new_here=True, wait_for_big_blind=False)`**:默认「付盲即玩」(rules.md ①);「等大盲」是后续 wire 标志(①.7 落地时接)。每次重新就座都 `new_here=True` → 天然实现 rules.md ①.8/.10 的「换座/坐出再坐回算上一手没参与、要 post/等大盲」。
5. **载荷延续临时 dataclass**:`PlayerBoughtIn`(出站,挂 `ServerMessage`);`SitDown`/起身复用 `UserStatusChanged`(0014)。隐私:均无底牌/牌堆。

## 测试(core 单测)

`test_seat_buyin.py`:SitDown(观战→就座 + new_here + UserStatusChanged;占座/非观战/越界/不在房 错误臂)、BuyIn(全局→座位转账 + PointsWrite + PlayerBoughtIn + 守恒;非自座/积分不足/≤0/局中 错误臂)、起身(就座→观战 + 退筹回全局 + 释座 + PointsWrite + 守恒;局中 PLAYING 拒起身)。守恒 + 隐私断言默认开。

## 实际改了什么

- `app/core/reduce.py`:+`reduce` 两个 `match` 臂(`SitDown`/`BuyIn`);+`_sit_down`(观战→就座 + 新建 `Seat(new_here=True)` + `UserStatusChanged`)、`_buy_in`(校验自座/非局中/正额/余额够 → `user.points -= amt`、`seat.points += amt` → `Persist(PointsWrite)` + `Broadcast(PlayerBoughtIn)`);抽取 `_release_seat`(腾座退筹回全局 + 产 PointsWrite,无座位→None),`_evict` 改用之;`_set_user_status` 把起身(`→WATCHING`)从 0014 的 `INTERNAL` 占位改为实落(`_release_seat` + `UserStatusChanged(WATCHING)`),`_SELF_STATUS_TARGETS` +`WATCHING`。
- `app/core/errors.py`:+`INVALID_BUY_IN`。`app/core/messages.py`:+`PlayerBoughtIn`。
- 测试:`tests/core/test_seat_buyin.py`(新,14)。
- 文档同步:`user.md`(出入口不变量改述:买入借记 / 腾座——离桌·清理·起身贷记)、`core.md`(事件表「买入/离桌/起身」)、`error.md`(`INVALID_BUY_IN`)、`TODO.md`。

**偏离计划**:无。范围与开工「打算」一致(SitDown/BuyIn/起身;不含 JoinRoom/Connect/Set*Blind)。

## 自 review(push 前,money path + 分层重点核对)

方法:7 维 + core 红线,**手动复审 + 一个独立 reviewer agent 对抗核实**(默认先反驳,驳不倒才记)。**money path / 状态机 / 分层 / 文档同步四维均 CLEAN**——无 chip-conservation 或不变量 bug 经核实成立。要点:

1. **②④ money path(核实 CLEAN)**:`_buy_in` 是零和转账(`user.points -= amt; seat.points += amt`),所有 `Err` 在 mutation 前返回;且 `commit` 仅 `err is None` 时跑 → 即便「改后才 Err」也整份丢弃,**无「改了全局积分却返 Err」的漏改**。`_release_seat` 把 `seat.points` 一次性并入 `user.points`、清零、释座,无重复/丢失;`_evict` 改用 `_release_seat` 后与 0014 内联版**行为等价**(同样退筹 Persist 先于 `del`、`seat_idx` 在释座前取供 `UserLeft`)——0014 驱逐测试仍绿。锁入筹码(`in_game_points`)仅在局中非零,而起身被 PLAYING 臂挡,故 `_release_seat` 永远只退「未锁入的座位栈」。
2. **④ 状态机(核实 CLEAN)**:起身(`→WATCHING`)在 `current is PLAYING` 时被首臂拦下(仅许 SITTING_OUT 延迟)→ 局中不可起身离座、锁入筹码不被抽走;就座/起身均查 `USER_STATUS_(SELF_)TRANSITIONS`。`BuyIn` 的局中拒绝精确锁定 PLAYING 参与者(在座非参与者——别人手牌期间——允许买入,其座位栈未锁、安全)。
3. **①分层 / ⑤规范(CLEAN)**:core 无新 await/IO/shell/db import;helper 不 raise(`assert seat is not None` 仅兜不可能态);买入上下限本篇不写裸字面量(区间校验留 P8 接 gameconfig),不违硬规则 9。
4. **③ 文档同步**:user.md 出入口不变量已含起身(第三贷记出入口);change record 签名与代码一致。
5. **⑥ 测试**:守恒断言两端齐(买入 seat+global==100;起身 20→100 释座);reviewer 指出的小缺口已补——负额买入(`-10`,防凭空加分)、买入座位越界。**隐私维度本篇 N/A**(就座/买入/起身不经手牌、无底牌流经)。

**core 红线复核**:纯同步(grep 无 shell/db/asyncio import)、失败 `return [], Err`(`INVALID_BUY_IN`/`INSUFFICIENT_POINTS`/`NOT_YOUR_SEAT`/`SEAT_TAKEN`/`HAND_IN_PROGRESS`/`INVALID_STATUS_TRANSITION`)、转账守恒(测试断言)、隐私 N/A。

## 测试

`.venv/bin/pytest tests/ -q` → **144 passed**(0014 的 130 + 本篇 14)。覆盖:SitDown(观战→就座 + new_here/付盲即玩默认 + UserStatusChanged;占座/非观战/越界/不在房 错误臂)、BuyIn(全局→座位转账 + PointsWrite(按 uid)+ PlayerBoughtIn + 守恒;叠加已有栈;非自座/越界/积分不足/0 额/负额/局中 错误臂)、起身(就座→观战 + 退筹回全局 + 释座 + PointsWrite + UserStatusChanged(WATCHING/None)+ 仍在 world.users;局中 PLAYING 拒起身)。

## 待办 / 下一步

- `JoinRoom` + `Connect` + `StateSnapshot`(进房载入 + 重连 + 整桌快照报文设计)。
- `SetSmallBlind`/`SetBuyIn` + 买入上下限:随配置收编(P8)接 `gameconfig`。
- 免盲投票(①.12-15)、等大盲再入局时机(①.7-10)、`RoomChat`。
