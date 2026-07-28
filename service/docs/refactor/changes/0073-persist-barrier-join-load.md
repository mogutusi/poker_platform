# 0073 · 运行期落库屏障(persist barrier):封「驱逐后重读陈旧 DB」窗口(0072·N1)

日期:2026-07-28(设计+实施)· 范围:`app/shell/persist.py`(`PersistWriter` 唤醒 + `barrier()`)、`app/shell/gameloop.py`(`task_done`)、`app/shell/receiver.py`(`_build_join` 两步屏障)、`app/shell/lifespan.py`(接线 + stop-drain `task_done`)、tests(barrier 穷举 + N1 主钉 e2e)、docs(storage/db/user/connection + 0072 状态回填 + TODO)。

## 背景 / 为什么(0072·N1,CONFIRMED 2/2)

`_evict` 把 `UserState` 从内存权威删除、退分 `PointsWrite` 进 delayDB 缓冲;若同 nick 在 flush 窗口(≤`DB_FLUSH_INTERVAL_MS`,默认 500ms)内重新 `JoinRoom`,Receiver `_build_join` 从**滞后的 DB** 读积分、reduce 无条件安装 → 未落库的最新值被陈旧值覆盖固化——**正常运行(非崩溃)下静默丢分/凭空刷分**。storage.md「绝不重载已在内存的实体」只在实体**仍在内存**时生效,驱逐打开了它未覆盖的窗口。

**用户定案(2026-07-28)**:做一个**运行期「强制等落库」原语**——与停服 drain 同族的落库屏障(「停服就肯定要用到这个」:drain 即该语义的关闭期形态,本篇补运行期形态);**R1(dedupe_key 撞键)暂缓不修**。备选「JoinRoom 读穿缓冲(命中未落 uid 用缓冲值)」不采:引入第二读源、且同样堵不住下述顺序洞,屏障方案语义单一(「DB 是唯一读源,读前令其追平」)。

## 关键设计发现:单靠 flush 屏障堵不住,须两步

天真方案「`_build_join` 读 DB 前 flush 缓冲」有**顺序洞**:同连接连发 `leave_room`→`join_room` 时,Receiver 可能在 **GameLoop 尚未处理 LeaveRoom** 时就处理 join 帧——此刻退分 `PointsWrite` **还没进缓冲**,flush 屏障空过,照读陈旧 DB;JoinRoom 命令随后排在 LeaveRoom 之后被处理,lost-update 依旧。屏障与 GameLoop/PersistWriter 的调度先后**无从保证**(三个就绪任务的执行序取决于事件循环)。

故屏障必须两步,各自确定性:

```
_build_join(0073):
  ① await inbox.join()            —— 此刻已入队的命令(含同连接刚发的 LeaveRoom)全部处理完
                                      ⇒ 其 dispatch 的 Persist 已同步进缓冲
  ② await persistwriter.barrier() —— 强制「此刻缓冲里的写」落库(或缓冲本就空)
  ③ load_user_by_nick(...)        —— DB 已追平,安全读
  任一步超时/失败 → fail-closed:回 Err(INTERNAL),不进 reduce,绝不拿可能陈旧的值装权威。
```

`inbox.join()` 依赖消费方 `task_done()`:GameLoop.run 每条命令 `finally: task_done()`(handle 抛异常也计数,免 join 悬死;N3 的兜底问题另行处理,不在本篇);lifespan.stop 的同步排空循环同补。**join 等的是全局计数归零**(含期间他人新入队的命令)——≤20 人、命令处理 µs 级,队列常态归零;病态洪泛下由超时兜底(见下)。

## `PersistWriter.barrier()` 语义(实施蓝图)

- **契约**:`barrier(timeout_s=None) -> bool`。True = 调用时刻已在缓冲/在飞的写全部落库(或本就无待写);False = 超时 / 毒丸丢批 / 写者停止——**调用方 fail-closed**。缺省超时 = `DB_DRAIN_TIMEOUT_MS`(决策·可改:与 drain 共用「等落库上限」旋钮,不加第二个;正常路径只需 ≤1 个 commit,超时只在 DB 异常时触发,彼时 fail-closed 正确)。
- **唤醒**:`run()` 的 `sleep` 改为 `wait_for(_wake.wait(), timeout=interval)`——barrier 登记等待者后 `_wake.set()`,写者立即 flush,不等自然周期。
- **等待者归属**:`flush_once` **swap 前**取走当前等待者(其登记时刻的待写必在本批或更早)——本批 commit 成功 → resolve True;**缓冲空**(无可 flush)→ 直接 True;**失败回灌** → 等待者放回队首,随重试继续等;**毒丸丢批** → resolve **False**(数据已灭,等待永不可达,如实报失败);**关闭取消** → 等待者交还,`run()` 的 `finally` 统一 resolve False(免 Receiver 悬死)。
- **在飞窗口**:`barrier` 快路径 `缓冲空 且 无在飞批` 才直接 True——批已 swap 出、commit 未落时缓冲虽空但未持久,须登记等待。
- **与 drain 的关系**:同一语义两形态——drain 是关闭期(写者已停,stop() 自驱 flush 循环)、barrier 是运行期(写者活,登记-唤醒-等待)。drain 本篇不改。
- **接线**:`run_receiver(..., persistwriter: PersistWriter | None = None)` 关键字参数——生产(lifespan 两个 ws 端点)必传;None = 测试/dev 直驱模式跳过两步(既有 18 处测试调用零改动,N1 回归测试显式传)。

