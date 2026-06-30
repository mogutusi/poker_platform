# 0043 · 房间参数配置:SetSmallBlind / SetBuyIn(0 号位占座者)

日期:2026-06-30 · 范围:`app/core/reduce.py`(`_set_small_blind`/`_set_buy_in` + 共用守卫 + match 臂 + `StateSnapshot.buy_in`)、`app/core/errors.py`(`INVALID_SMALL_BLIND`/`NOT_ROOM_OWNER`)、`app/gameconfig.py`(`MIN/MAX_SMALL_BLIND`、`MIN/MAX_BUY_IN`)、`app/poker.env.example`、`app/wire/server.py`(`RoomConfigChanged` + `StateSnapshot.buy_in`)、`app/wire/client.py`(`SetSmallBlind`/`SetBuyIn` 报文 + to_command)、`app/shell/receiver.py`(`_guard_room_config` 上下限防护)、`frontend/src/types/wire.gen.ts`(重生成)、测、文档。落地 [core.md](../../core.md) 命令表「0 号位配置房间参数」—— TODO P1 最后一项,gating 前置(0042 配置收编)已满足。

## 背景 / 为什么

`SetSmallBlind(amount)`/`SetBuyIn(amount)` 两个 Command **dataclass 自 0002 起就存在**(commands.py,注释「0 号位配置」),但一直是 stub:reduce 无臂(落 `case _` → INTERNAL)、无 wire 报文、无 gameconfig 上下限。历次(0014/0015/0022/0023/0042)显式推迟,理由是「合法区间要 `gameconfig.MIN/MAX_*`,且 core 不 import config」——0042 配置收编已落地、prerequisite 满足,本批补齐。

## 关键设计决策(批判性,docs 此前留白处当场定 + 回写)

`lobby.md` v1「房静态预置」+「动态建房 future」,但**改既有房参数 ≠ 动态建房**:SetSmallBlind/SetBuyIn 改的是单个已存在 Room 的字段(单房间 scoped,走 checkout/commit),与注册表级 `CreateRoom`(future)正交。docs 此前明确 WHO(core.md:48「0 号位」)+ big_blind 派生,但对 WHEN / 授权错误码 / bounds 放置留白——本批定 + 回写(lobby.md 新增「房间参数配置」节、core.md 命令表富化):

1. **授权 = 0 号位占座者**(`room.seats[0]` 占座者 == origin)。无持久 owner 字段,座位 0 即 de-facto 房主;非占座者 / 0 号位空 → 新码 `NOT_ROOM_OWNER`。**残留**:座位 0 占座者随起身/离场变动(房主身份流动);v1 接受,持久 owner 待 `CreateRoom` 引 creator。授权早于时机校验(同 `_buy_in`:座位校验先于 `HAND_IN_PROGRESS`)。

2. **时机 = 仅两手之间**(`room.status is not PENDING_START or room.hand is not None` → `HAND_IN_PROGRESS`,镜像 `_start_hand`)。改盲会污染已锁入本手的下注(`small_blind` 喂下盲 + 各处 big_blind 派生,StartHand 时锁定)。entry_vote 进行中**不**额外 gate:免盲投票只关「放谁免费进」与盲额无关,且开票本就在 PENDING_START。

3. **big_blind 派生不存储**(`blinds.BIG_BLIND_MULTIPLE * small_blind`)。只 SetSmallBlind 影响它;无 `SetBigBlind`、无 Room.big_blind 字段需同步。

4. **bounds 在 shell、不在 core**(硬规则 1 / changes/0015:core 不 import gameconfig)。`receiver._guard_room_config` 按 `gameconfig.MIN/MAX_*` 拒越界 → `INVALID_SMALL_BLIND`/`INVALID_BUY_IN`,**完全镜像 `_guard_room_chat`**(房聊文本长度/限速也在 shell guard、reduce 只认在房)。reduce 只兜结构(在房 / 占座 0 / 非局中 / `amount>0` 自保兜底)。新增 gameconfig 字段 `MIN/MAX_SMALL_BLIND`、`MIN/MAX_BUY_IN`(+ `poker.env.example`)。
   - **为何不放 Pydantic Field**:wire `Field(ge=…)` 越界 → `INVALID_MESSAGE`(协议错),非干净业务码;且把业务 bounds 烤进协议 schema、改 bound 要 regen TS。shell guard 回 `INVALID_SMALL_BLIND` 语义清晰(同房聊回 `MESSAGE_TOO_LONG`)。

