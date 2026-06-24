# lifespan:dev shell 装配 + 明文 ws 端点(见 connection.md「lifespan」,最小 dev 版)。
# dev-only:无鉴权 / 无加密 / 无 DB(persist 桩);预置一个 dev 房 + dev 用户绕开延后的 JoinRoom。
# 运行:cd service && .venv/bin/uvicorn app.shell.lifespan:app  → ws://<host>/dev/ws?nick=alice
# P5 国密信道落地即替换握手/帧;P4 接 PersistWriter + drain;P8 lifespan drain 收尾。

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, WebSocket

from app import gameconfig
from app.core.commands import Command
from app.core.domain import Room, UserState, World
from app.core.enums import UserStatus
from app.shell.connection import Connection, ConnectionManager
from app.shell.dispatch import Dispatcher
from app.shell.gameloop import GameLoop
from app.shell.persist import NullPersister, PersistWriter, WriteBuffer
from app.shell.receiver import run_receiver
from app.shell.timer import Timer

log = logging.getLogger(__name__)


def build_dev_world() -> World:
    # 预置 dev 房 + dev 用户(WATCHING 在房),使 sit/buy/ready/start/action 无需 JoinRoom(见 changes/0018)。
    room = Room(
        seats=[None] * gameconfig.DEV_SEATS,
        small_blind=gameconfig.DEV_SMALL_BLIND,
        buy_in=gameconfig.DEV_BUY_IN,
    )
    users: dict[str, UserState] = {}
    for i, nick in enumerate(gameconfig.DEV_USERS):
        users[nick] = UserState(
            uid=i + 1, nickname=nick, points=gameconfig.DEV_START_POINTS, room=gameconfig.DEV_ROOM
        )
        room.users_in_room[nick] = UserStatus.WATCHING
    return World(rooms={gameconfig.DEV_ROOM: room}, users=users)


class DevShell:
    # 持有 dev shell 全部组件/协程,供 lifespan 启停 + 端点引用。
    def __init__(self) -> None:
        self.world = build_dev_world()
        self.inbox: "asyncio.Queue[Command]" = asyncio.Queue(maxsize=gameconfig.INBOX_MAX)
        self.conns = ConnectionManager()
        self.persist = WriteBuffer()
        # dev 无 DB → NullPersister(丢弃 + 日志);PersistWriter 仍周期 swap 清空缓冲、stop 时 drain。
        # P4 三接真 OrmPersister(to_orm + session)替 NullPersister。
        self.persistwriter = PersistWriter(self.persist, NullPersister())
        self.timer = Timer(self.inbox)
        self.dispatcher = Dispatcher(self.world, self.conns, self.persist, self.timer, self.inbox)
        self.gameloop = GameLoop(self.world, self.inbox, self.dispatcher)
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        # 起 GameLoop + Timer + PersistWriter(dev 无加密;persister 为 NullPersister)。
        self._tasks = [
            asyncio.create_task(self.gameloop.run(), name="gameloop"),
            asyncio.create_task(self.timer.run(), name="timer"),
            asyncio.create_task(self.persistwriter.run(), name="persistwriter"),
        ]

    async def stop(self) -> None:
        # 关闭序(db.md drain):先 cancel 生产者(gameloop/timer)+ writer 周期循环 → 不再产新写;
        # 再 await PersistWriter.drain() 终结 flush。cancel 若落在 writer 的 flush 半途,flush_once 会先回灌
        # 再 re-raise,故那批写仍由随后的 drain 补落(不丢);drain 在写者 task 收割后单线跑,无并发竞 swap。
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:  # 协程意外死亡(如 inbox 满已是 CRITICAL 态):记下但不阻断关闭(尽力 drain)
                log.exception("task %s crashed during shutdown", t.get_name())
        await self.persistwriter.drain()  # 终结 flush 残余缓冲(dev NullPersister:丢弃 + 日志)


def create_app() -> FastAPI:
    shell = DevShell()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        shell.start()
        log.info("dev shell up: room=%s users=%s", gameconfig.DEV_ROOM, gameconfig.DEV_USERS)
        yield
        await shell.stop()

    app = FastAPI(lifespan=lifespan, title="poker dev shell (plaintext, dev-only)")
    app.state.shell = shell

    @app.websocket("/dev/ws")
    async def dev_ws(ws: WebSocket, nick: str = Query(...)):  # type: ignore[valid-type]
        # dev 明文握手:?nick= 必须是预置 dev 用户(连接绑 nick,模型 2)。无 MAC / 无加密(P5 替换)。
        await ws.accept()
        if nick not in gameconfig.DEV_USERS:
            await ws.close(code=4404)  # 未知 dev 用户:拒,不建 Connection
            return
        conn = Connection.create(nick=nick, session_id=nick, ws=ws)
        await run_receiver(conn, shell.conns, shell.inbox, shell.timer)

    return app


app = create_app()  # uvicorn 入口:app.shell.lifespan:app
