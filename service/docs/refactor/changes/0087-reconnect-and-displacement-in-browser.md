# 0087 · 断线重连与顶替:在真浏览器里验(0079·B)

日期:2026-08-24 · 性质:**验证先行**(先补浏览器验证,再修它抓到的东西)· 触发:[TODO.md](../TODO.md)「前端」0079·B,以及 0078·D 余下的「重连后 seq 继续累加」。

## 为什么是这一项

[0083](0083-shell-lifecycle-hardening.md) 把顶替(`_displace` 后复查 `is_current`)、慢客户端丢连(cancel Sender + Receiver)、退出清理这三条路径大改了一遍,**而这些改动至今只有后端测试盖着**。后端测试用的是 `FakeWS`,它连「关一条 socket 需要时间」都要专门造一个 `_SlowCloseWS` 才模拟得出来(0083 §自 review ⑥ 自己记的)。0079/0080/0082/0084/0085 的经验一致:**这一层每次都能抓到前两层看不见的东西**。

要在浏览器里验的三件(TODO 原文):

1. **重连后 seq 继续累加** —— 从 0 重来会被服务器判 `stale_seq`(`FrameError`)→ 关连接 4400 → 重连 → 再被拒,陷入死循环。这是 [transport.md](../../../../frontend/docs/transport.md) §三点名「最容易踩的坑」,但至今只有 vitest 的单元断言,没有真断线过。
2. **快照重新对齐** —— 断线重连后服务器私发 `Personal(StateSnapshot)`,前端整份替换;座位、筹码、手牌进度应当回到断线前。
3. **被顶替的处理** —— 同账号在别处登录会顶掉当前连接。TODO 写的是「(4401)」,但**这条要先核实**:`_displace` 走的是 `_close_quietly(old.ws)` → `await ws.close()`,**不带 code**,即 1000。若真是 1000,前端 `ws.ts` 只对 4401 停止重连,别的一律退避重连 —— 那就是「被顶替 → 自动重连去抢 → 把对方顶掉 → 对方再抢」的乒乓,正是 [transport.md](../../../../frontend/docs/transport.md) §四明写「不要自动重连去抢」的反面。**先让验证说话,不先下结论。**

## 开工前已经看出的一处可疑(待浏览器证实/证伪)

`store/actions.ts` 的 `enterRoom` 在**每次** `onStateChange('open')`(含重连)都发一条 `join_room`,并把 `recovered` 重置为 false;而重连时用户**本来就在这个房间里**,服务器必回 `ALREADY_IN_ROOM`。`decideJoinMessage` 见到 `ALREADY_IN_ROOM` 一律判「先退再进」(`leave_room` + `join_room`)——那是为 [0078·A](0078-frontend-table-wiring.md) 的「上一次会话残留在**别的**房间」写的。若这条在同房重连时也触发,后果是**每次重连都把自己从座位上退下来**,桌上筹码退回全局积分、座位释放。

这条只是纸面推理,**以浏览器实测为准**。

## 打算怎么做

照 [0085](0085-raise-and-sidepot-verification.md) 的顺序:**先写验证,让它自己说话**,再修它抓到的东西,每处动行为前先读该行为的设计文档。

1. **新增 `e2e/reconnect.spec.ts`**,两个用例:
   - **① 断线重连**:两人同桌 → 其中一人 `context.setOffline(true)` 断网 → 断线横幅出现 → `setOffline(false)` → 断言:重新连上、**座位与筹码还在**(快照对齐)、**断线后发出的命令服务器仍然接受**(seq 没有回退)、**全程没有任何一条连接被 4400 关掉**(seq 回退的直接症状)、ws 连接总数是可数的小数目(没有重连风暴)。
   - **② 被顶替**:同一账号在第二个 context 登录 → 断言第一个 context **不再无限重连去抢**,且第二个 context 能稳定用下去。
