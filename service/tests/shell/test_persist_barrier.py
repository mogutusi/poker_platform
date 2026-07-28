"""0073 运行期落库屏障:PersistWriter.barrier 穷举 + JoinRoom 载入屏障的 N1 主钉 e2e。

单元侧(FakePersister,同 test_persist_writer):缓冲空即达成 / 唤醒落库 / 在飞批不提前放行 /
失败重试后达成 / 毒丸 fail-closed / 超时 fail-closed / 写者停止 fail-closed / GameLoop task_done 供 inbox.join。
e2e 侧(真 sqlite,同 test_dev_db_e2e):0072·N1 主钉——离房退分未落库、立即重进,载入不得回退积分
(修复前必红:读到滞后 DB 的旧值)。
"""

import asyncio

from app import gameconfig
from app.core.commands import Connect
from app.core.records import PointsWrite
from app.db.models import User
from app.db.orm_persister import OrmPersister
from app.shell.connection import Connection, ConnectionManager
from app.shell.dispatch import Dispatcher
from app.shell.gameloop import GameLoop
from app.shell.lifespan import DevShell
from app.shell.persist import PersistWriter, WriteBuffer
from app.shell.receiver import run_receiver
from app.shell.timer import Timer
from app.core.domain import World
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from app.db.engine import make_engine
from tests.shell._fakes import FakeWS


def _points(uid: int, points: int) -> PointsWrite:
    return PointsWrite(uid=uid, points=points)


class FakePersister:
    # 同 test_persist_writer 的替身:可控失败(fail_times)/ 可门控暂停(gate,测在飞窗口)。
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

    async def cleanup_dms(self, cutoff) -> int:
        return 0


def _writer(buf, persister, **kw) -> PersistWriter:
    kw.setdefault("flush_interval_s", 0.002)
    kw.setdefault("drain_timeout_s", 0.5)
    return PersistWriter(buf, persister, **kw)


async def _with_run(w: PersistWriter, coro):
    # 起 run 循环跑 coro,收尾 cancel(barrier 依赖 run 活着消费唤醒)。
    task = asyncio.create_task(w.run())
    try:
        return await coro
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── 缓冲空且无在飞:立即 True,不需要写者 ──
async def test_barrier_empty_buffer_true_immediately():
    w = _writer(WriteBuffer(), FakePersister())
    assert await w.barrier(timeout_s=0.01) is True  # 快路径,无等待


# ── 有待写:barrier 唤醒写者落库后 True(不等自然周期:interval 拉大到测试必超时的程度)──
async def test_barrier_wakes_writer_and_flushes():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister()
    w = _writer(buf, p, flush_interval_s=999)  # 自然周期永不来,只有唤醒能触发
    ok = await _with_run(w, asyncio.wait_for(w.barrier(timeout_s=1.0), timeout=2.0))
    assert ok is True
    assert buf.is_empty() and p.flushed[0][0][("user", "1")].points == 100


# ── 在飞窗口:批已 swap 出(缓冲空)但未 commit → barrier 不得提前放行;commit 后达成 ──
async def test_barrier_waits_for_in_flight_batch():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister()
    p.gate = asyncio.Event()  # 卡住 commit,制造在飞
    w = _writer(buf, p, flush_interval_s=0.002)
    task = asyncio.create_task(w.run())
    try:
        for _ in range(100):  # 等 run 进 flush(批已 swap、缓冲空、卡 gate)
            if p.attempts == 1 and buf.is_empty():
                break
            await asyncio.sleep(0.002)
        assert p.attempts == 1 and buf.is_empty()
        bar = asyncio.create_task(w.barrier(timeout_s=2.0))
        await asyncio.sleep(0.02)
        assert not bar.done()  # 缓冲虽空但在飞未落 → 不放行(快路径被 _in_flight 挡住)
        p.gate.set()  # commit 放行 → 本批落库;bar 的等待者由下一轮 flush_once(缓冲空)达成
        assert await asyncio.wait_for(bar, timeout=2.0) is True
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── 失败回灌后重试成功:等待者跨轮存活,最终 True ──
async def test_barrier_survives_retry_then_true():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister(fail_times=1)  # 第一轮失败回灌,第二轮成功
    w = _writer(buf, p, flush_interval_s=0.002, max_retry=5)
    ok = await _with_run(w, asyncio.wait_for(w.barrier(timeout_s=2.0), timeout=3.0))
    assert ok is True and buf.is_empty() and p.flushed


