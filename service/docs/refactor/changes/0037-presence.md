# 0037 · presence:在线状态只读聚合 + ConnectionManager.rename

日期:2026-06-24 · 范围:`app/shell/presence.py`(新:`Presence`)、`app/shell/connection.py`(+`online_nicks` / `rename`)、`app/shell/lifespan.py`(DevShell 装配 `presence`)、`tests/shell/test_presence.py`(新)+ `tests/shell/test_connection.py`(rename/online_nicks)、文档(`presence.md`/`TODO`)。落地 [presence.md](../../presence.md)「只读聚合」+「改昵称连接重挂」。

## 背景 / 为什么

[presence.md](../../presence.md) 定:presence 是**只读的「谁在线 / 在哪房 / 什么状态」视图**,给 lobby(人数)、messaging(私聊在线判断)、rest(改昵称在房判定)、好友共用——**不是新权威态,是对 ConnectionManager(在线)+ committed world(房/状态)两处的只读聚合**。当前未落地:这些读法散在各处、改昵称缺连接重挂。本批把它收口成统一 API + 补 `rename`。

> **定位:这是 P7 的读基座(有据前瞻依赖,非紧邻落地)**。消费者(lobby 房列表 / DM 在线判断 / 改昵称 REST)随 P7 落;其中 **REST 类消费者还卡在 no-node / openapi-typescript 工具缺口**(见 [TODO](../TODO.md) / [wire.md](../../wire.md))。本批先把被多方依赖的只读原语 + 连接重挂落定并**测固**(无 IO、不会崩、纯 `__init__`),使消费者直接复用——这是 [0027](0027-prototype-teardown.md) 式「有据前瞻」而非投机死代码(presence.md 即如此设计)。

## 关键设计决策(对齐 presence.md)

1. **`Presence(world, conns)` 持引用、方法只读聚合**(presence.md 把零散读法「收口成统一只读 API」;示意是自由函数,实现收成一个类持 world+conns)。`is_online`(ConnectionManager 有连接)/ `current_room`(`world.users[nick].room`,纯大厅 None)/ `room_headcount`(`len(users_in_room)`,房不存在 0)/ `online_nicks`(ConnectionManager 全表)。
2. **读 committed world 安全 + 见最新**:`commit`(`shell/world.py`)**原地改 World 对象**(`world.users = work.users`、`world.rooms[name] = work.room`),World 对象稳定 ⇒ `Presence` 每次读 `self._world.users/.rooms` 得**最新提交态**;单线程下读是原子引用换(presence.md:18「要么旧要么新、不撕裂」)。**绝不写 world、绝不实时游戏裁定**(实时裁定在 reduce)——只展示 / 软守门、容忍滞后一拍。
3. **`ConnectionManager.rename(old, new)`**(presence.md 改昵称):把连接从 `old` 键重挂到 `new` 键 + 改 `Connection.nick`,否则私聊/路由按新 nick 找不到旧连接;无 `old` 连接则 no-op(未连接时改名只改库)。改昵称的「仅大厅判定 + 改 DB/会话表」归 REST handler(rest.md,待 P7 REST),本批只补连接层 `rename` 原语。
4. **DevShell 装配 `self.presence`**:shell 组装完整(per presence.md 架构),`app.state.shell.presence` 可达,供后续 REST/DM 消费。本批无生产读者(消费者随 lobby/DM/rename 落地);为已测的读基座,非死代码(架构明定 + 测穷举 + 紧邻消费)。

## 打算改什么(开工前)

- `app/shell/connection.py`:`ConnectionManager` +`online_nicks() -> set[str]` +`rename(old, new)`。
- `app/shell/presence.py`(新):`Presence(world, conns)` + 四只读方法。
- `app/shell/lifespan.py`:`DevShell.setup` 建 `self.presence = Presence(self.world, self.conns)`。
- `tests/shell/test_presence.py`(新):在线/在房/人数/全体 + presence 见提交后变化(world-ref 语义)。
- `tests/shell/test_connection.py`:rename 重挂 + 无 old no-op;online_nicks。
- 文档:`presence.md`(标落地 + Presence 类)、`TODO`(P7 presence 划掉)。

## 实际改了什么

- **`app/shell/presence.py`(新)**:`Presence(world, conns)` + `is_online`/`current_room`/`room_headcount`/`online_nicks`(纯只读;持稳定 world 引用,每次读最新提交态)。
- **`app/shell/connection.py`**:`ConnectionManager` +`online_nicks() -> set[str]`(全表)+`rename(old, new)`(重挂键 + 改 `Connection.nick`;无 old → no-op)。
- **`app/shell/lifespan.py`**:`DevShell.setup` 建 `self.presence = Presence(self.world, self.conns)`(`app.state.shell.presence` 可达,供后续消费)。
- **`tests/shell/test_presence.py`(新,6 测)**:在线⊥在房 / 大厅 vs 在房 current_room / 人数 + 未知房 0 / online_nicks / **presence 见提交后变化(world-ref 语义:join→leave 实时反映)** / 只读不改 world(深比较)。
- **`tests/shell/test_connection.py`(+3)**:online_nicks 集合 / rename 重挂(旧键移、新键挂、Connection.nick 改)/ rename 无 old no-op。
- **文档**:`presence.md`(只读 API 收口成 `Presence` 类 + `rename` 标落地)、`TODO`(presence 划掉)。