2. **观测手段**:用 `context.addInitScript` 包一层 `window.WebSocket`,把每条连接的 url / close code / 生命周期记进 `window.__wsLog`。这是**测试侧**的仪器,不往生产代码里塞测试钩子;它读的是浏览器自己的 API,能拿到 Playwright 的 `page.on('websocket')` 拿不到的**关闭码**。
3. **修验证抓到的东西**。可能涉及:`store/joinFlow.ts` / `store/actions.ts`(同房重连别误判成残留)、`shell/receiver.py` 的顶替关闭码 + `transport/ws.ts` 的关闭码处置。**凡改到前端可见的连接语义(关闭码),同一次改动同步 [BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md) / [connection.md](../../connection.md) / [transport.md](../../../../frontend/docs/transport.md)**(0070 起的用户指示)。
4. **每条新测都做反向变异验证**:把修法改回旧行为,对应用例必须变红。

## 要动的文件(预期)

- 新增 `frontend/e2e/reconnect.spec.ts`
- 视实测结果:`frontend/src/store/joinFlow.ts`、`frontend/src/store/actions.ts`、`frontend/src/transport/ws.ts`、`service/app/shell/receiver.py`
- 文档:[TODO.md](../TODO.md)(勾项)、[connection.md](../../connection.md)、[frontend/docs/transport.md](../../../../frontend/docs/transport.md)、[frontend/BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md)、[frontend/docs/dev.md](../../../../frontend/docs/dev.md)

## 实际改了什么

验证先行又一次奏效:**要验的三件里,两件本来就是对的;真正的收获是验证过程抓出的四个缺陷 + 一个假绿的用例。**

先说被验的三件:

| 要验的 | 结论 |
|---|---|
| ① 重连后 seq 继续累加 | **本来就对**。`SecureChannel` 挂在会话上、前端 `session.ts` 的计数器也只随登录归零,浏览器里真断一次线再重连,后续命令服务器照收,全程没有一条连接被 `4400` 关掉 |
| ② 快照重新对齐 | 服务端**本来就对**(快照带座位、底池、自己的底牌);但客户端拿到之后立刻又把自己作没了 —— 见缺陷一 |
| ③ 被顶替的处理 | **不对,而且 TODO 里写的前提也不对**:顶替走的是不带 code 的 `ws.close()`(1000),不是 4401。客户端分不出「掉线」与「被顶」,于是照常重连去抢 |

### 缺陷一(重):每次断线重连都把自己从座位上退下来

- **现象**:浏览器里断一次线,重连之后页面显示「观战中」——人从座位上下来了,桌上筹码退回全局积分,手也不在了。
- **机理**:`enterRoom` 每次 `open`(含重连)都发一条 `join_room`;重连时用户**本来就在这个房间**,服务器必回 `ALREADY_IN_ROOM`。而 `decideJoinMessage` 见到这个码一律判「先退再进」——那条规则是 [0078·A](0078-frontend-table-wiring.md) 为「上一次会话残留在**别的**房间」写的,同房重连撞上它纯属误伤。
- **协议层取证**(`smoke-client` 探针):重连后服务器依次发 `user_status_changed` → `state_snapshot(本房)` → `error ALREADY_IN_ROOM`,顺序稳定(Receiver 在进收帧循环**之前**就投了 `Connect`,而单连接严格保序)。
- **修法**:`decideJoinMessage` 多收一个 `snapshotRoom`(本条连接上已收下的快照说我在哪个房)。快照说我已在目标房 ⇒ 这条 `ALREADY_IN_ROOM` 是**预料之中的回答**,咽掉;快照说我在别的房、或压根没快照 ⇒ 仍按残留处理。`enterRoom` 每次 `open` 把它连同 `recovered` 一起归零(新连接不继承旧连接的判断)。
- **那条多余的 `join_room` 为什么不干脆不发**:留着是**故意**的。断线久过占座窗口(`LIVENESS_TIMEOUT`=90s)的话,服务器早把人清出房间了,这条 `join_room` 正是自愈的那一下;不发就会卡在一张再也不会更新的桌子上。代价只是一条错误帧的往返。

### 缺陷二:开局底池显示 0

- **现象**:每手 preflop 桌上写着「底池 0」,而同一时刻重连拿到的快照写着 3。
- **机理**:`HandStarted` 不带 `pot`,前端于是硬写 `pot: 0`;而服务端 `_pot` 的口径是 `contributed + 各家本街 bet_amount`,盲注一下就是 3。
- **修法**:`HandStarted` 加 `pot`(必填、无默认 ⇒ 漏填是编译期错误),前端照抄。