5. **产 `Broadcast(RoomConfigChanged{small_blind,big_blind,buy_in})`,无 Persist**。携完整当前配置快照(非仅改动项)→ 客户端单条即对齐、含观战者(派发按 `users_in_room`)。房状态不落库(storage.md)→ 无 Persist、重启回 gameconfig 缺省;config 变更不碰积分 / 不触手牌(只读守恒)。

6. **`StateSnapshot` 补 `buy_in` 字段**(此前有 small_blind/big_blind 无 buy_in):否则 SetBuyIn 的效果重连后看不到。completeness 修——快照携完整房配。

## 实际改了什么

- **`app/core/errors.py`**:+`INVALID_SMALL_BLIND`、`NOT_ROOM_OWNER`;`INVALID_BUY_IN` 注释更新(≤0 由 reduce 兜 / 上下限 shell 防)。
- **`app/core/reduce.py`**:import `SetBuyIn`/`SetSmallBlind`/`RoomConfigChanged`;match +2 臂;`_room_config_guards`(共用:在房→占座 0→非局中)+ `_room_config_changed`(广播构造,big_blind 派生)+ `_set_small_blind`/`_set_buy_in`(守卫 → ≤0 兜底 → 原地改 `room.small_blind`/`room.buy_in` → 广播);`_state_snapshot` 两处构造补 `buy_in=room.buy_in`。
- **`app/gameconfig.py`** + **`poker.env.example`**:`MIN_SMALL_BLIND=1`/`MAX_SMALL_BLIND=100000`/`MIN_BUY_IN=1`/`MAX_BUY_IN=100000000`(`Field` 边界 + env 值)。
- **`app/wire/server.py`**:+`RoomConfigChanged`(注册 SERVER_MESSAGES);`StateSnapshot` +`buy_in`。
- **`app/wire/client.py`**:+`SetSmallBlind`/`SetBuyIn`(CLIENT_MESSAGES + union + to_command 臂;注释同 RoomChat:真实路径走 shell guard、to_command 留作通用映射 + 协议直测)。
- **`app/shell/receiver.py`**:import 命令;`_frame_to_command` +拦截 → `_guard_room_config`(按 gameconfig 上下限拒越界 + 构 Command 盖连接 nick)。
- **`frontend/src/types/wire.gen.ts`**:重生成(+`RoomConfigChanged`/`SetSmallBlind`/`SetBuyIn` 接口 + 联合成员 + `StateSnapshot.buy_in`/`RoomConfigChanged.buy_in`);`--check` 干净。
- **测**:`tests/core/test_room_config.py`(17:小盲/买入 happy + 派生 BB + 广播全房[含观战者]+ 非占座者/观战者/空 0 号位/非成员/局中/≤0 各拒臂 + 守恒[seats/hand/users 三快照]无 Persist + **自 review 补**:坐出 0 号位仍可配置 / 非占座者局中先吃 NOT_ROOM_OWNER 钉死守卫顺序 / 免盲投票进行中房配双向不互扰 / SetBuyIn 非成员对称 / StateSnapshot.buy_in 进房可见)、`tests/shell/test_room_config_guard.py`(8:小盲/买入 上/下界拒 + 闭区间端点放行 + 身份盖 nick);`tests/wire/test_protocol.py`(+RoomConfigChanged 广播样本 / +SetSmallBlind·SetBuyIn parse→to_command + 注册表样本 / StateSnapshot 补 buy_in)、`tests/test_gameconfig.py`(_valid_kwargs +4 字段 + 4 边界拒,含 MAX_BUY_IN)。**390→419**。
- **文档**:core.md(命令表)、lobby.md(新增「房间参数配置」节)、config.md(bounds 落地 0043)、wire-protocol-guide.md(client/server 目录 + state_snapshot 行 + 已交付)、TODO.md(勾项 + 残留)。

419 全绿;codegen `--check` 干净;core 无越层 import(grep `import.*gameconfig` 在 core 下无命中)。

> **外部改动(本批进行中)**:`gameconfig.py` 的 `MIN/MAX_SMALL_BLIND`、`MIN/MAX_BUY_IN` 四字段的 `le=` 上限被去掉(现仅 `ge=1`)——保留该改动(上限本就是任意值,运营自定;example 仍给 100000/1e8)。

## 自 review

方法:对照 [review.md](../../review.md) 跑**对抗式 5 维 review 子代理工作流**(core 正确性 · shell guard/分层 · wire/codegen/协议 · 文档同步 · 测试充分;每维独立审 → 24 候选逐条「默认反驳」二次核实)。**29 agent、24 候选、确认 8(0 真 code bug)**:rejected 16 条里两高风险维(core 纯度/validate-before-mutate/post-mutation 快照、wire lockstep/codegen/隐私)经 agent 实读代码 + 跑 `--check`/pytest 确认正确。确认 8 条全是**测试护栏缺口(7)+ 文档计数(1)**,无行为缺陷。逐维:

- **① 分层 / 不变量**:`reduce` 房配臂纯同步、core 不 import gameconfig(bounds 在 shell guard,grep 复验)、validate-before-mutate(守卫全在 mutation 前 return Err)、`RoomConfigChanged` 携 post-mutation 全快照、big_blind 派生不存储——agent 实读确认无缺陷(均落 rejected 的 "no defect")。授权早于时机(seat0 校验先于 HAND_IN_PROGRESS,镜像 _buy_in)。
- **② shell guard**:`_guard_room_config` 闭区间 `MIN<=x<=MAX`、错误码分流正确、身份盖连接 nick、不读 world、reject 仅 outbound 无副作用——镜像 `_guard_room_chat`,agent 确认无缺陷。「shell bounds 先于 reduce 授权 → 非 owner 越界先得 INVALID_*」记为已知取舍(bounds 是公开配置,非泄露)。
- **②③ 代码↔文档**:核对 core.md/lobby.md/config.md/wire-protocol-guide/TODO 与代码一致;**抓到 1 doc 计数错**(本记录 test_room_config 标 13、实 12)——补的 5 测后现为 17,已改本记录。另清两处 0042 起的 stale 前瞻(TODO reduce 状态行「房配待后续」/ wire-guide「还没有」列房配)。
- **③ wire/codegen**:CLIENT/SERVER_MESSAGES + union + to_command 三处 lockstep、`StateSnapshot.buy_in` + `RoomConfigChanged` 入 codegen、`--check` 干净、隐私无 hole_cards——agent 跑脚本确认,全 rejected(no defect)。
- **⑥ 测试**:补 5 高价值核测(见下)+ 1 gameconfig 边界,强化守恒断言(seats/hand/users 三快照)。419 全绿。

**对抗核实存活 / 采纳 / 驳回**:24 候选 → 确认 8、驳回 16。
- **采纳 6(护栏补全,无一改产品代码)**:① 免盲投票进行中房配双向不互扰(决策 2 load-bearing、零覆盖,且 sibling 臂都接了 `_maybe_resolve_entry_vote`——未来误抄即 bug,补测钉死);② 守卫顺序「授权早于时机」无测(非 owner 局中应得 NOT_ROOM_OWNER 非 HAND_IN_PROGRESS,补测杀 gate-swap 变异);③ `StateSnapshot.buy_in` 投影零测(补 SetBuyIn→JoinRoom 快照可见,钉死决策 6);④ 坐出 0 号位仍可配置(授权键于占座非状态,补测);⑤ SetBuyIn 非成员对称拒臂;⑥ 守恒测只快照 users、不覆盖 seats/hand(补三快照,对齐「不碰积分/不触手牌」注释);⑦ gameconfig `MAX_BUY_IN` 零边界覆盖(补 ge=1 拒 0)。
- **改判 1(stale)**:review 建议补 `le=` 上限拒测——但 `le=` 已被外部改动去掉(见上),故只补 `ge=1`,不补上限测(否则必失败)。
- **驳回 16**:14 条是 agent 对「纯度/lockstep/codegen/隐私/identity」的**正向确认**(no defect),2 条 nit 驳回(`room.seats[0]` IndexError 仅当未来建 0 座房——构造恒 ≥2 座;happy 测 `e.room=='r1'` 在无观战者时弱——已加观战者 W 强化)。

> 批判性自评:本批 **0 真 bug**,但 review 兑现「绿测 ≠ 可提交」——它定位到 4 个 load-bearing 设计决策(决策 2 投票交互 / 守卫顺序 / 决策 6 快照 / 授权键于占座)实现正确却**无回归护栏**,补的测经实跑(413→419)且针对性杀变异(gate-swap / 投票误接 / buy_in 漏投影)。房配 SetSmallBlind/SetBuyIn 至此功能闭环(core 授权+时机+派生 / shell 上下限 / wire 双向 / 快照对齐)。

## 待办 / 下一步

- 持久 room owner(`CreateRoom` 引 creator 时):取代「座位 0 = 房主」的流动身份。
- `app/config.py`(基础设施 `DATABASE_URL`/JWT,另一轨)——0042 起的余项。
- 房配跨字段一致(改小盲后 buy_in < 某倍 BB 是否拒):v1 不耦合(buy_in 仅客户端默认提示、core 不读),future 视需要。