## 测试(计划)

- barrier 穷举(`test_persist_barrier.py`,FakePersister 直驱):缓冲空立即 True / 有待写经唤醒落库 True / 在飞批(gate)未 commit 不提前放行 / 失败重试后 True / 毒丸 False / 超时 False / 写者 cancel 挂起等待者 False。
- `GameLoop.run` 补 `task_done`:`inbox.join()` 在命令处理完后返回(含 handle 抛异常路径)。
- **N1 主钉(e2e,修复前必红)**:真 sqlite(参照 test_dev_db_e2e)——seed 100 分 → join → buyin 60(此刻手动 flush 使 DB=40)→ leave(退分 100 的 PointsWrite 进缓冲、**不 flush**)→ 同连接立即重 join(走 run_receiver 全链,起真 GameLoop + 真 PersistWriter[interval 拉大,唯 barrier 可触发 flush])→ 断言重进后内存积分 = 100(修复前读到 40)。

## 文档(计划)

- storage.md:①「载入一次」节补「**载入屏障(0073)**:驱逐后同 nick 重进,载入前经 `inbox.join()+PersistWriter.barrier()` 令 DB 追平」;契约速查 2 补该句。
- db.md:PersistWriter 节补 barrier 原语(唤醒/等待者/超时/毒丸/停止语义)+ 与 drain 的两形态关系。
- user.md:生命周期「载入」步补屏障;注意点补「驱逐后重进」。
- connection.md:收帧循环 JoinRoom 富化处补一句。
- 前端可见面:**无新增**——屏障失败复用既有 `INTERNAL`(进房读 DB 失败同通道),报文形状/语义不变,BACKEND_GUIDE 不改(0070 纪律核对:无前端需知的行为差异,仅极端 DB 异常时 join 多一种同码失败)。

## 实际改了什么(与「打算」对照)

按蓝图全部落地,零偏离:

- **`persist.py`**:`PersistWriter` 加 `_wake`(唤醒事件)/`_waiters`(屏障等待者)/`_in_flight`(在飞批标志);`run()` 的 `sleep` 改 `wait_for(_wake, interval)` + `finally` 统一 resolve False(写者停止不悬死调用方);`flush_once()` swap 前取走等待者,空缓冲/成功 → True、失败回灌 → 放回续等、毒丸 → False、取消 → 交还给 run finally;新增 `barrier(timeout_s=None) -> bool`(快路径「缓冲空且无在飞」;缺省超时 = `DB_DRAIN_TIMEOUT_MS`)与 `_resolve` 助手。
- **`gameloop.py`**:`run()` 每条命令 `finally: inbox.task_done()`(handle 异常也计数,免 `join()` 悬死)。
- **`receiver.py`**:`run_receiver`/`_frame_to_command`/`_build_join` 穿 `persistwriter: PersistWriter | None = None`(关键字缺省 → 既有 18 处测试调用零改动;None = 测试/dev 直驱跳过);`_build_join` 读 DB 前两步屏障,任一步失败回 `Err(INTERNAL)` + `log.error`、不进 reduce。
- **`lifespan.py`**:两个 ws 端点传 `persistwriter=shell.persistwriter`;stop() 同步排空循环补 `finally: task_done()`(与 GameLoop.run 对称计数)。
- **tests**:新 `tests/shell/test_persist_barrier.py` 9 测——barrier 穷举 7(空缓冲快路径 / 唤醒落库[interval=999 证明非自然周期] / 在飞批不提前放行 / 失败重试后 True / 毒丸 False / 超时 False / 写者 cancel False+批回灌待 drain)+ `task_done` 供 `inbox.join` 1 + **N1 主钉 e2e**(真 sqlite:买入落库定格 DB → 离房退分只进缓冲 → 同连接紧接重进 → 断言积分不回退且 DB 已追平)。698 → **707 全绿**。
- **修复前必红验证(scratchpad,不入库)**:同一 e2e 流程关屏障(`persistwriter=None`)跑一次,缺陷如期复现(积分 1000→900,丢 100)——证明屏障即修复点,测试非凑巧绿。

## 自 review

对照 [review.md](../review.md) 逐维:

- **① 分层/不变量**:全部改动在 shell;core 零改动。`task_done` 同步(不变量 3 GameLoop 无 await 不破);唯一 DB 写者不变(barrier 只登记-唤醒-等待,flush 仍在写者协程/drain 单线);Receiver await `join()`/`barrier()` 属 shell IO 等待(Receiver 本就 await DB);新耦合仅 receiver→PersistWriter(shell 内部合法)。超时走 `gameconfig.DB_DRAIN_TIMEOUT_MS`,无裸字面量。
- **② 代码↔文档**:storage.md(载入屏障节 + 契约 2)/db.md(「运行期落库屏障」新节)/user.md(载入步 + 注意点)/connection.md(收帧循环步 5)四处同批同步;0072 台账 N1 状态回填、TODO 勾项。
- **③ 文档↔文档**:四文档口径一致(两步缺一不可、fail-closed、与 drain 共用旋钮、drain=关闭期形态);db.md 明写「屏障只保证落库不保证已入缓冲」防误用。
- **④ 数据模型**:无 schema/wire 变更;`PersistWriter` 三个新私有态逐一注释含义与不变量(在飞窗口/等待者归属)。
- **⑤ 规范**:注释中文、讲为什么(顺序洞、毒丸 fail-closed 理由);无死代码、无魔法数(0.002/999 等仅测试用例值);`persistwriter` 关键字缺省的「生产必传」在参数注释与 lifespan 接线处双写。
- **⑥ 测试**:主钉直击被修缺陷且做了「关屏障必红」反证;变异体覆盖——删 `task_done` → join 测悬死红;快路径漏 `_in_flight` → 在飞测红;毒丸 resolve True → 毒丸测红;`interval=999` 钉「靠唤醒不靠周期」杀「自然 flush 恰好赶上」的假绿。~~lifespan 不接线 → N1 e2e 红~~(**复审证伪**:e2e 自己接线不经端点——已补端点接线钉,见下「三视角复审」④)。
- **⑦ 流程账本**:设计先行(本篇「关键设计发现」节记录了「单靠 flush 屏障不够、须两步」的推翻-重设计过程);0072·N1 状态回填、R1 用户定案暂缓同批记账;前端可见面核对:无新增(复用 INTERNAL 同通道),BACKend_GUIDE 不改(0070 纪律,理由上记)。

**对抗自问(crux)**:① `inbox.join()` 等的是全局计数归零,他人持续入队会不会长等?——≤20 人命令 µs 级处理、队列常态归零;病态洪泛由 `DB_DRAIN_TIMEOUT_MS` 超时兜底 fail-closed,不悬死。② barrier 在写者已死(N3 场景)时?——`join()` 超时 False → INTERNAL,不装陈旧值(N3 本体另修,见 TODO)。③ 两个 Receiver 并发 join 各自登记等待者?——等待者列表按批取走、`_resolve` 幂等(done 跳过),互不干扰;写者单协程无并发 flush。④ `task_done` 多调/漏调?——消费点仅 GameLoop.run 与 stop-drain,两处均 finally 成对;put 侧无 task_done 概念,计数天然平衡。

### 三视角复审(工作流:并发正确性 / 文档同步 / 测试充分性)与处置

初版落地后跑对抗复审,**抓出 1 个真 bug、1 条不实自 review 断言、2 处文档遗漏、3 个存活变异体**,全部当批处置:

1. **真 bug · 毒丸 × 在飞登记(两审查者独立命中,medium)**:等待者在毒丸批 `await flush` 期间经 barrier 慢路径登记 → 只在 `self._waiters` 不在本批 `waiters`,毒丸后会被**下一轮空缓冲 flush 误 resolve True**(数据已灭而 DB 未追平 = N1 换触发面复活)。**修**:毒丸分支连同 `self._waiters` 一并取走 resolve False(过杀安全:fail-closed 只多一次 INTERNAL 重试)+ 杀测 `test_barrier_false_when_registered_during_poisoned_flight`(修复前必红,已变异验证)。
2. **不实断言**:⑥ 原写「lifespan 不接线 → N1 e2e 红」,复审实验证伪(删接线 707 仍绿——e2e 直调 run_receiver 不经端点)。**修**:划掉原句 + 补端点接线钉 `test_ws_endpoints_wire_persistwriter`(monkeypatch 捕获两个 ws 端点 handler 的实参,断言 `persistwriter is shell.persistwriter`)。
3. **存活变异体 ×3,各补杀测**(补后逐一变异验证 KILLED):超时摘除行(写者死时多次超时不得泄漏已取消 future → `test_barrier_timeout_removes_waiter_when_writer_dead`)/ stop-drain `task_done`(`test_stop_drain_balances_inbox_join`:stop 后 `inbox.join()` 0.1s 内返回)/ 跨连接驱逐路径(`test_n1_cleanup_evict_then_rejoin_keeps_points`:断线 → Timer `Cleanup` 驱逐退分入缓冲 → 第二条连接同 nick 重进不回退)。
4. **文档遗漏**:architecture.md 协程构成表两格(Receiver 让出点补 `inbox.join()+barrier()`、PersistWriter 让出点改「可被唤醒的 wait_for」)与 db.md:48「sleep→flush 薄壳」旧口径,均已改。
5. **误报排除**:复审报告的「MUTANT 注入进程在改工作区」实为测试充分性审查者自身的变异实验暂态(sed 改-测-还原);已核实 `grep MUTANT` 零命中、diff 仅本批 9+1 文件、全量重跑绿。

最终:**712 全绿**(707 + barrier 追加 5);0 未处置发现。