### 缺陷三(重):整轮 preflop 的「跟注」都发成 `bet(0)`,必被拒

- **现象**:heads-up 开局后点 Call 毫无反应;界面上 Call 按钮写着「Call 0」。
- **机理**:`_start_hand` 在 `HandStarted` 之后紧跟一条 `HandStatusChanged(status=PRE_FLOP)`,而前端把**每一条** `hand_status_changed` 都当成「换街了」,于是 `lastBet` 归 0、各家 `bet_amount` 归 0 —— 把刚下的盲注抹了。之后 `handleAction('call')` 发的是 `bet(state.lastBet)` = `bet(0)`,服务器回「目标额 0 不足跟注 2 且非 all-in」。
- **这是 [0084](0084-new-here-channel.md) / [BUG-19](../BUGS.md) 同一类病的第三例**:客户端需要的一个事实没有传达渠道,于是它自己推,而推断在边界上是错的。「换街了所以清零」对 flop/turn/river 成立,对**开局那条**恰好不成立。
- **修法**:`HandStatusChanged` 加 `last_bet` 与本街起点的 `players[]`,前端照抄不再推。两处产出点各自如实填(开局带盲注;`_close_street` 里 `settle_street` 已清零,带的就是新街零起点)。顺带把三处重复的 `PlayerView` 投影收成 `_player_views(hand)`。

### 缺陷四(重):被顶替之后两边无限互顶

- **现象**:同一账号在第二个浏览器里登录,两条连接开始乒乓——6 秒内新连接被关了 6 次。
- **机理**:`_displace` 调 `_close_quietly(old.ws)`,`ws.close()` **不带 code**(1000)。前端只对 4401 停止重连,别的一律退避重连;它一重连就把刚上位的那条顶掉,对方再重连……
- **为什么这不是「顶替语义要求静默」的应有之义**:[connection.md](../../connection.md) 说的「静默」是对 `world` 与房内其他人静默(不投 `Disconnect`、不广播),不是对**被顶的那个客户端**也不给交代。它需要知道「你被接管了,别抢」。
- **修法**:新增关闭码 **4409**(私有区,语义对齐 HTTP 409 Conflict),`_displace` 带上;顺带把散在三处的 4400/4401 裸字面量收成 `shell/connection.py` 的 `WS_CLOSE_*` 具名常量。前端 `ws.ts` 对 4409 停止重连并回 `onAuthLost('displaced')`,页面带 `?reason=` 跳回登录页,登录页把原因说成人话。
  - 顺带修正一处名不副实:`ws.ts` 原本把 4401 报成 `'displaced'`,而 4401 是会话失效。现在 4401→`'expired'`、4409→`'displaced'`。

### 顺带发现:`e2e/showdown.spec.ts` 是假绿的

写自己的推进循环时发现的:各 spec 的循环都「先点 Check,不行再点 Call」,而**按钮不按规则灰**(合法与否由服务器裁定,前端不变量 1),于是 heads-up preflop 小盲那一下 Check 必被拒;循环点完不等表态就往下走,从头到尾原地空转,最后靠 `ACTION_TIMEOUT`(15 秒)替人默认弃牌收场——「In game 消失」照样成立,用例照样绿。**它自称验过的三条街和摊牌,一次都没走到**(实测:8 轮点击,底池恒为 3,公共牌恒为 0 张)。

修法两条:

- 推进逻辑收进 [`e2e/helpers.ts`](../../../../frontend/e2e/helpers.ts) 一份,`actAndWait` **等服务器表态**(桌面指纹动了 = 接受 / 弹出错误 = 拒绝),两个动作都被拒就直接抛;
- `showdown.spec.ts` 的断言换成**只有真走到河牌才可能成立**的:正面朝上的牌 = 自己 2 张 + 公共 5 张,再加结算面板弹出。改完耗时从 19.3s 掉到 5.2s —— 那 15 秒本来就是在等超时。

> 指纹里不能用 locator 的 `isEnabled()` / `textContent()`:它们会**自动等元素出现**,而手牌一结束行动栏和底池就从 DOM 上消失,于是它们不是「返回没有」而是一直等到超时。改成一次 `page.evaluate` 取完。