# ── 毒丸丢批:等待者 resolve False(数据已灭,fail-closed)──
async def test_barrier_false_on_poison_batch():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister(fail_times=99)
    w = _writer(buf, p, flush_interval_s=0.002, max_retry=2)
    ok = await _with_run(w, asyncio.wait_for(w.barrier(timeout_s=2.0), timeout=3.0))
    assert ok is False and buf.is_empty()  # 批被毒丸丢弃,屏障如实报失败


# ── 超时:落库永远卡住 → False(等待者被摘除,不泄漏)──
async def test_barrier_false_on_timeout():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister()
    p.gate = asyncio.Event()  # 永不放行
    w = _writer(buf, p, flush_interval_s=0.002)
    ok = await _with_run(w, w.barrier(timeout_s=0.05))
    assert ok is False


# ── 超时摘除:写者根本没跑(卡死/已死,N3 形态)→ 多次超时不得在 _waiters 泄漏已取消 future(0073 复审补钉)──
async def test_barrier_timeout_removes_waiter_when_writer_dead():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    w = _writer(buf, FakePersister())  # 不起 run():等待者只能靠超时路径摘除
    assert await w.barrier(timeout_s=0.01) is False
    assert await w.barrier(timeout_s=0.01) is False
    assert w._waiters == []  # 摘除逻辑坏掉则这里累积两个已取消 future(无界泄漏)


# ── 毒丸 × 在飞登记(0073 复审抓修):等待者在毒丸批 await 期间登记 → 也必须 False,
#    不得被下一轮空缓冲 flush 误 resolve True(数据已灭而 DB 未追平 = N1 复活)──
async def test_barrier_false_when_registered_during_poisoned_flight():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister(fail_times=99)
    p.gate = asyncio.Event()  # 卡住 commit,让 barrier 有窗口在飞行期间登记
    w = _writer(buf, p, flush_interval_s=0.002, max_retry=1)  # 单次失败即毒丸
    task = asyncio.create_task(w.run())
    try:
        for _ in range(100):  # 等 run 进 flush(批已 swap、在飞、卡 gate)
            if p.attempts == 1 and buf.is_empty():
                break
            await asyncio.sleep(0.002)
        assert p.attempts == 1 and buf.is_empty()
        bar = asyncio.create_task(w.barrier(timeout_s=2.0))
        await asyncio.sleep(0.02)
        assert not bar.done()  # 在飞窗口:已登记、未放行
        p.gate.set()  # 放行 → flush 抛错 → streak=1=max_retry → 毒丸丢批
        assert await asyncio.wait_for(bar, timeout=2.0) is False  # 修复前:下一轮空缓冲误 True
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── 写者停止:挂着的等待者统一 False(不悬死 Receiver)──
async def test_barrier_false_when_writer_cancelled():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    p = FakePersister()
    p.gate = asyncio.Event()  # 卡住,使 cancel 落在 flush 半途
    w = _writer(buf, p, flush_interval_s=0.002)
    run_task = asyncio.create_task(w.run())
    bar = asyncio.create_task(w.barrier(timeout_s=5.0))
    for _ in range(100):
        if p.attempts == 1:
            break
        await asyncio.sleep(0.002)
    run_task.cancel()  # 写者停止(关闭):flush_once 回灌 + 交还等待者,run finally 统一 False
    try:
        await run_task
    except asyncio.CancelledError:
        pass
    assert await asyncio.wait_for(bar, timeout=1.0) is False
    assert not buf.is_empty()  # 在飞批已回灌,交 lifespan.stop 的 drain 补落(0025 语义不变)


