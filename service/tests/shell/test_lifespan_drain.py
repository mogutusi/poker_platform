"""P8:DevShell 关闭反序 drain(connection.md:177-180 / changes/0046)集成——stop() 端到端把缓冲落进 DB。

`PersistWriter.drain()` 本体(超时 CRITICAL / 毒丸 / 取消回灌)已在 test_persist_writer 穷举;此处验 **lifespan 编排**:
排空 inbox 在途命令 + 终结 drain 落 DB + start→stop 不挂死。用**文件 sqlite**(stop 会 dispose 原 engine,事后开新 engine 验)。
"""

import asyncio
import logging

import pytest
from sqlalchemy.pool import StaticPool

from app import gameconfig
from app.core.commands import BuyIn, JoinRoom, RoomCreate, SitDown
from app.core.records import PointsWrite
from app.db.engine import make_engine, make_sessionmaker
from app.db.models import User
from app.shell.connection import Connection
from app.shell import lifespan as lifespan_mod
from app.shell.lifespan import DevShell
from tests.shell._fakes import FakeWS


def _url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path}/drain.db"


async def _points(tmp_path, uid: int) -> int:
    # 事后用新 engine 读文件 DB(stop 已 dispose 原 engine);读完即 dispose,不泄漏连接。
    eng = make_engine(_url(tmp_path))
    try:
        async with make_sessionmaker(eng)() as s:
            return (await s.get(User, uid)).points
    finally:
        await eng.dispose()


async def test_stop_drains_inflight_inbox_commands_to_db(tmp_path):
    # ② + ③:排队在 inbox 的 join/sit/buy(未 start gameloop,未处理)→ stop() 排空处理 + drain 落 DB。
    shell = DevShell(engine=make_engine(_url(tmp_path)))
    await shell.setup()
    amount = 70
    shell.inbox.put_nowait(
        JoinRoom(
            origin="alice", room=gameconfig.DEV_ROOM, uid=1, loaded=gameconfig.DEV_START_POINTS,
            create=RoomCreate(gameconfig.DEV_SMALL_BLIND, gameconfig.DEV_BUY_IN, gameconfig.DEV_SEATS, gameconfig.ROOM_CHAT_HISTORY_SIZE),
        )
    )
    shell.inbox.put_nowait(SitDown(origin="alice", seat=0))
    shell.inbox.put_nowait(BuyIn(origin="alice", seat=0, amount=amount))
    await shell.stop()
    assert shell.inbox.empty()  # 在途命令被排空(spec「在途命令处理完」)
    assert await _points(tmp_path, 1) == gameconfig.DEV_START_POINTS - amount  # 买入 PointsWrite 经 drain 落 DB


async def test_stop_flushes_already_buffered_writes_to_db(tmp_path):
    # ③ 隔离:写已在缓冲(直接 put)→ stop() drain 落 DB(不依赖 inbox 排空那条路径)。
    shell = DevShell(engine=make_engine(_url(tmp_path)))
    await shell.setup()
    shell.persist.put(PointsWrite(uid=1, points=123))
    assert not shell.persist.is_empty()  # 前置:缓冲非空
    await shell.stop()
    assert shell.persist.is_empty()  # drain 清空缓冲
    assert await _points(tmp_path, 1) == 123  # 状态写落 DB


async def test_start_then_stop_completes_without_hang(tmp_path):
    # 起 gameloop/timer/persistwriter task → stop() 反序 cancel + drain + dispose,不挂死(drain 有界)。
    shell = DevShell(engine=make_engine(_url(tmp_path)))
    await shell.setup()
    shell.start()
    await asyncio.sleep(0)  # 让 task 起来
    await asyncio.wait_for(shell.stop(), timeout=5.0)  # 不挂死


async def test_stop_without_start_is_safe(tmp_path):
    # 未 start(task 引用全 None)直接 stop:cancel None 无碍、空 inbox 排空 0、drain 空缓冲、dispose 正常。
    shell = DevShell(engine=make_engine(_url(tmp_path)))
    await shell.setup()
    await shell.stop()  # 不抛


async def test_stop_cancels_registered_senders(tmp_path):
    # ④:stop 遍历 conns.online_nicks() cancel 各连接的 sender_task(best-effort 兜底)。
    shell = DevShell(engine=make_engine(_url(tmp_path)))
    await shell.setup()
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    conn.sender_task = asyncio.create_task(asyncio.sleep(3600))  # 模拟长跑 Sender
    shell.conns.register(conn)
    await shell.stop()
    try:
        await conn.sender_task  # stop 只 cancel 不 await;让 cancellation 落定
    except asyncio.CancelledError:
        pass
    assert conn.sender_task.cancelled()  # 被 stop ④ cancel


async def test_stop_drains_concurrently_with_live_gameloop_exactly_once(tmp_path):
    # 并发交接:gameloop task 在跑(start)+ inbox 有排队命令 + stop() 竞 cancel。无论命令由 gameloop 还是
    # drain 处理,每条恰处理一次(Queue 取走即移除)——终态 DB 积分 = 一次买入,既不丢也不重(钉死 0046 交接不变量)。
    shell = DevShell(engine=make_engine(_url(tmp_path)))
    await shell.setup()
    shell.start()
    amount = 55
    # 同步连投三条(put_nowait 无 await ⇒ 投完前 gameloop 不运行;await stop 时才竞):
    shell.inbox.put_nowait(
        JoinRoom(
            origin="alice", room=gameconfig.DEV_ROOM, uid=1, loaded=gameconfig.DEV_START_POINTS,
            create=RoomCreate(gameconfig.DEV_SMALL_BLIND, gameconfig.DEV_BUY_IN, gameconfig.DEV_SEATS, gameconfig.ROOM_CHAT_HISTORY_SIZE),
        )
    )
    shell.inbox.put_nowait(SitDown(origin="alice", seat=0))
    shell.inbox.put_nowait(BuyIn(origin="alice", seat=0, amount=amount))
    await asyncio.wait_for(shell.stop(), timeout=5.0)
    assert shell.inbox.empty()
    assert await _points(tmp_path, 1) == gameconfig.DEV_START_POINTS - amount  # 恰一次(非 -2×amount / 非未扣)