### 新增的浏览器用例(`e2e/reconnect.spec.ts`)

- **掉线再重连**:两人同桌开局 → 掐掉 A 的 socket → 断言重连、还在座、底池不变、**底牌是原来那两张**(按图片名比对)、重连后 A 发出的加注 B 那边看得见、**全程没有一条连接被 4400 关掉**(seq 回退的直接症状)、没有重连风暴。
- **同账号别处登录**:老连接在牌桌上 → 新 context 用同账号登录 → 断言老连接被关、**新连接一次都没被抢回去**、老连接不再反复重连、界面告诉用户「账号在别处登录」。

> **断线怎么造**:`context.setOffline(true)` **不管用** —— 实测(chromium)它只挡新请求,已建立的 WebSocket 照常活着,断线横幅根本不出现。改成用 `addInitScript` 包一层 `window.WebSocket`(顺带记下每条连接的**关闭码**,`page.on('websocket')` 给不了),再从页面里把那条 socket `close()` 掉。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **746 passed**(745 → 746) |
| 前端 vitest | **86 passed**(82 → 86) |
| 浏览器 `npm run test:e2e` | **15 passed**(13 → 15) |
| `npm run smoke` / `smoke:raise` / `smoke:stale` | 全部通过(守恒 2020 → 2020;三人 3000) |
| 后端改完重启 uvicorn 再跑前端各层 | 是(本仓纪律) |

**踩到一个值得记下的坑**:重启 uvicorn 时端口还没让出来,新进程打一行 `[Errno 98] address already in use`
就退了,**旧进程继续服务**——curl 照样 200,于是整套浏览器用例在悄悄测老代码,只有那条正好验新行为的
用例红、其余 14 条全绿,看起来像「新用例不稳定」。已写进 [dev.md](../../dev.md):重启后要确认进程真的换了。

**反向变异验证 6 处**,每处都确认「改回旧行为 → 对应测试变红」:

| 变异 | 变红的 |
|---|---|
| `decideJoinMessage` 去掉快照判据 | vitest 1 条 + 浏览器「掉线再重连」(停在「观战中」) |
| 前端 `hand_started` 硬写 `pot: 0` | vitest 1 条 + 浏览器(底池期望 0 实得 3) |
| 前端 `hand_status_changed` 退回本地清零 | vitest 1 条 + 浏览器「打到摊牌」,报的正是**旧缺陷的原话**:`目标额 0 不足跟注 2 且非 all-in` |
| `_start_hand` 的 `pot=_pot(hand)` 硬写 0 | core 1 条 |
| 开局 `HandStatusChanged` 的 `last_bet`/`players` 硬写空 | core 1 条 |
| `_displace` 退回不带关闭码 | shell 1 条 + 浏览器「别处登录」(新连接 6 秒被抢 6 次) |

## 自 review

按 [review.md](../../review.md) 七维。本批横跨 core / wire / shell / 前端四层,最高风险面是**协议加字段的完备性**(漏填 = 客户端静默拿到错值)与**连接语义**(关闭码改动直接影响前端重连行为)。