# ── GameLoop.run 补 task_done:inbox.join() 在已入队命令处理完后返回(载入屏障第①步的前提)──
async def test_gameloop_task_done_unblocks_inbox_join():
    inbox: "asyncio.Queue" = asyncio.Queue()
    world = World()
    gl = GameLoop(world, inbox, Dispatcher(world, ConnectionManager(), WriteBuffer(), Timer(inbox), inbox))
    task = asyncio.create_task(gl.run())
    try:
        await inbox.put(Connect(origin=None, nick="ghost"))  # 大厅 no-op 命令
        await asyncio.wait_for(inbox.join(), timeout=1.0)  # task_done 后返回;缺 task_done 则悬死在此
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── 0072·N1 主钉(e2e,修复前必红):离房退分未落库 → 立即重进 → 载入不得回退积分 ──
async def test_n1_rejoin_within_flush_window_keeps_points():
    engine = make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    shell = DevShell(engine=engine)
    await shell.setup()
    # 换大周期写者:自然 flush 永不来,唯 barrier 能触发落库——确定性复现「flush 窗口内重进」。
    shell.persistwriter = PersistWriter(
        shell.persist, OrmPersister(shell.sessionmaker), flush_interval_s=999, drain_timeout_s=2.0
    )
    gl = asyncio.create_task(shell.gameloop.run())
    pw = asyncio.create_task(shell.persistwriter.run())
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(
        run_receiver(
            conn, shell.conns, shell.inbox, shell.timer, shell.sessionmaker, shell.world, shell.persist,
            persistwriter=shell.persistwriter,  # 载入屏障接线(与 lifespan 生产路同形)
        )
    )
    start = gameconfig.DEV_START_POINTS
    try:
        await asyncio.sleep(0)
        conn.ws.feed('{"type":"join_room","room":"dev"}')  # 首进:载入 start
        await _settle(lambda: "alice" in shell.world.users)
        conn.ws.feed('{"type":"sit_down","seat":0}')
        conn.ws.feed('{"type":"buy_in","seat":0,"amount":100}')  # 全局 start-100,座位 100
        await _settle(lambda: shell.world.users.get("alice") is not None and shell.world.users["alice"].points == start - 100)
        await shell.persistwriter.flush_once()  # 使 DB 定格在「买入已扣」= start-100(制造滞后基线)
        async with shell.sessionmaker() as s:
            assert (await s.get(User, 1)).points == start - 100
        # 离房:退座位筹码回全局(PointsWrite(start) 只进缓冲、不 flush)→ 紧跟同连接立即重进。
        conn.ws.feed('{"type":"leave_room"}')
        conn.ws.feed('{"type":"join_room","room":"dev"}')  # 修复点:载入屏障令 DB 先追平再读
        await _settle(lambda: "alice" in shell.world.users and shell.world.users["alice"].points == start)
        assert shell.world.users["alice"].points == start  # 修复前:读到滞后 DB 的 start-100,静默丢 100
        async with shell.sessionmaker() as s:
            assert (await s.get(User, 1)).points == start  # barrier 已把退分落库
    finally:
        for t in (rx, gl, pw):
            t.cancel()
        for t in (rx, gl, pw):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await engine.dispose()


async def _settle(cond, timeout: float = 2.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.005)
    return cond()


# ── 生产接线钉(0073 复审补,变异体③杀测):两个 ws 端点必须把 shell.persistwriter 传给 run_receiver;
#    N1 e2e 自己接线不经端点,删掉 lifespan 传参此前 707 仍绿——此测直钉端点 handler 的实参 ──
async def test_ws_endpoints_wire_persistwriter(monkeypatch):
    import time as _time

    from app.shell import lifespan as lifespan_mod

    captured: list[dict] = []

    async def _capture_run_receiver(conn, conns, inbox, timer, sessionmaker, world, persist, persistwriter=None):
        captured.append({"nick": conn.nick, "persistwriter": persistwriter})

    async def _fake_load(sessionmaker, nick):
        return (1, gameconfig.DEV_START_POINTS)  # 绕开真 DB(端点前置的行存在性检查)

    monkeypatch.setattr(lifespan_mod, "run_receiver", _capture_run_receiver)
    monkeypatch.setattr(lifespan_mod, "load_user_by_nick", _fake_load)
    app = lifespan_mod.create_app()
    shell = app.state.shell
    dev_ep = next(r for r in app.routes if getattr(r, "path", None) == "/dev/ws").endpoint
    sec_ep = next(r for r in app.routes if getattr(r, "path", None) == "/ws").endpoint
    await dev_ep(FakeWS(), nick="alice")  # 明文 dev 端点
    sid, _session = shell.session_store.create("alice", "alice", _time.time())
    await sec_ep(FakeWS(), sid=sid)  # 加密端点
    assert len(captured) == 2
    for c in captured:
        assert c["persistwriter"] is shell.persistwriter and c["persistwriter"] is not None  # 生产必传


