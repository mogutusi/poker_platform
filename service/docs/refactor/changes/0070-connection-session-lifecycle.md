# 0070 · 连接与会话生命周期修理(保活重设计 + 观战者断线即清 + 会话清扫 + exp 强制)

日期:2026-07-13 · 范围:`app/shell/timer.py`、`app/shell/receiver.py`、`app/shell/sender.py`、`app/shell/dispatch.py`、`app/shell/connection.py`、`app/shell/lifespan.py`、`app/core/reduce.py`(`_disconnect`)、`app/auth/session.py`、`app/gameconfig.py`(注释)、tests、docs(timer/connection/core/user/auth/coding_principle/TODO + **frontend/BACKEND_GUIDE.md**)。源自用户点名的整体架构审计(A1/A2/B4 三项;A3 房聊入 Room 另开 0071),方案经与用户两轮讨论定案。

## 问题(审计确认)

1. **A1 · 离线清理机制实际失效**:保活表「触发即删」+ 续命只靠收客户端帧 + 断线时不重新装表。而协议无客户端心跳(timer.md:122 当年要求"观战者必须周期 ping",从未落进协议/前端契约)——任何人静默 ≥ `LIVENESS_TIMEOUT` 后其保活条目已自燃删除,之后断线**永远无 Cleanup**:筹码锁桌、座位常占、房间因"有人"永不销毁,直至重启。
2. **A2 · 过期会话零清扫**:`SessionStore.prune` 无任何调用方;惰性删只在「再次 lookup 同一 sid」时触发,而静默轮换抛弃的旧会话永远不会再被查——过期会话(含 32B token + 派生信道)常驻内存,无界增长。
3. **B4 · exp 对活连接不生效**:auth.md 承诺「exp 到点拒该会话的报文」,实现只在 ws 握手与 REST 查表时检查;已建立的 ws 连接可带着过期密钥活到断线,架空「密钥寿命有上限」的设计目的。

## 方案(用户定案)

1. **保活重设计:断线装表、重连拆表**(替代原「每帧续命」;比"断线补一针心跳"的最小补丁更干净)——`_liveness` 语义变为字面的「**断线后占座窗口**」:
   - 装表:Receiver 退出清理(`was_current`)与 `dispatch._drop_connection`(慢客户端被踢)两处,即**凡投 `Disconnect` 处必装**;
   - 拆表:新连接接入时(重连/顶替落在窗口内 → 取消清理倒计时);reduce 的 OFFLINE staleness 校验仍是最后防线;
   - 删除每帧续命;在线用户不进保活表,无空触发。
   - **掉线检测交传输层**(这是本设计成立的前提,经查实):uvicorn+websockets 默认 20s 协议级 ping/20s 超时,死连接(拔电/NAT 失效)≤~40s 变成正常断线事件;客户端浏览器 WebSocket 自动回 pong,前端零实现。
2. **观战者断线即清出房间**(用户提议):`_disconnect` 见 `WATCHING` → 直接走 `_begin_leave`(观战者必不在手 → 即时 `_evict`,顶层空房归一随手销毁空房);在座各态(SITTING_IN/READY/SITTING_OUT/PLAYING)照旧标 OFFLINE 留窗。理由:观战者无座无筹码,重进零成本;OFFLINE 幽灵观战者会拖住房间销毁。**前端可见变化**:观战者掉线重连后不在房,需重新 `join_room`(写进 BACKEND_GUIDE)。
3. **A2**:`SessionStore.create()` 开头 `prune(now)`——清扫频率 = 登录频率,零新增接线。
4. **B4**:`Connection` 持 `session` 引用(dev 明文为 None);Receiver 收帧、Sender 发帧前各比对一次 `expires_at`,过期关连接(4401,与握手拒同码)。完全空闲的过期连接(双向零流量)存活到下次任一方向活动——记档接受(无流量即无泄露面)。
5. **流程纪律新增**(用户指示):**变动凡涉及前端需要知道的行为/契约,同一变更内必须同步 `frontend/BACKEND_GUIDE.md`**——落进 coding_principle.md「双向同步」与 TODO 持续项。

## 打算改什么

- timer.py:`heartbeat/drop_liveness` → `arm_cleanup/cancel_cleanup`(语义改名),注释重写;tick 不变(一次性触发在新语义下正确:条目只在离线期存在)。
- receiver.py:接入处 `cancel_cleanup`;删每帧续命;finally `was_current` → `arm_cleanup`;`_recv_frame` 加密臂加 exp 检查。
- sender.py:发送前 exp 检查(覆盖"纯收听"的过期连接)。
- dispatch.py:`_drop_connection` 补 `arm_cleanup`。
- connection.py:`session: Session | None` 字段 + `create` 透传;lifespan `/ws` 握手传 session。
- reduce.py:`_disconnect` 加 WATCHING 即清臂。
- session.py:`create` 先 `prune`。
- gameconfig.py:`LIVENESS_TIMEOUT` 注释改「断线占座窗口」。
- tests:timer 改名迁移 + 断线装/重连拆语义;core 观战者断线即清(含末人销房)/在座断线仍 OFFLINE;session prune-on-create;B4 收/发两侧过期关闭;A1 坏链回归(在线静默不触发、断线后必触发)。
- docs:timer.md(保活层驱动者/接口/流程/注意点①重写)、connection.md(断开语义分叉 + 装拆点)、core.md(Disconnect 行)、user.md(离场来源)、auth.md(exp 强制落地)、**frontend/BACKEND_GUIDE.md**(传输层自动心跳、观战者断线即清、exp 断连 4401)、coding_principle.md + TODO 持续项(前端文档同步纪律)。