328 全绿;core 无 presence import(只读聚合在 shell)。

## 自 review

方法:对照 [review.md](../../review.md) 跑对抗式 4 维 review **子代理工作流**(world-ref 正确性 / rename 正确性 / 死代码-分层 / 测试-文档)。结论 **go,0 must-fix**:16 候选全驳到 nit/minor,三大高风险面经实查均 sound。逐维:

- **① 分层 / 不变量**:**核心红线·world-ref + 不变量 2**——实查 `commit`(`world.py:47/53`)**原地改 World 对象**(`world.users=`/`world.rooms[..]=`),`Presence` 持稳定引用每次读得最新提交态(`test_presence_reflects_committed_world_changes` join→leave 钉死)。**不撕裂**:唯一写者 `GameLoop.handle` 是纯同步 `def`、两行赋值间**零 await**(`gameloop.py` 已记「handle 全程无 await」),单线程下无协程可切入——已把此 WHY 补进 `presence.py` 注释。`Presence` 纯只读(`test_presence_does_not_mutate_world` 深比较)、shell-only(import core.domain `World` + shell `ConnectionManager`,coding_principle 硬规则 1 只约束 core 不 import shell,反向允许)。
- **② 代码↔文档**:presence.md 自由函数示意收口成 `Presence` 类(签名/walrus 与实现一致)、`rename` 标落地;改昵称完整流(仅大厅判定 + 改 DB/会话表)明确归 P7 REST。
- **③ 文档↔文档**:presence.md / TODO / 本记录口径一致;**修订本记录「紧邻消费」为「有据前瞻」**——REST 类消费者卡在 no-node/openapi-typescript 工具缺口(诚实标注,非投机死代码)。
- **④ 数据模型**:`Presence` 持引用不持快照(语义正确);`online_nicks` 返回 `set(...)` 新拷贝(`test_online_nicks_returns_fresh_copy` 钉死)。
- **⑤ 规范**:注释讲「为什么」(world-ref/不撕裂/rename 前提);`rename` 注释补**前提**(调用方先过 DB nickname 唯一约束 + 仅大厅);无裸字面量/死 print。
- **⑥ 测试**:在线⊥在房(补 **online-in-lobby** 正交格:有连接 + 不在 world.users → is_online True & current_room None)/ 大厅 vs 在房 / 人数+未知房 0 / online_nicks + **拷贝不可变** / presence 见提交后变化 / 只读不改 world / rename 重挂+no-op。330 全绿。
- **⑦ 账本**:打算↔实际一致 + 采纳 4 条 nit(torn-read WHY 注释、online-in-lobby 测、online_nicks 拷贝测、rename 前提注释 + 诚实前瞻框定);TODO 划项;提交引用 0037、全英文。

**对抗核实存活 / 采纳 / 驳回**:functional 候选**全部驳到 nit/minor**(0 blocker/major)。*采纳(均已修)*:① torn-read「为什么不撕裂」补进 presence.py 注释 + 本记录;② 补 online-in-lobby 正交测 + 修「纯大厅」措辞;③ 补 online_nicks 拷贝不可变测 + 注释;④ rename 前提注释;⑤ 本记录「前瞻」框定诚实化(REST 卡工具缺口)。*驳回 / 推迟*:`rename` 无碰撞守护——**推迟 P7 消费者 PR**(当前零生产调用 + DB 唯一约束 + 仅大厅上游兜,非 day-1 blocker);「presence 死代码 major」——降为 minor(有据前瞻、已测、无 IO 不会崩,0027 式 sanctioned);「presence 重复 dispatch 广播逻辑」——驳回(dispatch 在 GameLoop 内读 world 路由广播,presence 给外部只读消费者,无共享域逻辑)。

> 批判性自评:本批是「**消费者未到、先落被多方依赖的只读基座**」——最该自省的是「会不会成投机死代码」。结论否:架构(presence.md)明定、本批纯只读无 IO/不会崩、测穷举、且诚实标注 REST 消费者卡工具缺口(非「下周就用」)。最高风险面 world-ref 不撕裂,review 实跑确认锚在「唯一写者 handle 无 await」这条既有不变量,非新假设。

## 待办 / 下一步

- 消费者:lobby 房列表(`room_headcount`)、私聊 DM 在线判断(`is_online`)、改昵称 REST(`current_room` 守门 + `rename`)。
- 变化推送(上下线/进出房广播给大厅/好友)v1 用轮询,future(presence.md §待定)。
