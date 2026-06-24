"""P4(二):PersistWriter 写回协程穷举(db.md「主循环 / 失败回灌 / 毒丸 / 优雅 drain」)。

脱真 DB:Persister 协议用 FakePersister 替身(可控失败 / 可门控暂停),直驱 flush_once/drain,
不依赖睡眠时序。验:先 swap 后 await(双缓冲隔离)、失败整批回灌「更新者优先」、毒丸丢批、drain 清空/有界。
"""

import asyncio

from app.core.records import HandRecordWrite, PointsWrite
from app.shell.persist import NullPersister, PersistWriter, WriteBuffer
from datetime import datetime, timezone

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _points(uid: int, points: int) -> PointsWrite:
    return PointsWrite(uid=uid, points=points)


def _record(key: str) -> HandRecordWrite:
    return HandRecordWrite(dedupe_key=key, start_time=T0, final_pot=0, participants=())


class FakePersister:
    # 落库替身:记录成功落库的批次;fail_times 次内抛异常;gate 不为 None 时 flush 阻塞在其上(测双缓冲)。
    def __init__(self, fail_times: int = 0) -> None:
        self.flushed: list[tuple[dict, list]] = []
        self.fail_times = fail_times
        self.attempts = 0
        self.gate: asyncio.Event | None = None

    async def flush(self, dirty, appends) -> None:
        self.attempts += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.attempts <= self.fail_times:
            raise RuntimeError("boom")
        self.flushed.append((dict(dirty), list(appends)))


def _writer(buf, persister, **kw) -> PersistWriter:
    kw.setdefault("flush_interval_s", 0.001)
    kw.setdefault("drain_timeout_s", 0.5)
    return PersistWriter(buf, persister, **kw)


# ── flush_once:空缓冲 no-op ──
async def test_flush_once_empty_is_noop():
    p = FakePersister()
    w = _writer(WriteBuffer(), p)
    assert await w.flush_once() is False
    assert p.attempts == 0


# ── flush_once:取走一批落库并清空 ──
async def test_flush_once_flushes_and_clears():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    buf.put(_record("dev:1"))
    p = FakePersister()
    w = _writer(buf, p)
    assert await w.flush_once() is True
    assert buf.is_empty()
    (dirty, appends) = p.flushed[0]
    assert dirty[("user", "1")].points == 100 and [r.dedupe_key for r in appends] == ["dev:1"]


# ── 失败 → 整批回灌,下次成功落库 ──
async def test_flush_failure_requeues_then_succeeds():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister(fail_times=1)
    w = _writer(buf, p, max_retry=5)
    await w.flush_once()  # 第一次失败 → 回灌
    assert not buf.is_empty() and p.flushed == []
    await w.flush_once()  # 第二次成功
    assert buf.is_empty() and p.flushed[0][0][("user", "1")].points == 100


# ── 回灌「更新者优先」:失败回灌**之前**内存已更新 250 → requeue 的旧 100 用 setdefault 不盖 250 ──
async def test_requeue_updater_wins_across_flush():
    # 关键时序:必须在 flush_once 的 requeue **跑之前**就让 250 进缓冲,才真正考到 requeue 的 setdefault
    # (若 put(250) 在 requeue 之后,250 只是普通 put 覆盖,setdefault 变异体也会绿——见自 review)。
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister(fail_times=1)
    p.gate = asyncio.Event()
    w = _writer(buf, p, max_retry=5)
    task = asyncio.create_task(w.flush_once())  # swap 取走 100,卡在 gate(尚未 requeue)
    await asyncio.sleep(0)
    buf.put(_points(1, 250))  # 在 requeue 之前,内存权威更新到 250(进新缓冲)
    p.gate.set()  # 放行 → flush 抛错 → requeue(旧 100)setdefault 进缓冲(键已有 250 → 不盖)
    await task
    assert buf.swap()[0][("user", "1")].points == 250  # 更新者优先:保留 250,不被回灌的旧 100 盖


# ── 取消落在 flush 半途:批先回灌、由 drain 补落(不静默丢)──
async def test_cancel_during_flush_requeues_for_drain():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister()
    p.gate = asyncio.Event()  # flush 卡住,模拟在飞落库
    w = _writer(buf, p)
    task = asyncio.create_task(w.run())  # 周期循环很快进 flush_once、swap 取走、卡 gate
    for _ in range(50):  # 等到 run 进 flush(批已 swap 出、缓冲空)
        if p.attempts == 1 and buf.is_empty():
            break
        await asyncio.sleep(0.005)
    assert p.attempts == 1 and buf.is_empty()  # 批在飞
    task.cancel()  # 关闭取消落在 flush 半途
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert not buf.is_empty()  # 在飞批已被回灌(不丢),待 drain 补落
    p.gate.set()  # 放行后用新 persister drain 补落
    p2 = FakePersister()
    await PersistWriter(buf, p2, flush_interval_s=0.001, drain_timeout_s=0.5).drain()
    assert buf.is_empty() and p2.flushed[0][0][("user", "1")].points == 100


