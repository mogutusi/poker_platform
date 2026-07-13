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

(收工回填)

## 自 review

(push 前回填)
