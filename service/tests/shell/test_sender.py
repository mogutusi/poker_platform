"""Sender:per-connection 出站严格保序(同一连接按 enqueue 顺序发出,testing.md「保序」)。"""

import asyncio

from app.shell.sender import sender_loop
from app.wire.server import UserStatusChanged
from app.core.enums import UserStatus
from tests.shell._fakes import make_conn


def _msg(seat: int) -> UserStatusChanged:
    return UserStatusChanged(nickname="alice", status=UserStatus.SITTING_IN, seat_position=seat)


async def test_sender_preserves_enqueue_order():
    conn = make_conn("alice")
    for i in range(20):
        conn.outbound.put_nowait(_msg(i))  # 入队顺序 0..19
    task = asyncio.create_task(sender_loop(conn))
    while len(conn.ws.sent) < 20:  # 等 Sender 抽干队列
        await asyncio.sleep(0)
    task.cancel()
    # 发出顺序严格 == 入队顺序
    seats = [UserStatusChanged.model_validate_json(s).seat_position for s in conn.ws.sent]
    assert seats == list(range(20))


async def test_sender_exits_on_cancel():
    conn = make_conn("alice")
    task = asyncio.create_task(sender_loop(conn))
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done()
