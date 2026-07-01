"""P4(三之二-b):DB-backed dev shell 端到端——命令穿 reduce → Persist → OrmPersister → 真 DB 行。

验:DevShell.setup 种子+从 DB 载入(world 积分来自 DB、OrmPersister 接上)、种子幂等(重启承接落库变更)、
买入 PointsWrite 落 DB(状态写)、一手牌经 dispatch 盖 end_time 后 HandRecord+participants 落 DB(事件写)。
aiosqlite 内存库(StaticPool 共享单连接 + make_engine 装 PRAGMA foreign_keys=ON)。
"""

import asyncio

from sqlalchemy import func, select
from sqlalchemy.pool import StaticPool

from app import gameconfig
from app.core.commands import BuyIn, JoinRoom, PlayerAction, RoomCreate, SitDown, StartHand
from app.core.enums import PlayerActionType
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import HandParticipant, HandRecord, User
from app.db.orm_persister import OrmPersister
from app.shell.connection import Connection, ConnectionManager
from app.shell.dispatch import Dispatcher
from app.shell.gameloop import GameLoop
from app.shell.history import RoomChatBuffer
from app.shell.lifespan import DevShell
from app.shell.persist import PersistWriter, WriteBuffer
from app.shell.receiver import run_receiver
from app.shell.timer import Timer
from tests.shell._fakes import FakeWS
from tests.builders import DECK, T0, make_table, seat


async def _settle(cond, timeout: float = 1.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.005)
    return cond()


def _mem_engine():
    return make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )


async def _dev_shell(engine=None):
    shell = DevShell(engine=engine or _mem_engine())
    await shell.setup()
    return shell


# ── DevShell.setup:种子 dev 用户进 DB,world 起步为空(连接 join_room 才载入,见 0030)──
async def test_setup_seeds_db_with_empty_world():
    shell = await _dev_shell()
    assert shell.world.users == {}  # 无预置用户
    assert shell.world.rooms == {}  # 无静态预置房(动态房:join 才建 / 空则消失,见 0049)
    async with shell.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == len(gameconfig.DEV_USERS)
        alice = (await s.execute(select(User).where(User.nickname == "alice"))).scalar_one()
        assert (alice.id, alice.points) == (1, gameconfig.DEV_START_POINTS)  # 供 join 载入


# ── 种子幂等:同 engine 再 setup 不重置 DB 积分(模拟重启承接上次落库)──
async def test_setup_idempotent_preserves_points():
    eng = _mem_engine()
    await _dev_shell(engine=eng)
    sm = make_sessionmaker(eng)
    async with sm() as s:  # 模拟上次运行落库后的积分
        async with s.begin():
            (await s.get(User, 1)).points = 42
    await _dev_shell(engine=eng)  # 重启场景:同库再 setup(种子幂等)
    async with sm() as s:
        assert (await s.get(User, 1)).points == 42  # 没被种子重置回 DEV_START_POINTS


# ── 端到端状态写:join_room 载入 → 买入 → PointsWrite → DB User.points UPDATE ──
async def test_e2e_buyin_persists_points_to_db():
    shell = await _dev_shell()
    amount = 100
    # alice 连接进大厅 → join_room 载入(直接驱动 JoinRoom 命令,等价 Receiver 读 DB 富化 + 盖建房配置后的产物)。
    # 房不存在 → 用 create 建房(动态房,0049)。
    shell.gameloop.handle(
        JoinRoom(
            origin="alice", room=gameconfig.DEV_ROOM, uid=1, loaded=gameconfig.DEV_START_POINTS,
            create=RoomCreate(gameconfig.DEV_SMALL_BLIND, gameconfig.DEV_BUY_IN, gameconfig.DEV_SEATS),
        )
    )
    shell.gameloop.handle(SitDown(origin="alice", seat=0))
    shell.gameloop.handle(BuyIn(origin="alice", seat=0, amount=amount))
    await shell.persistwriter.flush_once()
    async with shell.sessionmaker() as s:
        assert (await s.get(User, 1)).points == gameconfig.DEV_START_POINTS - amount


