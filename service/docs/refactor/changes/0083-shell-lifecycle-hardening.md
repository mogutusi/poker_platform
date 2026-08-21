# 0083 · shell 生命周期硬化:顶替链 / 丢连 / 改名重挂 / GameLoop 兜底 / 优雅关闭

日期:2026-08-21 · 性质:**缺陷修复(后端 shell 层)**· 触发:接手继续开发,从 [TODO.md](../TODO.md) 取「已确认为真但未修」的 shell 缺陷。

## 为什么选这一批(开工前的判断)

[BUGS.md](../BUGS.md) 里 high + medium 共 8 条未修缺陷。挑出的 5 条有一个共同点:**全都在 shell 层,全都是「协程/连接的生命周期」出错**,而且两两之间的修法互相牵动(顶替链的修法和丢连的修法都在改「一条连接怎么干净地终结」)。分批修会来回动同几行代码,合成一批更省也更安全。

| 本批 | 严重度 | 一句话 |
|---|---|---|
| [BUG-1](../BUGS.md)(0074·E) | **high** | 顶替链 A←B←C:B 在 `_displace` 的 await 窗内被 C 顶掉,恢复后仍复活用户 + 抹清理表 → 座位筹码永久泄漏 |
| [BUG-6](../BUGS.md)(0072·N2) | medium | 慢客户端被 drop 时只 unregister,不关 ws 不 cancel 协程 → 幽灵命令源 + 同 nick 双 Receiver |
| [BUG-7](../BUGS.md)(0072·N3) | medium | `GameLoop.handle` 的 `except` 只裹 `reduce()` 一行,commit/dispatch 抛异常会杀掉唯一状态写者且无人察觉 |
| [BUG-4](../BUGS.md)(0074·F) | medium | 改昵称窗内 ws 顶替:`rekey` 只改了已死对象,活连接永久挂旧键 |
| [BUG-5](../BUGS.md)(0074·I/J) | medium | 关闭路径:`_cancel_and_await` 吞掉 `stop()` 自身的取消;lifespan 的 `yield` 无 `try/finally` → drain 整体跳过 |

**不在本批**:BUG-2(手牌记录撞键,用户已定案暂缓)、BUG-3(Timeout 跨手 staleness,属 core/协议面,要动 `Timeout` 命令字段 + wire)、BUG-8/9/10(会话吊销 / 快照投影 / 离场者收结算,各自要动协议或 reduce)。本批一行 core 都不动。

## 打算怎么改

### 1. BUG-1 · `_displace` 之后复查 `is_current`(`shell/receiver.py`)

`run_receiver` 里 `await _displace(old)` 是一个 await 窗口;窗内本连接自己可能已被更新的连接顶掉。恢复后照旧 `cancel_cleanup` + 投 `Connect`,就会把已经 `OFFLINE` 的用户复活成在线,同时抹掉占座清理表里的定时项——`_cleanup` 只回收 `OFFLINE` 座位,于是清理再也不触发,座位与桌上筹码永久泄漏。

修法:`_displace` 返回后复查 `conns.is_current(conn)`,不是当前连接就直接返回,不起 Sender、不拆表、不投 `Connect`。此时尚未进 `try`,也就没有 `finally` 里的 `Disconnect`——这正是要的:顶替语义下旧连接静默退出。

### 2. BUG-6 · drop 慢客户端时一并终结其协程(`shell/dispatch.py` + `shell/connection.py`)

`_drop_connection` 只把连接从表里摘掉,ws 还开着、Receiver 还阻塞在 `receive`。客户端继续发帧就继续往 `inbox` 投命令——一个已经"不存在"的连接仍在驱动状态机;重连后同一 nick 会同时挂两个 Receiver。

修法:对齐 `receiver.py` 的退出清理路径,drop 时 cancel Sender + cancel Receiver。为此 `Connection` 加 `receiver_task` 字段,由 `run_receiver` 自己填(`asyncio.current_task()`)。

**为什么 cancel Receiver 而不是只关 ws**:触发条件是「读慢写健」的非对称慢客户端——它的 TCP 发送缓冲已经堵住,`ws.close()` 要发关闭帧,同样可能堵住,而客户端的上行仍然畅通,Receiver 会继续产出命令。只关 ws 堵不住幽灵命令源。cancel 是同步的(只置标志、下个 await 生效),不违反「dispatch 不 await」。

### 3. BUG-7 · GameLoop 兜底范围 + watchdog(`shell/gameloop.py` + `shell/lifespan.py`)

