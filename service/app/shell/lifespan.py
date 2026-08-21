# lifespan:dev shell 装配 + 明文 ws 端点(见 connection.md「lifespan」,最小 dev 版)。
# dev-only:无鉴权 / 无加密;接真 async DB(sqlite+aiosqlite)——幂等种子 dev 用户进 DB + OrmPersister 落库。
# 用户连接 → 进大厅 → 主动 join_room → Receiver 读 DB 载入(per-join,0030);dev 房空预置。
# 运行:cd service && .venv/bin/uvicorn app.shell.lifespan:app  → ws://<host>/dev/ws?nick=alice
# P5 国密信道落地即替换握手/帧;P8 lifespan drain 收尾。

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app import gameconfig
from app.auth.channel import SecureChannel
from app.auth.passwords import hash_password
from app.config import settings
from app.core.commands import Command
from app.core.domain import World
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.db.orm_persister import OrmPersister
from app.db.queries import load_user_by_nick
from app.auth.session import Session, SessionStore
from app.rest.hands import make_hands_router
from app.rest.leaderboard import make_leaderboard_router
from app.rest.lobby import make_lobby_router
from app.rest.login import make_login_router
from app.rest.profile import make_nickname_router, make_profile_router
from app.shell.connection import Connection, ConnectionManager
from app.shell.dispatch import Dispatcher
from app.shell.gameloop import GameLoop
from app.shell.logsetup import setup_logging
from app.shell.persist import PersistWriter, WriteBuffer
from app.shell.presence import Presence
from app.shell.receiver import run_receiver
from app.shell.timer import Timer

log = logging.getLogger(__name__)


def _dev_uid(index: int) -> int:
    return index + 1  # dev 用户主键 = 预置序号 + 1(id 自 1 起,避免 0;种子/载入一致)


@lru_cache(maxsize=1)
def _dev_password_hash() -> str:
    # dev 共享口令的哈希:进程内算一次缓存(100k 轮 ≈0.16s;dev 用户共享口令 → 共享 salt$rounds$digest 无害),
    # 免每次 setup 重算拖慢 dev 测(changes/0060)。
    return hash_password(gameconfig.DEV_PASSWORD, gameconfig.PWD_HASH_ROUNDS)