# ── stop-drain 计数钉(0073 复审补,变异体④杀测):stop() 排空后 inbox 计数配平,join() 立即返回;
#    漏 task_done 则 join 悬死(关闭窗口内 Receiver 的载入屏障①会白等满超时)──
async def test_stop_drain_balances_inbox_join():
    engine = make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    shell = DevShell(engine=engine)
    await shell.setup()
    shell.inbox.put_nowait(Connect(origin=None, nick="alice"))  # 大厅 no-op,只为计数
    await shell.stop()  # 排空:handle + task_done(0073)
    assert shell.inbox.empty()
    await asyncio.wait_for(shell.inbox.join(), timeout=0.1)  # 计数未配平则悬死于此
    await engine.dispose()


# ── 0072·N1 跨连接变体(0073 复审补):断线 → Cleanup 驱逐退分(只进缓冲)→ 新连接同 nick 重进,
#    积分不得回退。与同连接主钉的差别:驱逐命令来自 Timer,重进走第二条 Receiver ──
async def test_n1_cleanup_evict_then_rejoin_keeps_points():
    engine = make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    shell = DevShell(engine=engine)
    await shell.setup()
    shell.persistwriter = PersistWriter(
        shell.persist, OrmPersister(shell.sessionmaker), flush_interval_s=999, drain_timeout_s=2.0
    )
    gl = asyncio.create_task(shell.gameloop.run())
    pw = asyncio.create_task(shell.persistwriter.run())
    conn1 = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx1 = asyncio.create_task(
        run_receiver(
            conn1, shell.conns, shell.inbox, shell.timer, shell.sessionmaker, shell.world, shell.persist,
            persistwriter=shell.persistwriter,
        )
    )
    start = gameconfig.DEV_START_POINTS
    rx2 = None
    try:
        await asyncio.sleep(0)
        conn1.ws.feed('{"type":"join_room","room":"dev"}')
        await _settle(lambda: "alice" in shell.world.users)
        conn1.ws.feed('{"type":"sit_down","seat":0}')
        conn1.ws.feed('{"type":"buy_in","seat":0,"amount":100}')
        await _settle(lambda: "alice" in shell.world.users and shell.world.users["alice"].points == start - 100)
        await shell.persistwriter.flush_once()  # DB 定格在「买入已扣」= start-100
        await conn1.ws.close()  # 断线:Receiver 退出清理 → arm_cleanup + 投 Disconnect(标 OFFLINE 保座)
        await _settle(lambda: shell.conns.get("alice") is None)
        shell.timer._liveness["alice"] = 0.0  # 占座窗口即刻到期(免等 LIVENESS_TIMEOUT)
        shell.timer.tick()  # 投 Cleanup → reduce 驱逐:退分 PointsWrite(start) 只进缓冲、不 flush
        await _settle(lambda: "alice" not in shell.world.users)
        conn2 = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
        rx2 = asyncio.create_task(
            run_receiver(
                conn2, shell.conns, shell.inbox, shell.timer, shell.sessionmaker, shell.world, shell.persist,
                persistwriter=shell.persistwriter,
            )
        )
        await asyncio.sleep(0)
        conn2.ws.feed('{"type":"join_room","room":"dev"}')  # 屏障令退分先落库再读
        await _settle(lambda: "alice" in shell.world.users and shell.world.users["alice"].points == start)
        assert shell.world.users["alice"].points == start  # 修复前:读到滞后 DB 的 start-100
    finally:
        tasks = [t for t in (rx1, rx2, gl, pw) if t is not None]
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        await engine.dispose()