`handle` 只兜住 `reduce()`;`checkout`/`commit`/`_audit_applied`/`dispatch` 抛异常会冒出 `run()` 杀掉唯一状态写者,与 [architecture.md](../../architecture.md)「接住 → 继续下一条」不符,且无人察觉。

修法两处:把 `except Exception` 提到裹住 commit 与 dispatch;给 `run()` 的 task 挂 done-callback,非取消退出即落 CRITICAL。

### 4. BUG-4 · 改昵称的连接重挂按「当前连接 + 归属校验」(`rest/profile.py`)

`live_conn = conns.get(old_nick)` 在两次 DB await 之前捕获;窗内被顶替则它已是死对象,`rekey` 走 else 分支只改死对象的 `.nick`,活连接永久挂在旧键上。

修法:把捕获挪到全部 await 之后(那之后到 `rekey` 全程同步、原子),并加**归属校验**——0065 当初早捕获正是怕窗内别人改名占走 `old_nick` 键而误挂他人连接;晚查 + 校验同时堵住两头。

### 5. BUG-5 · 关闭路径不吞自己的取消 + `yield` 包 `try/finally`(`shell/lifespan.py`)

修法:`_cancel_and_await` 用 `t.cancelled()` 区分「子任务被我 cancel」与「取消是冲我来的」,后者上抛;lifespan 的 `yield` 包 `try/finally` 保证 `stop()` 必被调用。

## 要动的文件(预期)

- `app/shell/receiver.py` / `app/shell/dispatch.py` / `app/shell/connection.py` / `app/shell/gameloop.py` / `app/shell/lifespan.py` / `app/rest/profile.py`
- 测试:`tests/shell/test_receiver.py` / `test_dispatch.py` / `test_gameloop.py` / `test_lifespan_drain.py` / `tests/rest/test_change_nickname.py`
- 文档:[connection.md](../../connection.md)(顶替/退出清理/丢连语义)、[BUGS.md](../BUGS.md)(划掉已修)、[TODO.md](../TODO.md)(勾项)、[frontend/BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md)(丢连语义对前端可见 → 按 0070 起的用户指示同步)

## 实际改了什么

五条都修了,但**有三处偏离了开工时的打算**,都是对抗核实逼出来的(见下)。全部改动带回归测试,并逐条做了**反向变异验证**:把修法改回旧行为,对应的测试必须变红——不变红的测试等于没测(0078 的教训)。

### 1. BUG-1 · `_displace` 之后复查 `is_current`(`shell/receiver.py`)

按计划落地。核实过程补了两条计划里没有的认识:

- **窗口比想象的宽得多。** `await old.ws.close()` 一路走到 `websockets` 的 `close_timeout`(uvicorn 不传,取默认 10s),而且是**两段**(写关闭帧、等 `transfer_data_task`),窗口宽达十几秒。更要命的是它**恰好在顶替最常见的场景里最宽**——顶替本来就多发生在旧 socket 假死时([connection.md](../../connection.md)「顶替语义」)。
- **同一处复查还顺带堵住另一条路,不需要第三条连接**:B 卡在 `_displace` 期间已经在表里但还没起 Sender,dispatch 完全可以把它的 `outbound` 灌满 → `_drop_connection(B)` 摘键 + 投 `Disconnect` → B 恢复后照样复活用户。`is_current` 对这条路一样为假,一并挡掉。
- **偏离①:早退路径不补 `ws.close()`。** 原打算顺手关一下自己的 ws。实测这是个坏主意:多一个 await 就多一个可被 cancel 打断的窗口,而 `_drop_connection` 恰好可能在此刻 cancel 本 Receiver——异常点会落在 `try` 之外,`finally` 一行都不跑,只能靠「此刻 `sender_task` 仍是 None」这个巧合才不泄漏。改成直接 `return`:顶掉我的那条已经关过我的 ws,而 endpoint 一返回 uvicorn 自会收掉 socket。

### 2. BUG-6 · drop 时终结连接的两条协程(`shell/dispatch.py` + `shell/connection.py`)

