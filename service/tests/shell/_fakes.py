"""shell 测试桩:fake ws + 连接/壳装配助手(只验接线与保序,不重测 core,见 testing.md)。"""

import asyncio

from app.core.commands import Command
from app.shell.connection import Connection, ConnectionManager
from app.shell.dispatch import Dispatcher
from app.shell.gameloop import GameLoop
from app.shell.persist import WriteBuffer
from app.shell.timer import Timer


_WS_CLOSED = object()  # 哨兵:close() 喂入,唤醒阻塞的 receive_text → 抛错(模拟 WebSocketDisconnect)


class FakeWS:
    # 最小 ws 替身:记录发出的文本/二进制帧、可喂入收到的帧、可关闭(close 唤醒并断开 receive)。
    # 明文路用 text(feed/sent),加密路用 bytes(feed_bytes/sent_bytes);同一连接只走一路。
    def __init__(self) -> None:
        self.sent: list[str] = []  # send_text 记录(明文 dev 帧)
        self.sent_bytes: list[bytes] = []  # send_bytes 记录(加密二进制帧)
        self._inbox: "asyncio.Queue" = asyncio.Queue()
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True  # ws 握手接受(端点先 accept 再鉴权)

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def receive_text(self) -> str:
        item = await self._inbox.get()
        if item is _WS_CLOSED:
            raise RuntimeError("ws closed")  # 真实 ws 此处抛 WebSocketDisconnect → Receiver 退出
        return item

    async def receive_bytes(self) -> bytes:
        item = await self._inbox.get()
        if item is _WS_CLOSED:
            raise RuntimeError("ws closed")
        return item

    def feed(self, text: str) -> None:
        self._inbox.put_nowait(text)  # 测试侧:模拟客户端发来一明文帧

    def feed_bytes(self, data: bytes) -> None:
        self._inbox.put_nowait(data)  # 测试侧:模拟客户端发来一加密帧

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code
        self._inbox.put_nowait(_WS_CLOSED)  # 唤醒阻塞的 receive_*,使其报错退出


def make_conn(nick: str) -> Connection:
    return Connection.create(nick=nick, session_id=nick, ws=FakeWS())


def drain(conn: Connection) -> list:
    # 取空某连接 outbound,返回已入队的 ServerMessage 列表(同步,无需 loop)。
    out = []
    while not conn.outbound.empty():
        out.append(conn.outbound.get_nowait())
    return out


class Shell:
    # 把 inbox/conns/persist/timer/dispatcher/gameloop 装一处,供测试驱动 gameloop.handle(cmd)。
    def __init__(self, world, *, inbox_maxsize: int = 0) -> None:
        # inbox_maxsize=0 → 无界(默认,既有测试不触发背压);>0 → 有界,供「inbox 满」背压/丢连测试。
        self.inbox: "asyncio.Queue[Command]" = asyncio.Queue(maxsize=inbox_maxsize)
        self.conns = ConnectionManager()
        self.persist = WriteBuffer()
        self.world = world  # 供 Receiver 只读(FetchRoomChat 读房内聊天历史,0071)
        self.timer = Timer(self.inbox)
        self.dispatcher = Dispatcher(world, self.conns, self.persist, self.timer, self.inbox)
        self.gameloop = GameLoop(world, self.inbox, self.dispatcher)

    def connect(self, *nicks: str) -> dict[str, Connection]:
        conns = {n: make_conn(n) for n in nicks}
        for c in conns.values():
            self.conns.register(c)
        return conns

    def inbox_drain(self) -> list[Command]:
        out = []
        while not self.inbox.empty():
            out.append(self.inbox.get_nowait())
        return out