# ── 全链:真 dev shell + Receiver,connect → join_room(读 DB)→ sit → buy → 内存先生效 + 落 DB ──
async def test_e2e_connect_join_buy_through_dev_shell():
    shell = await _dev_shell()
    gl = asyncio.create_task(shell.gameloop.run())  # 只起 gameloop(persistwriter 手动 flush 求确定性)
    conn = Connection.create(nick="alice", session_id="alice", ws=FakeWS())
    rx = asyncio.create_task(
        run_receiver(conn, shell.conns, shell.inbox, shell.timer, shell.sessionmaker, shell.history, shell.persist)
    )
    try:
        await asyncio.sleep(0)  # 登记 + 投 Connect(大厅 no-op,alice 尚不在 world)
        conn.ws.feed('{"type":"join_room","room":"dev"}')  # Receiver 读 DB 富化 → JoinRoom
        await _settle(lambda: "alice" in shell.world.users)
        assert shell.world.users["alice"].points == gameconfig.DEV_START_POINTS  # 从 DB 载入
        conn.ws.feed('{"type":"sit_down","seat":0}')
        await _settle(lambda: shell.world.rooms[gameconfig.DEV_ROOM].seats[0] is not None)
        conn.ws.feed('{"type":"buy_in","seat":0,"amount":100}')
        await _settle(lambda: shell.world.users["alice"].points == gameconfig.DEV_START_POINTS - 100)
        await shell.persistwriter.flush_once()  # delayDB 落库
        async with shell.sessionmaker() as s:
            assert (await s.get(User, 1)).points == gameconfig.DEV_START_POINTS - 100  # DB 追平
    finally:
        for t in (rx, gl):
            t.cancel()
        for t in (rx, gl):
            try:
                await t
            except asyncio.CancelledError:
                pass


# ── 端到端事件写:一手牌经 gameloop(dispatch 盖 end_time)→ HandRecord+participants 落 DB ──
async def test_e2e_hand_record_persists_to_db():
    world = make_table(
        {0: seat("alice", 100, new_here=False), 1: seat("bob", 100, new_here=False)},
        room_name="r1",
    )
    world.users["alice"].uid = 1  # 真账号 id 自 1 起(非 make_table 的 enumerate 0);DB 种子由下方从 world 派生,二者同源
    world.users["bob"].uid = 2
    sm = await _seed_users_from_world(world)  # DB 种子 uid 取自 world——不会与参与者 uid 悄悄漂移
    gameloop, writer = _harness(world, sm)
    gameloop.handle(StartHand(origin="alice", seat=0, started_at=T0, deck=list(DECK)))
    hand = world.rooms["r1"].hand
    actor = hand.players[hand.acting_position].nickname
    gameloop.handle(PlayerAction(origin=actor, action=PlayerActionType.FOLD))  # 弃牌 → 只剩一人 → 手结束
    await writer.flush_once()
    expected_uids = {world.users["alice"].uid, world.users["bob"].uid}
    async with sm() as s:
        hr = (await s.execute(select(HandRecord))).scalar_one()
        assert hr.end_time is not None  # dispatch 盖了墙钟(core 留 None)
        assert hr.dedupe_key == "r1:1"
        parts = (await s.execute(select(HandParticipant))).scalars().all()
        assert {p.uid for p in parts} == expected_uids  # FK 解析到种子用户(uid 取自同一 world)


async def _seed_users_from_world(world):
    # 从 world.users 派生 DB 种子(id=uid / nickname),保证参与者 uid 与 DB 用户行同源、FK 必解析。
    engine = _mem_engine()
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            for u in world.users.values():
                s.add(User(id=u.uid, nickname=u.nickname, points=gameconfig.DEV_START_POINTS))
    return sm


def _harness(world, sm):
    # 最小真 shell 接线(OrmPersister 落 DB):gameloop 同步驱动 + writer.flush_once 手动落库。
    inbox: "asyncio.Queue" = asyncio.Queue()
    persist = WriteBuffer()
    dispatcher = Dispatcher(world, ConnectionManager(), persist, Timer(inbox), inbox, RoomChatBuffer())
    gameloop = GameLoop(world, inbox, dispatcher)
    writer = PersistWriter(persist, OrmPersister(sm), flush_interval_s=0.001, drain_timeout_s=0.5)
    return gameloop, writer