- **偏离②:修法与 [BUGS.md](../BUGS.md) 登记的「drop 时关 ws + cancel」不同,改为 cancel Sender + cancel Receiver,不关 ws。** 理由是触发条件本身:能把 `outbound` 灌满的正是「读慢写健」的非对称慢客户端,它的下行已经堵住,**关闭帧和数据一样发不出去**,而上行畅通、Receiver 照旧产命令。关 ws 堵不住这个源。为此给 `Connection` 加 `receiver_task`,由 `run_receiver` 进门自填。
- 实测复现(把旧 drop 体装回当前 Dispatcher):旧行为下 drop 之后 Receiver `done=False`,被丢弃的连接还能投出 `SitDown`;重连后旧 ws 仍在产 `Connect`/`LeaveRoom`——同一 nick 两个 Receiver 并存坐实。新行为下两项都归零。
- **顺带堵上一个没登记的泄漏**:`stop()` 收 Sender 是按 `conns.online_nicks()` 遍历的,被 drop 的连接早已不在表里 → 它的 Sender 在关闭时也从没被 cancel 过。现在 drop 当场就 cancel 了。
- 没有出现「双份 `Disconnect`」——drop 与 Receiver 的 `finally` 两边都全程无 await,谁先跑另一边都看到「我不是当前连接」。但这条正确性**没有任何东西钉住**,已写进 [connection.md](../../connection.md) 和代码注释:往那个 `finally` 里加任何 await 都会重新打开窗口。

### 3. BUG-7 · 兜底范围 + watchdog(`shell/gameloop.py` + `shell/lifespan.py`)

- **偏离③(最重要):这条的定性要下调,而处置要上调。** 对抗核实把 `checkout`/`commit`/`_audit_applied`/`dispatch` 逐条走了一遍,**当前代码没有可达的抛出路径**:`commit` 是一次属性赋值加一次 dict 操作;`dispatch` 每条臂都是 dict/list 操作、`isinstance` 守住的 `dataclasses.replace`、或 `WriteBuffer.put`,唯二会抛的 `put_nowait` 早已各自兜住;`checkout` 深拷的全是普通 dataclass。0072·N3 当初也只给了结构性论证、没给出触发者。所以它是**潜在缺口**,不是活的崩溃路径,[BUGS.md](../BUGS.md) 与 [TODO.md](../TODO.md) 都已改口径。修它的理由是防御性的:**兜底缺口 + 无告警**的组合会把日后新增的任何一行(比如本批自己给 `Connection` 加字段)从一个 `AttributeError` 放大成永久哑掉的服务器。
- **处置比原计划严**,因为核实揪出了原方案两个真问题:
  - **不能无条件回 `INTERNAL`。** [error.md](../../error.md) 把 `INTERNAL` 定义成「工作副本已丢、`world` 未动」。commit 之后再崩时 `world` 已经改了,回 `INTERNAL` 是在骗客户端「什么都没发生」,还会诱导它重试而重复生效。现在按 `_Progress.committed` 分两路:commit 前照旧回 `INTERNAL`;commit 后不回报文,落 CRITICAL 留人工介入。这样 error.md 的契约不用改口径,行为也不再撒谎。
  - **派发必须逐事件兜。** 原方案里第一个炸掉的事件会把同批**剩下的全部事件**一起丢掉。手尾那一批正好装着 `Persist(HandRecordWrite)`、`Persist(PointsWrite)` 和 `ClearAction`/`TurnChanged`:丢一条 `Persist` 是手牌记录**永久**丢失(写缓冲里只有 `put` 进去的,没进去的没人重试),丢一条 `TurnChanged` 是 Timer 不装表、该行动的人能无限拖住整桌。现在每条事件各自 try。
- watchdog 挂给 GameLoop / Timer / PersistWriter 三条常驻协程,非取消退出即 CRITICAL。**这条不是新规矩,是补上早就写好的规矩**:[log.md](../../log.md) 的级别表里「GameLoop task 意外退出」一直列在 CRITICAL,只是没人实现——此前它只会在 GC 时以 asyncio 的 "Task exception was never retrieved" 冒个泡。
- 已知代价,记档不掩饰:`receiver_task` 存的是 `asyncio.current_task()`,而两个 ws endpoint 都是直接 `await run_receiver(...)`,所以它其实是 uvicorn 的 `run_asgi` task。cancel 它会让 uvicorn 打一条 ERROR 级的 `Exception in ASGI application` + CancelledError traceback,而 [log.md](../../log.md) 把「慢客户端被丢弃」定在 WARNING。取舍:慢客户端被丢弃本就罕见(要灌满 256 条出站队列),这条 ERROR 也确实带信息;而唯一的替代——只关 ws——已经论证过堵不住。**顺带一条给后人的地雷提示**:任何测试若直接 `await run_receiver(...)` 而不是 `create_task`,`receiver_task` 就成了测试自己的 task,一次 drop 会把测试取消掉。

### 4. BUG-4 · 改昵称的连接重挂(`rest/profile.py`)

