# lifespan:dev shell 装配 + 明文 ws 端点(见 connection.md「lifespan」,最小 dev 版)。
# dev-only:无鉴权 / 无加密;接真 async DB(sqlite+aiosqlite)——幂等种子 dev 用户进 DB + OrmPersister 落库。
# 用户连接 → 进大厅 → 主动 join_room → Receiver 读 DB 载入(per-join,0030);dev 房空预置。
# 运行:cd service && .venv/bin/uvicorn app.shell.lifespan:app  → ws://<host>/dev/ws?nick=alice
# P5 国密信道落地即替换握手/帧;P8 lifespan drain 收尾。

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app import gameconfig
from app.core.commands import Command
from app.core.domain import Room, World
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.db.orm_persister import OrmPersister
from app.shell.connection import Connection, ConnectionManager
from app.shell.dispatch import Dispatcher
from app.shell.gameloop import GameLoop
from app.shell.history import RoomChatBuffer
from app.shell.logsetup import setup_logging
from app.shell.persist import PersistWriter, WriteBuffer
from app.shell.receiver import run_receiver
from app.shell.timer import Timer

log = logging.getLogger(__name__)


def _dev_uid(index: int) -> int:
    return index + 1  # dev 用户主键 = 预置序号 + 1(id 自 1 起,避免 0;种子/载入一致)


async def seed_dev_users(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    # 幂等种子:dev 用户进 DB(id=序号+1 / nickname / points=DEV_START_POINTS)。仅当该 id 不在才 INSERT——
    # 重启不重置已落库积分(OrmPersister 写回的最新值得以保留)。原型注册(P5)未建,此为 dev 替身。
    async with sessionmaker() as session:
        async with session.begin():
            existing = set((await session.execute(select(User.id))).scalars())
            for i, nick in enumerate(gameconfig.DEV_USERS):
                uid = _dev_uid(i)
                if uid not in existing:
                    session.add(User(id=uid, nickname=nick, points=gameconfig.DEV_START_POINTS))


def build_dev_world() -> World:
    # 预置一个**空** dev 房(房须存在供 JoinRoom 的 NO_SUCH_ROOM 校验,但不预置任何用户)。
    # 用户连接 → 进大厅(Connect no-op)→ 主动 join_room{"dev"} → Receiver 读 DB 载入(per-join,0030)。
    room = Room(
        seats=[None] * gameconfig.DEV_SEATS,
        small_blind=gameconfig.DEV_SMALL_BLIND,
        buy_in=gameconfig.DEV_BUY_IN,
    )
    return World(rooms={gameconfig.DEV_ROOM: room}, users={})


class DevShell:
    # 持有 dev shell 全部组件/协程。__init__ 只建脱 IO 部件;setup() 异步建表/种子/载入 + 建 world 依赖部件。
    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self.engine = engine if engine is not None else make_engine()
        self.sessionmaker = make_sessionmaker(self.engine)
        self.inbox: "asyncio.Queue[Command]" = asyncio.Queue(maxsize=gameconfig.INBOX_MAX)
        self.conns = ConnectionManager()
        self.persist = WriteBuffer()
        # 接真 OrmPersister(替 NullPersister):周期 swap → 落 DB;stop 时 drain。
        self.persistwriter = PersistWriter(self.persist, OrmPersister(self.sessionmaker))
        self.timer = Timer(self.inbox)
        self.history = RoomChatBuffer()  # 房聊环形缓冲:dispatch 写 / Receiver 的 FetchRoomChat 读
        # world 及其依赖(dispatcher/gameloop)在 setup() 从 DB 载入后建。
        self.world: World | None = None
        self.dispatcher: Dispatcher | None = None
        self.gameloop: GameLoop | None = None
        self._tasks: list[asyncio.Task] = []

    async def setup(self) -> None:
        # 异步启动:建表(dev 引导,无 Alembic)→ 幂等种子 dev 用户进 DB(供 join_room 载入)→ 建空 world + dispatcher + gameloop。
        await create_all(self.engine)
        await seed_dev_users(self.sessionmaker)
        self.world = build_dev_world()
        self.dispatcher = Dispatcher(self.world, self.conns, self.persist, self.timer, self.inbox, self.history)
        self.gameloop = GameLoop(self.world, self.inbox, self.dispatcher)

    def start(self) -> None:
        # 起 GameLoop + Timer + PersistWriter(须先 await setup() 建好 gameloop)。
        assert self.gameloop is not None, "DevShell.start() 前须先 await setup()"
        self._tasks = [
            asyncio.create_task(self.gameloop.run(), name="gameloop"),
            asyncio.create_task(self.timer.run(), name="timer"),
            asyncio.create_task(self.persistwriter.run(), name="persistwriter"),
        ]

    async def stop(self) -> None:
        # 关闭序(db.md drain):先 cancel 生产者(gameloop/timer)+ writer 周期循环 → 不再产新写;
        # 再 await PersistWriter.drain() 终结 flush(cancel 若落在 flush 半途,flush_once 先回灌再 re-raise,drain 补落);
        # 最后 dispose engine 释放连接池。
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:  # 协程意外死亡:记下但不阻断关闭(尽力 drain)
                log.exception("task %s crashed during shutdown", t.get_name())
        await self.persistwriter.drain()  # 终结 flush 残余缓冲(OrmPersister 落 DB)
        await self.engine.dispose()  # 关连接池


def create_app() -> FastAPI:
    shell = DevShell()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(gameconfig.LOG_LEVEL, gameconfig.LOG_FORMAT, gameconfig.LOG_FILE)  # 启动序:先配日志(connection.md)
        await shell.setup()  # 异步建表 + 种子 + 载入 + 建 world/gameloop(serving 前完成)
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
        await run_receiver(conn, shell.conns, shell.inbox, shell.timer, shell.sessionmaker, shell.history)

    return app


app = create_app()  # uvicorn 入口:app.shell.lifespan:app