- **① 分层 / 不变量**:core 仍纯同步、零 IO;新字段都在 reduce 里投影产出,没有绕过事件机制。`_player_views` 是 core 内的纯读 helper,不 raise。**前端不变量 1 在本批是被修复的一方**——「开局底池是 0」「换街了所以清零」「`ALREADY_IN_ROOM` 一定是残留」都是客户端在替服务器裁定,三处都换成了照抄服务器。新增的 `WS_CLOSE_*` 是 shell 层常量,core 不认识它们。
- **② 代码↔文档同步**:改到的每处前端可见面都同步了——[BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md)(新增关闭码三行表 + 开局下注态那条「别自己推」)、[wire-protocol-guide.md](../../wire-protocol-guide.md)(消息目录 + §5 时序图两处报文形状)、[connection.md](../../connection.md)(顶替必须带 4409,并写明「静默」是对谁静默)、[core.md](../../core.md)(事件载荷:同 `new_here` 的理由适用于下注态)、[transport.md](../../../../frontend/docs/transport.md) §四、[state.md](../../../../frontend/docs/state.md) 事件表、[frontend/docs/dev.md](../../../../frontend/docs/dev.md)(helpers 的由来 + 断线怎么造 + `setOffline` 不管用)。
- **③ 文档↔文档一致**:[TODO.md](../TODO.md) 勾掉 0079·B 与 0078·D(后者的余项正是本批验的 seq);[BUGS.md](../BUGS.md) 的 BUG-19 补「0087 进展」——`last_bet` 那一半已补齐,**剩下的是 `last_raise_size`**,不能让后来者以为整条已修。本篇链回 0078/0083/0084/0085。
- **④ 数据模型正确性**:三个新字段都**必填、无默认**,漏填是编译期错误(实测:加完先跑测试,`tests/wire/test_protocol.py` 立刻红,正是它漏填);`HandStatusChanged.players` 与 `HandStarted.players`/`StateSnapshot.players` 同一投影(`_player_views`),不会出现三份口径。`WS_CLOSE_DISPLACED` 取 4409 而不是复用 4401:会话仍然有效,报 4401 等于骗客户端「你的会话没了」,还会诱导它去重新登录——而重新登录会再顶掉对方一次。
- **⑤ 规范合规**:新字段/新常量都带中文含义注释;三处关闭码裸字面量收成具名常量(本批之前 4400/4401 散在 receiver/sender/lifespan);无死代码;注释讲「为什么」,尤其三处反直觉点:为什么重连仍要发那条明知会被拒的 `join_room`、为什么顶替必须带码、为什么指纹不能用会自动等待的 locator 方法。
- **⑥ 测试充分**:6 处反向变异全部确认。**如实记几处缺口**:(a) 顶替用例断言的是「新连接一次没被抢回去 + 老连接不再反复重连」,**没有直接断言老连接收到的关闭码就是 4409**——浏览器侧只知道它被关了;真正钉住 4409 的是后端 `test_async_displacement_old_connection_exits_silently`(变异验证过),两条合起来才完整。(b) 断线窗口很短(约 0.5 秒),**没有验过「断线超过占座窗口(90 秒)后重连」**那条自愈路径——那正是「多余的 `join_room` 」存在的理由,目前只有推理和协议层的 `smoke:stale` 覆盖近似场景。(c) 会话过期(4401)的前端处置只有代码路径,没有用例——造一个过期会话要么改配置要么等一小时。(d) 摊牌时**对手亮出的底牌**在界面上只在 `hand_show_down` 与 `hand_ended` 之间闪一下(`hand_ended` 一到 `handStatus` 就变 `null`,整块牌面停止渲染),所以用例断言的是结算面板而不是亮牌;这是产品问题不是测试问题,记档见下。
- **⑦ 流程账本**:本篇即账本,开工前先写「打算」(含「先写验证、让它自己说话」的顺序)、收工回填。与打算相比多出三件:改了协议(开工时只预计可能动关闭码)、修了 `showdown.spec.ts` 的假绿、抽出了 `e2e/helpers.ts`。三件都如实分节记下,理由也写在原处。

### 顺带发现,未在本批处理

- **摊牌亮牌在界面上几乎看不见**:`hand_show_down` 与 `hand_ended` 是同一批事件,后者一到 `handStatus` 就变 `null`、`gameStarted` 转假,牌面整块停止渲染。协议层没问题(冒烟验过 reveals),是界面该给一个「结算展示期」。记档,未登记为缺陷(不是「会错」,是「没做」)。
- **`e2e/` 里还有三个 spec(hub / journey / table / vote-config)各自抄着自己的 login/joinTable**。本批把 `showdown` / `raise` / `reconnect` 收到 `helpers.ts`,其余的没动——它们的流程不同(有的根本不进房),机械替换收益不大、风险不为零。留档。
- **`ConnectionBanner` 的 `closed` 文案**仍是「可能是会话过期,或账号在别处登录」这种两头猜的说法。现在服务器分得清了(4401 vs 4409),但被顶替时页面会直接跳回登录页、横幅根本来不及显示,所以没动它。真要改得先决定「被顶之后是留在原页还是跳走」。