- 连接改为**全部 await 之后**当场按 `old_nick` 查(从最后一次 `await update_nickname` 返回到 `rekey` 全程同步,查表与重挂之间没有窗口)。
- **偏离④:归属校验比原计划严。** 原打算「dev 明文连接(`session is None`)一律认作本人」,理由是「dev 端点要求 `?nick=` 名下有 DB 行」。这个理由是 TOCTOU:DB 行是**建连时**查的,到 rekey 这一步 `old_nick` 名下早就没有行了(刚被我改走)。若真有一条无会话连接搁浅在这个键上,就会把陌生 socket 认领成我的——之后它发的每条命令都按**我的** uid 解析。改成比 `session_id`:dev 端点建连时就把 `session_id` 盖成握手用的那个 nick,这是它自己立的不变量。抽出 `_belongs_to`。
- 顺带修正了三处夹具:它们造的是「无会话 + `session_id` 是会话 id」这种**生产里不存在**的连接形状,正好会被新校验挡掉。改成真实形状(加密连接带 `session`)。

### 5. BUG-5 · 关闭路径(`shell/lifespan.py`)

- `yield` 包 `try/finally`,关闭无条件跑到 `stop()`。
- `_cancel_and_await` 区分两种 `CancelledError`。**判据里 `current_task().cancelling()` 不可省**,这点是写测试时才看清的:cancel 一个正 `await` 别的 task 的 task,asyncio 会连它等的那个 future 一起 cancel ⇒ 子任务最终**也是** cancelled,只看 `t.cancelled()` 根本判不出「取消是冲我来的」。变异验证:把判据削成只剩 `t.cancelled()`,测试照样变红。

### 6. 顺带:冒烟的守恒断言是假的(`frontend/scripts/smoke-e2e.mjs`)

跑冒烟验证本批时它红了,查下来是脚本自身两个缺陷,与本批无关但会一直误报:

- **断言写死 `sum === 2000`,而它自己的注释写着「断言守恒,而不是某人回到 1000」。** dev 库是长期复用的:服务器带着「有人在座」被杀过一次,桌上的筹码就再也回不到全局积分(崩溃带走内存状态,见 [architecture.md](../../architecture.md)「崩溃语义」),写死的初始总额从此永远对不上。本机现在 alice+bob = 1800,少的 200 就是这么来的。改成跑之前先读一次基线、跑完比基线。
- **固定睡 400ms 就去读 `/leaderboard`,而 `DB_FLUSH_INTERVAL_MS` 缺省 500ms。** 积分走 delayDB,读的是 DB,必然偶尔读到退分之前的旧值——症状是「凭空少了一笔买入」,一个吓人的假阳性。改成轮询等到守恒或超时。

## 验证

| 层 | 结果 |
|---|---|
| 后端 pytest | **737 passed**(722 → 737,新增 15) |
| 反向变异验证 | 8 处(BUG-1 复查 / drop 双 cancel / handle 兜底 ×3 / 吞取消 / 只看 `t.cancelled()` / `yield` 无 finally / watchdog 挂载 / 窗前捕获 / 无归属校验)逐一确认「改回旧行为 → 对应测试变红」 |
| 前端 vitest | 81 passed |
| 协议冒烟 `npm run smoke` | 通过(守恒 1800 → 1800) |
| 残留自愈冒烟 | 通过 |
| 浏览器 `npm run test:e2e` | 12 passed |

后端改完已重启 uvicorn 再跑的前端各层(本仓纪律)。

## 自 review

按 [review.md](../../review.md) 七维。本批全在 shell,最高风险面是**并发交错**与**连接身份**,重点深挖这两处。

