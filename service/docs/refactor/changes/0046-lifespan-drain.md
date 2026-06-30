# 0046 · P8 收尾:lifespan 关闭反序 drain(faithful shutdown + 集成测)

日期:2026-06-30 · 范围:`app/shell/lifespan.py`(`DevShell.start/stop` 反序关闭 + 排空 inbox + cancel Senders)、`tests/shell/test_lifespan_drain.py`(新建)、`docs/connection.md`/`TODO.md`。落地 [connection.md](../../connection.md)「lifespan 关闭(必须 drain)」四步序 —— TODO P8 项。

## 背景 / 为什么(批判性:先核实「还剩什么」)

TODO P8:「`shell/lifespan.py` drain:关闭反序 drain(超 `DB_DRAIN_TIMEOUT_MS` 落 CRITICAL)」。核实后:**有界 drain 本体(timeout → CRITICAL + 毒丸 + 取消半途回灌)早随 0025 落在 `PersistWriter.drain()` 并穷举测**(`test_persist_writer` 的 `test_drain_timeout_returns_without_hang`/`poison`/`empties_buffer`/`cancel_during_flush`)。`DevShell.stop()`(0029)已调 `drain()`。所以「超时 CRITICAL」这一头条**已满足**。

真实缺口 = `stop()` 与 connection.md:177-180 的**四步关闭序**有偏差,且**无集成测**证明 `stop()` 端到端把缓冲落进 DB:
- connection.md 关闭序:① 停 Receiver → ② **排空 inbox + 停 GameLoop**(在途命令处理完、不再产新 Persist)→ ③ PersistWriter 终结 flush(有界 / CRITICAL)→ ④ 关连接池 + cancel 各 Sender。
- 旧 `stop()`:把 gameloop/timer/persistwriter **一起 cancel** → drain → dispose。**缺**:② 的「排空 inbox」(直接 cancel GameLoop → 排队命令被丢,其本该产生的 Persist 不落)、④ 的「cancel 各 Sender」。

## 关键设计决策

1. **反序分阶段 stop()**(对齐 connection.md):① cancel Timer + GameLoop(停 inbox 的生产与消费;GameLoop 在 `await get()` 处被 cancel,**绝不打断处理到一半的命令**——`handle` 全程同步,cancel 只在下个 await 生效,故在途命令要么完整处理、要么根本没开始)→ ② **同步排空 inbox**:`while not inbox.empty(): gameloop.handle(get_nowait())`(复用 `handle` 这个「抽出供直接驱动」的同步方法;全程无 await ⇒ 该循环原子,PersistWriter 不与之竞 swap)→ ③ cancel PersistWriter 周期循环 + `await drain()`(有界 / CRITICAL,0025)→ ④ cancel 各 Sender(`conns.online_nicks()` 遍历)+ `engine.dispose()`。
2. **排空 inbox 不破坏一致性**:被丢的只可能是「未开始处理」的排队命令——它们从未 commit 进 world、也无对应 Persist,丢弃即「丢一个未生效的输入」(积分非货币、重连可重发,storage.md 接受)。② 把这些命令处理掉是「更优雅」,让其 Persist 也落库;但**正确性不依赖它**(已完成命令的写在 cancel 生效前已入缓冲,③ 必落)。
3. **dispose 总会执行**:drain 即便超时(CRITICAL)也 return(非 raise),故 `dispose()` 在其后照常跑,释放连接池让进程干净退出。
4. **Sender cancel 是 best-effort 兜底**:正常路径每条 Receiver 的 finally 已 cancel 自己的 Sender;uvicorn 关闭时撕连接也触发之。④ 兜住仍登记的连接,cancel 已结束的 task 是 no-op。
5. **不改 `PersistWriter.drain()`**(已正确 + 已测);本批只补 `stop()` 编排 + 集成测。

## 打算改什么(开工前)

- `lifespan.py`:`__init__` 存命名 task 引用(`_gameloop_task`/`_timer_task`/`_persistwriter_task`,初 None);`start()` 命名建 task;`stop()` 重写为四阶段 + `_cancel_and_await` 辅助。
- `tests/shell/test_lifespan_drain.py`:① inbox 在途命令(join/sit/buy)经 `stop()` 排空 + 落 DB(文件 sqlite,dispose 后新 engine 验);② 已入缓冲的写经 `stop()` 落 DB;③ `start()`→`stop()` 不挂死(wait_for 兜)。
- 文档:connection.md(dev shell 落地注:stop 反序 + 排空 inbox)、TODO.md(P8 drain 勾)。

## 实际改了什么

