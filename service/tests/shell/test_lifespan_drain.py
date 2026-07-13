"""P8:DevShell 关闭反序 drain(connection.md:177-180 / changes/0046)集成——stop() 端到端把缓冲落进 DB。

`PersistWriter.drain()` 本体(超时 CRITICAL / 毒丸 / 取消回灌)已在 test_persist_writer 穷举;此处验 **lifespan 编排**:
排空 inbox 在途命令 + 终结 drain 落 DB + start→stop 不挂死。用**文件 sqlite**(stop 会 dispose 原 engine,事后开新 engine 验)。
"""

import asyncio

from app import gameconfig
from app.core.commands import BuyIn, JoinRoom, RoomCreate, SitDown
from app.core.records import PointsWrite
from app.db.engine import make_engine, make_sessionmaker
from app.db.models import User
from app.shell.connection import Connection
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