async def seed_dev_users(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    # 幂等种子:dev 用户进 DB(id=序号+1 / nickname / points / 鉴权列)。新用户 INSERT;pre-P5 已存在但 name=NULL 的
    # dev 行**回填**鉴权列(login-enable,不重置 points/nickname——承接 OrmPersister 落库积分);已启用(name 非 NULL)则跳过。
    # 鉴权列 = dev 脚手架:name=昵称、口令=DEV_PASSWORD(共享哈希)、k_cur=DEV_KUSER(共享,dev-only,见 changes/0060)。
    # k_cur_until 留 NULL = 不排程(0066):dev 共享钥不被轮换 cron 轮走(轮走则 DEV_KUSER 登录失效);ver 记 1 对账。
    async with sessionmaker() as session:
        async with session.begin():
            for i, nick in enumerate(gameconfig.DEV_USERS):
                uid = _dev_uid(i)
                user = await session.get(User, uid)
                if user is None:
                    session.add(User(
                        id=uid, nickname=nick, points=gameconfig.DEV_START_POINTS,
                        name=nick, hash_password=_dev_password_hash(),
                        k_cur=gameconfig.DEV_KUSER, k_cur_ver=1,
                    ))
                elif user.name is None:  # pre-P5 dev 行:补鉴权列 login-enable(不动 points/nickname)
                    user.name = nick
                    user.hash_password = _dev_password_hash()
                    user.k_cur = gameconfig.DEV_KUSER
                    user.k_cur_ver = 1


def build_dev_world() -> World:
    # 空 world:无静态预置房(动态房——谁都可创建 / 空则消失,见 core.md 房间生命周期 / changes/0049)。
    # 用户连接 → 进大厅(Connect no-op)→ 主动 join_room{room} → Receiver 读 DB 载入 + 盖建房默认配置 →
    # 房不存在则 reduce 建房、加入;最后一人离开该房则销毁(per-join 载入 0030,动态房 0049)。
    return World()


def _channel_for(session: Session) -> SecureChannel:
    # 取本会话逐帧信道:首次握手派生、缓存在 Session 上,跨重连复用同一实例 → seq 逐会话连续(挡跨重连重放,
    # 见 auth.md「seq 按会话计」/ changes/0061 决策 1)。同步无 await ⇒ 检查-派生-赋值原子,并发握手无竞态。
    if session.channel is None:
        session.channel = SecureChannel.derive(session.token, gameconfig.WS_FRAME_MAX_BYTES)
    return session.channel


def _watchdog(task: "asyncio.Task") -> None:
    # 常驻协程的死亡告警(0083 / BUG-7):GameLoop/Timer/PersistWriter 三条循环只应因关闭时的 cancel 而结束。
    # 任何其它退出都意味着「进程还在、ws 还连着,但状态机已经哑了」——这是最难察觉的故障,必须留 CRITICAL。
    # 正常关闭走 cancel,不落噪声。
    if task.cancelled():
        return
    exc = task.exception()  # 已排除 cancelled ⇒ 不会抛
    if exc is not None:
        log.critical("shell task %s died: %r", task.get_name(), exc, exc_info=exc)
    else:
        log.critical("shell task %s exited unexpectedly", task.get_name())


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
        self.dispatcher = Dispatcher(self.world, self.conns, self.persist, self.timer, self.inbox)
        self.gameloop = GameLoop(self.world, self.inbox, self.dispatcher)
        self.presence = Presence(self.world, self.conns)  # 只读聚合,持稳定 world 引用(commit 原地改其 .users/.rooms)

    def start(self) -> None:
        # 起 GameLoop + Timer + PersistWriter(须先 await setup() 建好 gameloop)。三者都是不该返回的常驻循环,
        # 各挂一个 watchdog:非取消而退出即落 CRITICAL(0083 / BUG-7)。
        assert self.gameloop is not None, "DevShell.start() 前须先 await setup()"
        self._gameloop_task = asyncio.create_task(self.gameloop.run(), name="gameloop")
        self._timer_task = asyncio.create_task(self.timer.run(), name="timer")
        self._persistwriter_task = asyncio.create_task(self.persistwriter.run(), name="persistwriter")
        for t in (self._gameloop_task, self._timer_task, self._persistwriter_task):
            t.add_done_callback(_watchdog)

    async def _cancel_and_await(self, *tasks: "asyncio.Task | None") -> None:
        # cancel 一组 task 并收割(意外死亡记 ERROR 但不阻断关闭——尽力 drain)。
        # **两种 CancelledError 必须分开**(0083 / BUG-5):此前一律吞掉,连「取消是冲 stop() 自己来的」也吞,
        # 于是关闭超时与强制中止形同虚设——上层再怎么 cancel,stop() 都赖着不走。
        # 判据取两条相与:`current.cancelling() > 0` = 确实有人 cancel 了我(3.11+ 精确计数);
        # `not t.cancelled()` = 子任务并非「被我 cancel 掉」这一预期结局。任一成立就上抛。
        for t in tasks:
            if t is not None:
                t.cancel()
        for t in tasks:
            if t is None:
                continue
            try:
                await t
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if (current is not None and current.cancelling() > 0) or not t.cancelled():
                    raise  # 取消冲我来的 → 上抛,让关闭超时真的能中止 stop()
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
                    self.gameloop.handle(cmd)  # 同步;内部已兜住整条链(0083),这里是不信任兜底的第二道
                except Exception:  # 单条命令处理异常不得中断关闭(否则 dispose 被跳、连接池泄漏)——尽力 drain
                    log.exception("command %s crashed during shutdown drain", type(cmd).__name__)
                finally:
                    self.inbox.task_done()  # 与 GameLoop.run 对称计数(0073),免遗留 inbox.join() 等待者悬死
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
        try:
            yield
        finally:
            # 关闭无条件跑到 stop()(0083 / BUG-5):此前 `yield` 裸着,关闭路径上一旦抛异常或被取消,
            # stop() 被整体跳过 → drain 根本不执行,写缓冲里未落库的积分全丢、engine 连接池泄漏。
            # 「关闭必须 drain」是 connection.md / db.md 的关闭契约,不能挂在「关闭一切顺利」这个前提上。
            await shell.stop()

    app = FastAPI(lifespan=lifespan, title="poker dev shell (plaintext, dev-only)")
    app.state.shell = shell
    # 跨源放行:前端在 3000、后端在 8000,浏览器会先发预检,不回 CORS 头就整个请求被拦。
    # 来源白名单走 app/config(基础设施轨),不硬编码;留空表示同源部署、不需要 CORS。
    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,  # 明确列举,不用 "*":带凭据的请求下通配是无效的,也不该放任
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
    # REST 大厅房间列表(唯一读 committed world 的 REST,见 app/rest/lobby.py);world 迟绑(setup() 后才建)。
    app.include_router(make_lobby_router(lambda: shell.world))
    # REST 排行榜(读 DB 结算积分,见 app/rest/leaderboard.py);sessionmaker 迟绑(setup() 前已在 __init__ 建好)。
    app.include_router(make_leaderboard_router(lambda: shell.sessionmaker))
    # REST 手牌历史(读 DB,游标分页,见 app/rest/hands.py)。
    app.include_router(make_hands_router(lambda: shell.sessionmaker))
    # 登录端点(P5:K_user 护密码、铸会话、K_user 加密下发 session,见 app/rest/login.py)。sessionmaker/session_store 迟绑。
    app.include_router(make_login_router(lambda: shell.sessionmaker, shell.session_store))
    # 用户资料(P5 REST 加密信封首个消费者:POST /user/me 走会话密钥信封,见 app/rest/profile.py / changes/0062)。
    app.include_router(make_profile_router(lambda: shell.sessionmaker, shell.session_store))
    # 改昵称(仅大厅;presence 在 setup() 后才建 → 迟绑 getter,见 changes/0065)。
    app.include_router(
        make_nickname_router(lambda: shell.sessionmaker, shell.session_store, lambda: shell.presence, shell.conns)
    )

    @app.websocket("/dev/ws")
    async def dev_ws(ws: WebSocket, nick: str = Query(...)):  # type: ignore[valid-type]
        # dev 明文握手:?nick= 必须是预置 dev 用户(连接绑 nick,模型 2)。无 MAC / 无加密(dev-only 脚手架)。
        # 与加密端点 /ws 并存;前端切到加密后再退役本端点(见 changes/0061 决策 4)。
        await ws.accept()
        if nick not in gameconfig.DEV_USERS:
            await ws.close(code=4404)  # 未知 dev 用户:拒,不建 Connection
            return
        # 名下须仍有 DB 行:dev 用户改名后旧名无行——放行会造出「无 DB 背书的孤儿连接键」,
        # 与后续改名撞键、_build_join 还会按 nick 错配他人行(0065 自 review 抓修;正路走 /ws?sid=)。
        if await load_user_by_nick(shell.sessionmaker, nick) is None:
            await ws.close(code=4404)
            return
        conn = Connection.create(nick=nick, session_id=nick, ws=ws)  # channel=None → 明文帧
        await run_receiver(
            conn, shell.conns, shell.inbox, shell.timer, shell.sessionmaker, shell.world, shell.persist,
            persistwriter=shell.persistwriter,  # 载入屏障接线(0073):生产路必传
        )

    @app.websocket("/ws")
    async def secure_ws(ws: WebSocket, sid: str = Query(...)):  # type: ignore[valid-type]
        # 加密握手(P5,见 auth.md §加密信道 / changes/0061):?sid=<session_id> 查会话(存在且未过期 = 握手鉴权;
        # 持钥证明落首帧 MAC)→ 派生/复用逐会话信道 → 建 Connection(channel 非 None → 加密帧)→ run_receiver。
        # 无 session(未登录/过期/伪 sid)→ 关闭码拒,绝不建 Connection(connection.md 步 1)。
        await ws.accept()
        session = shell.session_store.lookup(sid, time.time())
        if session is None:
            await ws.close(code=4401)  # 未认证 / 过期 / 未知 sid:拒
            return
        conn = Connection.create(
            nick=session.nickname, session_id=sid, ws=ws, channel=_channel_for(session), session=session
        )
        await run_receiver(
            conn, shell.conns, shell.inbox, shell.timer, shell.sessionmaker, shell.world, shell.persist,
            persistwriter=shell.persistwriter,  # 载入屏障接线(0073):生产路必传
        )

    return app


app = create_app()  # uvicorn 入口:app.shell.lifespan:app