- **`app/shell/lifespan.py`**:`DevShell.__init__` 把 `self._tasks: list` 换成三个命名引用 `_gameloop_task`/`_timer_task`/`_persistwriter_task`(初 None);`start()` 命名建 task;新增 `_cancel_and_await(*tasks)` 辅助(cancel + 收割,吞 CancelledError、意外死亡 ERROR 不阻断);`stop()` 重写为**反序关闭**:① `_cancel_and_await(timer, gameloop)` → ② `while not inbox.empty(): gameloop.handle(get_nowait())` 排空在途命令(**每条 handle 包 try/except 兜异常 + 计数 INFO**,见自 review)→ ③ `_cancel_and_await(persistwriter)` + `await drain()` → ④ 遍历 `conns.online_nicks()` cancel 各 `sender_task` + `await engine.dispose()`。
- **`app/shell/gameloop.py`**:`handle` 注释补「关闭排空(lifespan.stop ②)直接驱动」用途(原仅「供测试直接驱动」)。
- **`tests/shell/test_lifespan_drain.py`**(新建,6):inbox 在途 join/sit/buy 经 `stop()` 排空 + 落 DB(文件 sqlite,dispose 后新 engine 验积分)/ 已入缓冲的 `PointsWrite` 经 `stop()` drain 落 DB / `start()`→`stop()` 不挂死(`wait_for` 5s)/ 未 `start()` 直接 `stop()` 安全 + **自 review 补**:`stop()` cancel 已注册连接的 `sender_task`(④)/ **并发交接 exactly-once**(start + 排队 + stop 竞,终态 DB = 一次买入,不丢不重)。
- **文档**:`connection.md`(dev shell 落地注:stop 反序 + dev ①-④ 不与 spec 1-4 一一对应说明)、`TODO.md`(P8 drain 勾 + 余端到端冒烟)。
- **未改**:`PersistWriter.drain()`(0025 已正确 + 已测,本批不碰)、core(不涉)。

432 全绿(426→432,+6);app `create_app()` 构建 + `stop` 仍是协程,冒烟通过;core 无越层 import(未碰)。

## 自 review

方法:对照 [review.md](../../review.md) 跑 **3 维 compact 对抗 review 子代理工作流**(关闭序/并发正确性 · 测试充分 · 文档/账本+scope 批判)。**3 agent、6 确认(0 真 code bug)**:并发维 agent 实证「Queue 取走即移除 ⇒ cancel-during-get 不丢不重」「dispatch `match ev` 无 raise 路径」。逐维:

- **① 关闭序 / 并发**:inbox 排空循环全程无 await ⇒ 原子,PersistWriter 不竞 swap;cancel GameLoop 后 `await` 收割保证其停;每条命令 Queue 取走即移除 ⇒ gameloop 与 drain 不双处理。**采纳 1(robustness,本就在 review 前自查并修)**:原 drain 循环只 catch `QueueEmpty`,而 `gameloop.handle` 的 `checkout`/`commit`/`dispatch` 在其内层 try 之外,若抛会冒出 `stop()` 跳过 ③④ 致连接池泄漏——**已包 `try/except` 兜每条 handle**(尽力 drain,dispose 必达);实测 dispatch `match ev` 无 `case _`、未知事件静默掠过不 raise,故实际不易触发,属防御补强。
- **⑥ 测试**:**采纳 2 test-gap**——④ Sender-cancel 零覆盖(补 `test_stop_cancels_registered_senders`:注册连接 + 长跑 sender_task → stop cancel 之);并发交接零覆盖(补 `test_stop_drains_concurrently_with_live_gameloop_exactly_once`:start + 排队 + stop 竞 → 终态 DB = 一次买入,钉死不丢不重)。drain 本体超时/毒丸/回灌已在 test_persist_writer 覆盖,本批不重测。
- **②③ 文档**:**采纳 2**——connection.md 的「反序四步」会误读为与 spec 1-4 一一对应(实则 spec 步1「停 Receiver」dev 无显式动作【uvicorn 先撕 receiver】、Timer-cancel 是 dev 专属),已改「反序关闭」+ 补不对应说明;gameloop `handle` 注释「供测试直接驱动」未提关闭复用,已补。
- **⑤ 规范**:`stop()` 四阶段各带「为什么」注释(cancel 不打断同步 handle / 排空原子 / drain 超时也 return 故 dispose 必达 / Sender best-effort);无裸字面量(`DB_DRAIN_TIMEOUT_MS` 经 gameconfig)。
- **⑦ 账本**:打算↔实际一致(多出 robustness 兜异常 + 2 补测);测 426→432;自 review 段已填(review #6 提示);提交引用 0046、全英文。

**对抗核实存活 / 采纳 / 驳回**:6 候选全 survives——采纳 5(1 robustness 已自修、2 test-gap、2 doc),1 是自 review 占位(本段即填)。残留:dev stop 的 `except QueueEmpty`(get_nowait 前已 `not empty()` 故不可达)保留作防御惯用,不算死代码红线。0 真 bug;review 兑现「绿测 ≠ 可提交」——抓出「drain 循环异常会跳 dispose 泄漏池」(已自修)+ Sender-cancel/并发交接两处零覆盖 + 文档 spec↔dev 误对应。

## 待办 / 下一步

- P8 余:端到端冒烟(前端 ↔ 后端走通一手牌)——需前端 WS client 集成。
- P5 国密信道落地时,Receiver/Sender 帧编解替换;本 stop() 反序框架可复用。