# ── 成功路径复位 fail_streak:fail→success→fail,max_retry=2 时第二次 fail 仍回灌(非毒丸)──
async def test_success_resets_fail_streak():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister(fail_times=1)  # 第 1 次失败、第 2 次成功、第 3 次……成功(只 fail 一次)
    w = _writer(buf, p, max_retry=2)
    await w.flush_once()  # fail → streak 1
    assert w._fail_streak == 1
    await w.flush_once()  # success → streak 复位 0(缓冲清空)
    assert w._fail_streak == 0 and buf.is_empty()
    # 若成功不复位 streak,下面这次失败会 streak=2 触发毒丸丢批;复位后应只是回灌
    buf.put(_points(2, 50))
    p.fail_times = 3  # 让接下来的尝试再失败一次(attempts 已 2,fail_times=3 → 第 3 次失败)
    await w.flush_once()  # fail → streak 应为 1(已复位),回灌非毒丸
    assert w._fail_streak == 1 and not buf.is_empty()


# ── 毒丸:同批连续失败达 max_retry → 丢批,缓冲清空,失败计数复位 ──
async def test_poison_pill_drops_after_max_retry():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister(fail_times=99)  # 永远失败
    w = _writer(buf, p, max_retry=3)
    await w.flush_once()  # streak 1 → 回灌
    await w.flush_once()  # streak 2 → 回灌
    assert not buf.is_empty()
    await w.flush_once()  # streak 3 == max_retry → 毒丸丢批
    assert buf.is_empty() and p.flushed == []
    assert w._fail_streak == 0  # 计数复位,不卡死后续


# ── 双缓冲:先 swap 后 await——flush await 期间新 put 进新缓冲,不污染在飞批次 ──
async def test_double_buffer_swap_before_await():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister()
    p.gate = asyncio.Event()  # flush 阻塞在 gate 上
    w = _writer(buf, p)
    task = asyncio.create_task(w.flush_once())  # swap 取走 {1:100},卡在 gate
    await asyncio.sleep(0)  # 让 flush_once 跑到 await gate
    buf.put(_points(1, 999))  # 在飞落库期间内存又写 → 进新空缓冲
    assert len(buf) == 1  # 新值在新缓冲,不影响已 swap 批次
    p.gate.set()  # 放行
    await task
    assert p.flushed[0][0][("user", "1")].points == 100  # 落的是 swap 那刻的 100,不是 999
    assert len(buf) == 1  # 999 仍在缓冲待下批


# ── drain:循环 flush 直到缓冲空 ──
async def test_drain_empties_buffer():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    buf.put(_record("dev:1"))
    buf.put(_record("dev:2"))
    p = FakePersister()
    w = _writer(buf, p)
    await w.drain()
    assert buf.is_empty()
    flushed_appends = [r for _, appends in p.flushed for r in appends]
    assert {r.dedupe_key for r in flushed_appends} == {"dev:1", "dev:2"}


# ── drain:持久失败 → 毒丸兜底清空(不挂死)──
async def test_drain_bounded_on_persistent_failure_via_poison():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister(fail_times=99)
    w = _writer(buf, p, max_retry=2)
    await asyncio.wait_for(w.drain(), timeout=2.0)  # 不挂死:毒丸丢批后缓冲空,drain 返回
    assert buf.is_empty()


# ── drain:超时放弃(max_retry 极大不触毒丸、drain_timeout 极小)→ 返回但缓冲残留 ──
async def test_drain_timeout_returns_without_hang():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister(fail_times=10**9)  # 始终失败
    w = _writer(buf, p, max_retry=10**9, drain_timeout_s=0.02)  # 不触毒丸,只能靠 deadline 退出
    await asyncio.wait_for(w.drain(), timeout=2.0)  # 超时即返回,不挂死
    assert not buf.is_empty()  # 持久失败 + 未达毒丸 → 残留(已 CRITICAL 记,进程将退)


# ── run 主循环:周期 flush(冒烟)──
async def test_run_loop_flushes_periodically():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister()
    w = _writer(buf, p, flush_interval_s=0.005)
    task = asyncio.create_task(w.run())
    for _ in range(50):  # 至多 ~0.25s,轮询到落库即停
        if p.flushed:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert p.flushed and buf.is_empty()


# ── NullPersister:不抛、吞批(dev 无 DB)──
async def test_null_persister_succeeds_silently():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    w = _writer(buf, NullPersister())
    await w.flush_once()
    assert buf.is_empty()  # NullPersister 成功 → 批次落(丢弃),缓冲清空