- **① 分层 / 不变量**:core 一行未动。守住的:`_drop_connection` 只 cancel(同步)不 await,不变量 3 未破;shell 仍不写 `world`(复活问题是靠「不投那条 `Connect`」解决的,不是靠 shell 去改状态);对外发送仍只经 Sender 队列。**新增一条不变量,已写进 [connection.md](../../connection.md)**:Receiver 的退出 `finally` 与 `dispatch._drop_connection` 都必须全程无 await,`Disconnect` 恰好一份全靠这个。
- **② 代码↔文档同步**:本批改了行为的地方逐处同步——[connection.md](../../connection.md)(`receiver_task` 字段、顶替后复查、丢连语义、契约 6)、[architecture.md](../../architecture.md)(兜底范围 + commit 前后两种崩法 + 逐事件派发 + watchdog)、[log.md](../../log.md)(那条 CRITICAL 从「只写着」变成「真有」)。**自 review 补抓两处漏网**:[rest.md](../../rest.md):128 与 [presence.md](../../presence.md):47 都还写着「handler 在 await 前先捕获连接对象」——那正是 BUG-4 的成因,留着就是给后人一份「把缺陷改回去」的书面依据,已一并重写并说明防劫持的责任现在归归属校验。
- **③ 文档↔文档一致**:[BUGS.md](../BUGS.md) 五条划掉 + 已修表补五行(按本篇规矩划掉不删行);[TODO.md](../TODO.md) 五项勾掉;BUG-6 与 BUG-7 两条**登记时的说法都作了更正**(修法不同 / 定性从「会错的代码」降为「潜在缺口」)——照抄旧登记去改才是真正的风险。[frontend/BACKEND_GUIDE.md](../../../../frontend/BACKEND_GUIDE.md) 按 0070 起的用户指示同步:被丢弃时**现在会真的关 ws**,这是前端可见的连接语义变化。
- **④ 数据模型正确性**:`Connection.receiver_task` 与 `sender_task` 同型同语义,不引入新的可表达非法态;`_Progress` 只有一个 bool,存在的理由是「异常逃逸时拿不到返回值」,已写在字段注释里。全仓 40 处构造 `Connection` 全走 `Connection.create` 关键字,新字段不会因位置参数错位。
- **⑤ 规范合规**:无裸字面量(没引入新可调参数);新字段/新 dataclass 成员都带中文含义注释;无死代码(`_close_quietly` 仍被 `_displace` 用);注释讲的是「为什么」——尤其是三处反直觉点:为什么 drop 要 cancel 而不是关 ws、为什么早退路径**不**补 close、为什么判据里 `cancelling()` 不能省。
- **⑥ 测试充分**:15 条新测,全部做了反向变异验证。**如实记两处覆盖空缺**:(a) 「exactly-one-`Disconnect`」这条正确性没有测试钉住(要构造 drop 与 Receiver 退出同帧交错,当前 fake 做不出来),只有文档和注释在保证;(b) cancel Receiver 会让 uvicorn 打 ERROR 这件事没有测试,因为所有测试都用 `create_task` 而不是真 endpoint。另记一处**测试设计陷阱**:仓库自带的 `FakeWS.close()` 里没有 await 点,拿它根本构造不出 `_displace` 的窗口,照它写的 BUG-1 回归测试**把修法删掉也照样绿**——本批为此专门做了带闸门的 `_SlowCloseWS`。
- **⑦ 流程账本**:本篇即账本,开工前先写、收工回填,三处偏离(早退不 close / drop 用 cancel / 归属校验加严)与一处定性更正(BUG-7)都留了痕。提交信息引用 0083。

### 驳回 / 未采纳

- **`_displace` 不跟着一起 cancel 旧 Receiver。** 核实时提出:既然 `receiver_task` 有了,`_displace` 只 cancel 旧 Sender、靠 `ws.close()` 让旧 Receiver 报错退出,万一那个 close 抛了,旧 Receiver 就停在 `receive_*` 上——正是刚修掉的幽灵命令源。**明知不对称,仍然不动**,理由是两条路径的性质相反:顶替是**正常且频繁**的路径(密钥静默轮换靠的就是重连顶替,见 [auth.md](../../auth.md)),在那里 cancel ASGI task 会让 uvicorn 每次都打一条 ERROR traceback,把正常流量变成错误日志;而 drop 是**罕见且已经降级**的路径,那点噪声换得起,何况在那里关 ws 已被证明无效。留档以便日后重议。
- **改昵称 × 顶替链的残余交错**:`rekey` 可能改到一条正卡在 `_displace` 里的连接的 `.nick`,它恢复后 `cancel_cleanup(新 nick)`,而待拆的表项挂在旧 nick 下。追下去无害:改昵称只在大厅可做,而大厅用户的 `Cleanup` 落到 reduce 里查不到 `world.users[旧 nick]`,是 no-op。记档不修。
- **`inbox` 满时 `await inbox.put(Connect)` 仍是一个可陈旧的窗口**:B 若卡在这里、期间 C 上位又断开,B 那条 `Connect` 还是会投出去。只在 inbox 满时可达,而 inbox 满按 [architecture.md](../../architecture.md) 已是 CRITICAL 的进程级故障态;要彻底关掉得把这里改成 `put_nowait` + 满则丢连,那是在改背压语义,不在本批范围。记档。
- **BUG-5 少一份独立复核**:本批的对抗核实是五路并行跑的,BUG-5 那一路失败没有产出。它的两条修法各自做了变异验证(把判据削回旧行为 / 去掉 `try/finally`,测试都变红),但**没有第二双眼睛复核过**,如实记下。