## 实际改了什么(与「打算」对照)

与「打算改什么」一致落地:

- timer:`heartbeat/drop_liveness` → `arm_cleanup/cancel_cleanup`,`_liveness` 语义注释改「断线占座窗口(条目只在离线期存在)」;tick 一次性触发在新语义下正确。
- receiver:接入 `cancel_cleanup`;删每帧续命;finally `was_current` → `arm_cleanup` + `Disconnect`;`_recv_frame` 加密臂收帧后先比 `session.expires_at`,过期关 4401(在 MAC 之前——过期会话不值得验)。
- sender:取到消息后、封帧前同样比对,过期关 4401 后 return(覆盖「只收不发」的过期连接;双向零流量的过期连接存活到下次任一方向活动,记档接受)。
- dispatch:`_drop_connection` 补 `arm_cleanup`(凡投 Disconnect 处必 arm 的第二处)。
- connection:`Connection.session: Session | None` 字段 + `create` 透传;lifespan `/ws` 握手传 session(dev 明文 None 不查)。
- reduce:`_disconnect` 加 `WATCHING → _begin_leave` 臂(观战者必不在手 → 即时 `_evict` + 投票重算;末人离房由顶层空房归一销毁)。
- session:`create()` 先 `prune(now)`。
- gameconfig:`LIVENESS_TIMEOUT` 注释改断线窗语义。

测试:既有 3 处迁移(timer 两测改名并入触发即删断言、session prune 测避开 create 预扫)+ 新 `tests/shell/test_lifecycle_0070.py` 8 测(观战者断线即清·末人销房 / 有他人房保留 / 在座断线仍 OFFLINE+Cleanup 退筹回归 / **A1 主钉:在线静默极久零 Cleanup、断线后满窗必触发**(旧实现必红)/ B4 收帧过期 4401 / 出站过期 4401 / 未过期放行到 MAC(4400 证明走到 open))+ session `create` 预扫 1 测;692→**700** 全绿。

docs:timer.md(表格/驱动者/接口/流程/注意点①全按新语义重写 + 历史注)、connection.md(步 5/6 + 断开语义分观战/在座)、core.md(Disconnect 行)、architecture.md(断开行)、user.md(驱逐来源 + 观战者即时)、auth.md(exp 兜底扩到活连接)、**frontend/BACKEND_GUIDE.md**(§1 心智模型第 4 条改写 + 新增第 5 条「心跳不用你操心」+ §4.3 exp 强制/4401 语义)、coding_principle.md + TODO 持续项(**前端手册同步纪律入规**)。

## 自 review

对照 [review.md](../review.md) 逐维(本单元与用户逐条讨论定案,方案本身经过了两轮外部对抗;实现后自查如下):

- **① 分层 / 不变量**:reduce 只改工作副本(`_disconnect` 观战臂复用 `_begin_leave`,失败路径不存在——观战者臂无 Err);shell 不写 world(arm/cancel 是 Timer 私有表);exp 检查在 shell 收发边界,core 不知情;`connection.py` import `auth.session` 无环(session→channel,不回指 shell)。
- **② 代码↔文档**:timer.md/connection.md/core.md/architecture.md/user.md/auth.md 与实现逐条对齐;「凡投 Disconnect 处必 arm」两处(receiver finally / dispatch._drop_connection)都已写进文档;gameconfig 注释同步。
- **③ 文档↔文档**:timer.md ↔ connection.md ↔ BACKEND_GUIDE 的断线语义三处一致(观战者即清/在座 90s/无应用层心跳);config.md 的 LIVENESS 示例注释是通用示意,不与新语义冲突。
- **④ 数据模型**:`Connection.session` 与 `channel` 同源(都来自握手的 Session),dev 明文两者皆 None,不可表达「有 session 无 channel」的怪态(create 参数成对传)。
- **⑤ 规范**:无新裸字面量(4400/4401 是既有关闭码;窗口取 `gameconfig.LIVENESS_TIMEOUT`);注释讲为什么(为何删每帧续命/为何 sender 也查)。
- **⑥ 测试**:A1 主钉直接模拟旧坏链时序(静默极久→零触发→断线→必触发),旧实现在此必红;B4 三测覆盖收/发/未过期放行;观战者臂含末人销房与非末人保留;`receiver finally → arm` 的一行由既有 receiver e2e 测试保护路径、由 timer 单测保护语义(组合面记为覆盖空缺,风险低)。
- **⑦ 流程账本**:变更记录先行(计划已先行提交 e9e0b39);打算↔实际无偏差;新纪律(前端手册同步)已入 coding_principle + TODO 持续项并在本单元自身践行(BACKEND_GUIDE 三处更新)。

**对抗自问(crux)**:①「观战者即清」会不会误伤"闪断 3 秒的观战者"?——会让他重新 join_room,但客户端可自动重进(指南已写),换来的是房间名册即时真实、空房即销;与用户确认过的取舍。②「断线装表」漏装即回到 A1?——装表点 = 投 Disconnect 点,两处枚举齐且文档立了「凡投必 arm」规则;新增第三处 Disconnect 投递时此规则是显式检查项。③ exp 检查放 MAC 前是否给未认证者探测面?——检查只依赖本连接已绑定的会话对象,不解析帧内容,无新输入面。0 未处置发现。
