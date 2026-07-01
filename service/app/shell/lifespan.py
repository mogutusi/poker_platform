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
from app.core.domain import World
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.db.orm_persister import OrmPersister
from app.auth.session import SessionStore
from app.rest.hands import make_hands_router
from app.rest.leaderboard import make_leaderboard_router
from app.rest.lobby import make_lobby_router
from app.rest.login import make_login_router
from app.shell.connection import Connection, ConnectionManager
from app.shell.dispatch import Dispatcher
from app.shell.gameloop import GameLoop
from app.shell.history import RoomChatBuffer
from app.shell.logsetup import setup_logging
from app.shell.persist import PersistWriter, WriteBuffer
from app.shell.presence import Presence
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
    # 空 world:无静态预置房(动态房——谁都可创建 / 空则消失,见 core.md 房间生命周期 / changes/0049)。
    # 用户连接 → 进大厅(Connect no-op)→ 主动 join_room{room} → Receiver 读 DB 载入 + 盖建房默认配置 →
    # 房不存在则 reduce 建房、加入;最后一人离开该房则销毁(per-join 载入 0030,动态房 0049)。
    return World()


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
        self.session_store = SessionStore(gameconfig.SESSION_TTL_SECONDS)  # ws 会话表:/user/login 铸,ws 握手查(P5)
        # world 及其依赖(dispatcher/gameloop)在 setup() 从 DB 载入后建。
        self.world: World | None = None
        self.dispatcher: Dispatcher | None = None
        self.gameloop: GameLoop | None = None
        self.presence: Presence | None = None  # 只读聚合(在线/在房/状态);供后续 lobby/DM/改昵称消费
        # 命名 task 引用:stop() 按关闭反序分阶段 cancel(生产者先于消费者 drain,见 connection.md / changes/0046)。
        self._gameloop_task: asyncio.Task | None = None
        self._timer_task: asyncio.Task | None = None
        self._persistwriter_task: asyncio.Task | None = None

    async def setup(self) -> None:
        # 异步启动:建表(dev 引导,无 Alembic)→ 幂等种子 dev 用户进 DB(供 join_room 载入)→ 建空 world + dispatcher + gameloop。
        await create_all(self.engine)
        await seed_dev_users(self.sessionmaker)
        self.world = build_dev_world()
        self.dispatcher = Dispatcher(self.world, self.conns, self.persist, self.timer, self.inbox, self.history)
        self.gameloop = GameLoop(self.world, self.inbox, self.dispatcher)
        self.presence = Presence(self.world, self.conns)  # 只读聚合,持稳定 world 引用(commit 原地改其 .users/.rooms)

    def start(self) -> None:
        # 起 GameLoop + Timer + PersistWriter(须先 await setup() 建好 gameloop)。
        assert self.gameloop is not None, "DevShell.start() 前须先 await setup()"
        self._gameloop_task = asyncio.create_task(self.gameloop.run(), name="gameloop")
        self._timer_task = asyncio.create_task(self.timer.run(), name="timer")
        self._persistwriter_task = asyncio.create_task(self.persistwriter.run(), name="persistwriter")

    async def _cancel_and_await(self, *tasks: "asyncio.Task | None") -> None:
        # cancel 一组 task 并收割(吞 CancelledError;意外死亡记 ERROR 但不阻断关闭——尽力 drain)。
        for t in tasks:
            if t is not None:
                t.cancel()
        for t in tasks:
            if t is None:
                continue
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("task %s crashed during shutdown", t.get_name())

    async def stop(self) -> None:
        # 关闭反序(connection.md:177-180 四步 / changes/0046):
        # ① 停 Timer + GameLoop:不再产生/消费 inbox 命令。GameLoop 在 `await get()` 处被 cancel,handle 全程同步
        #    ⇒ cancel 只在下个 await 生效,绝不打断处理到一半的命令(在途命令要么完整处理、要么没开始)。
        await self._cancel_and_await(self._timer_task, self._gameloop_task)
        # ② 排空 inbox(spec「在途命令处理完」):同步驱动 GameLoop.handle 处理排队命令,其 Persist 写入缓冲,
        #    交③一并落库。本循环全程无 await ⇒ 原子,PersistWriter 不与之竞 swap。被丢的只会是「未开始」的命令
        #    (从未 commit 进 world、无对应写),丢弃即丢一个未生效输入(storage.md 接受)。
        drained = 0
        if self.gameloop is not None:
            while not self.inbox.empty():
                try:
                    cmd = self.inbox.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    self.gameloop.handle(cmd)  # 同步;内部已兜 reduce 崩溃,这里再兜 checkout/commit 等
                except Exception:  # 单条命令处理异常不得中断关闭(否则 dispose 被跳、连接池泄漏)——尽力 drain
                    log.exception("command %s crashed during shutdown drain", type(cmd).__name__)
                drained += 1
        if drained:
            log.info("drained %d in-flight commands before shutdown", drained)
        # ③ 停 PersistWriter 周期循环 + 终结 flush:有界,超 DB_DRAIN_TIMEOUT_MS → CRITICAL(见 persist.drain,0025)。
        #    drain 即便超时也 return(非 raise)⇒ 下面 dispose 照常跑、进程干净退。
        await self._cancel_and_await(self._persistwriter_task)
        await self.persistwriter.drain()
        # ④ cancel 各 Sender(best-effort 兜底:正常每条 Receiver 的 finally 已 cancel 自己的;兜仍登记者)+ 关连接池。
        for nick in self.conns.online_nicks():
            conn = self.conns.get(nick)
            if conn is not None and conn.sender_task is not None:
                conn.sender_task.cancel()
        await self.engine.dispose()


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
    # REST 大厅房间列表(唯一读 committed world 的 REST,见 app/rest/lobby.py);world 迟绑(setup() 后才建)。
    app.include_router(make_lobby_router(lambda: shell.world))
    # REST 排行榜(读 DB 结算积分,见 app/rest/leaderboard.py);sessionmaker 迟绑(setup() 前已在 __init__ 建好)。
    app.include_router(make_leaderboard_router(lambda: shell.sessionmaker))
    # REST 手牌历史(读 DB,游标分页,见 app/rest/hands.py)。
    app.include_router(make_hands_router(lambda: shell.sessionmaker))
    # 登录端点(P5:K_user 护密码、铸会话、K_user 加密下发 session,见 app/rest/login.py)。sessionmaker/session_store 迟绑。
    app.include_router(make_login_router(lambda: shell.sessionmaker, shell.session_store))

    @app.websocket("/dev/ws")
    async def dev_ws(ws: WebSocket, nick: str = Query(...)):  # type: ignore[valid-type]
        # dev 明文握手:?nick= 必须是预置 dev 用户(连接绑 nick,模型 2)。无 MAC / 无加密(P5 替换)。
        await ws.accept()
        if nick not in gameconfig.DEV_USERS:
            await ws.close(code=4404)  # 未知 dev 用户:拒,不建 Connection
            return
        conn = Connection.create(nick=nick, session_id=nick, ws=ws)
        await run_receiver(
            conn, shell.conns, shell.inbox, shell.timer, shell.sessionmaker, shell.history, shell.persist
        )

    return app


app = create_app()  # uvicorn 入口:app.shell.lifespan:app