# ── 关闭路径的两处失守(changes/0083 / BUG-5)+ 常驻协程 watchdog(BUG-7)──

async def test_cancel_and_await_propagates_cancellation_aimed_at_stop_itself(tmp_path):
    # 此前 `_cancel_and_await` 一律吞 CancelledError,连「取消是冲 stop() 自己来的」也吞:
    # 上层再怎么设关闭超时、强制中止,stop() 都赖着不走。必须区分两种取消。
    shell = DevShell(engine=make_engine(_url(tmp_path)))
    swallowed = asyncio.Event()
    release = asyncio.Event()

    async def stubborn():  # 吞掉第一次取消、迟迟不结束的子任务(卡住的 flush / 慢 Sender)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            swallowed.set()
            await release.wait()

    child = asyncio.create_task(stubborn(), name="stubborn")
    await asyncio.sleep(0)
    outer = asyncio.create_task(shell._cancel_and_await(child))
    await asyncio.wait_for(swallowed.wait(), timeout=1.0)  # outer 已卡在 `await child` 上
    outer.cancel()  # 模拟「关闭超时到了,中止 stop()」
    with pytest.raises(asyncio.CancelledError):
        await outer  # 取消必须真的生效,而不是被吞掉后照常返回
    release.set()
    # 注:cancel 一个正 `await` 别的 task 的 task,asyncio 会连它等的那个 future 一起 cancel
    # ⇒ child 最终也是 cancelled。这正是「只看 `t.cancelled()`」不足以判别的那个真实场景
    # (那时 t 确实是 cancelled,却不代表取消不是冲我来的),故判据里必须有 `cancelling()`。
    await asyncio.gather(child, return_exceptions=True)
    await shell.engine.dispose()


async def test_lifespan_stops_shell_even_when_shutdown_raises(monkeypatch):
    # lifespan 的 `yield` 此前裸着:关闭路径上一旦抛异常或被取消,shell.stop() 被整体跳过
    # → drain 不执行,写缓冲里未落库的积分全丢、engine 连接池泄漏。关闭必须无条件跑到 stop()。
    class _StubShell(DevShell):
        def __init__(self) -> None:
            super().__init__(
                engine=make_engine(
                    "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
                )
            )
            self.stopped = False

        def start(self) -> None:
            pass  # 不起常驻协程:本用例只验关闭编排

        async def stop(self) -> None:
            self.stopped = True
            await self.engine.dispose()

    built: list[_StubShell] = []

    def factory() -> _StubShell:
        shell = _StubShell()
        built.append(shell)
        return shell

    monkeypatch.setattr(lifespan_mod, "DevShell", factory)
    monkeypatch.setattr(lifespan_mod, "setup_logging", lambda *a, **k: None)  # 别把测试进程的日志配置改掉
    app = lifespan_mod.create_app()
    shell = built[0]
    cm = app.router.lifespan_context(app)
    await cm.__aenter__()
    # contextlib 的 __aexit__ 对「原样再抛出去的同一个异常」返回 False(交由调用方 async with 上抛),
    # 不自己 raise;所以这里断言的是「没被吞掉」+「stop() 确实跑过」。
    suppressed = await cm.__aexit__(RuntimeError, RuntimeError("shutdown kaboom"), None)
    assert suppressed is False  # 异常照常上抛
    assert shell.stopped is True  # 但 stop() 必已跑过


async def test_watchdog_reports_dead_resident_task_but_stays_silent_on_cancel(caplog):
    # 常驻协程(GameLoop/Timer/PersistWriter)非取消而退出 = 「进程还在、ws 还连着,但状态机哑了」,
    # 是最难察觉的故障,必须留 CRITICAL;正常关闭走 cancel,不该落噪声。
    async def dies():
        raise RuntimeError("loop died")

    with caplog.at_level(logging.CRITICAL):
        dead = asyncio.create_task(dies(), name="gameloop")
        dead.add_done_callback(lifespan_mod._watchdog)
        with pytest.raises(RuntimeError):
            await dead
        await asyncio.sleep(0)  # done-callback 走 call_soon
        assert any("gameloop" in r.getMessage() for r in caplog.records)

        caplog.clear()
        cancelled = asyncio.create_task(asyncio.sleep(3600), name="timer")
        cancelled.add_done_callback(lifespan_mod._watchdog)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        await asyncio.sleep(0)
        assert caplog.records == []  # 关闭时的 cancel 不落 CRITICAL


async def test_start_attaches_watchdog_to_every_resident_task(monkeypatch, tmp_path):
    # 接线钉:watchdog 本体正确没用,得真挂上去。三条常驻协程一条都不能漏。
    monkeypatch.setattr(lifespan_mod, "_watchdog", lambda t: None)
    shell = DevShell(engine=make_engine(_url(tmp_path)))
    await shell.setup()
    shell.start()
    try:
        for task in (shell._gameloop_task, shell._timer_task, shell._persistwriter_task):
            assert task is not None
            assert task.remove_done_callback(lifespan_mod._watchdog) == 1, f"{task.get_name()} 未挂 watchdog"
    finally:
        await shell.stop()
