"""私信(DM)shell 路由穷举(messaging.md §私信 / changes/0038):防护 → 解析 uid → 落库 DMWrite → 在线投 DMDelivered。
纯 shell 路由(不进 GameLoop / 不碰 world):seeded sessionmaker 解析 uid、fake 连接验实时投递与失败回执、写缓冲验落库。"""

import asyncio
import time

from sqlalchemy.pool import StaticPool

from app import gameconfig
from app.core.errors import Err, ErrorCode
from app.db.dm_records import DMWrite
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import User
from app.shell.connection import ConnectionManager
from app.shell.messaging import route_direct_message
from app.shell.persist import WriteBuffer
from app.shell.ratelimit import TokenBucket
from app.wire import client as C
from app.wire.server import DMDelivered, DMUndelivered, ErrorMessage
from tests.shell._fakes import drain, make_conn


async def _seeded_sm(users: dict[str, int]):
    # users: {nick: uid};内存 sqlite(StaticPool 跨连接存活)+ 建表 + 种子,供 nick→uid 解析。
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            for nick, uid in users.items():
                s.add(User(id=uid, nickname=nick, points=0))
    return sm


def _dm(to_nick: str, text: str = "hi") -> C.DirectMessage:
    return C.DirectMessage(to_nick=to_nick, text=text)


def _both_online(sender="alice", recipient="bob"):
    # 建 conns + 两连接(都在线)+ 空写缓冲;返回 (conns, sender_conn, recipient_conn, persist)。
    conns = ConnectionManager()
    s_conn, r_conn = make_conn(sender), make_conn(recipient)
    conns.register(s_conn)
    conns.register(r_conn)
    return conns, s_conn, r_conn, WriteBuffer()


# ── 在线收件人:实时投 DMDelivered + 必落库 DMWrite(wire msg_id == 落库 dedupe_key)──
async def test_dm_delivered_to_online_recipient():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    conns, alice, bob, persist = _both_online()
    await route_direct_message(alice, _dm("bob", "hey"), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(bob)
    assert len(out) == 1 and isinstance(out[0], DMDelivered)
    assert out[0].from_nick == "alice" and out[0].text == "hey" and out[0].msg_id  # 身份盖连接 nick、原文
    assert drain(alice) == []  # 发件人成功路径零回包(v1 无 echo,送达走 0039 已读回执)
    snap = persist.snapshot()
    assert len(snap) == 1 and isinstance(snap[0], DMWrite)
    assert (snap[0].from_uid, snap[0].to_uid, snap[0].text) == (1, 2, "hey")  # 落库按不可变 uid
    assert snap[0].dedupe_key == out[0].msg_id  # wire msg_id 即落库 dedupe_key(前端可对齐)
    assert snap[0].created_at.tzinfo is not None  # shell 盖 tz-aware 墙钟


# ── 离线收件人:不实时投递,但仍落库(未读;登录补收 0039)──
async def test_dm_offline_recipient_persists_only():
    sm = await _seeded_sm({"alice": 1, "bob": 2})  # bob 在 DB 但不在线
    conns = ConnectionManager()
    alice = make_conn("alice")
    conns.register(alice)  # bob 不 register → 离线
    persist = WriteBuffer()
    await route_direct_message(alice, _dm("bob"), conns=conns, persist=persist, sessionmaker=sm)
    snap = persist.snapshot()
    assert len(snap) == 1 and isinstance(snap[0], DMWrite) and snap[0].to_uid == 2  # 落库未读
    assert drain(alice) == []  # 离线不是硬错误,发件人无回包


# ── 发件人无 DB 行(鉴权说有、DB 无)= 内部不一致 → INTERNAL,不落库(镜像 _build_join 的 unknown-user 臂)──
async def test_dm_sender_missing_db_row_errors_internal():
    sm = await _seeded_sm({"bob": 2})  # 只种子 bob(收件人);alice(发件人)无 DB 行
    conns, alice, bob, persist = _both_online()
    await route_direct_message(alice, _dm("bob"), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], ErrorMessage) and out[0].code is ErrorCode.INTERNAL
    assert persist.is_empty() and drain(bob) == []  # 发件人缺行 → 既不落库也不投收件人


# ── DB 读失败(sessionmaker 未建表)→ 兜成 INTERNAL,不落库(镜像 _build_join 的 db-error 臂)──
async def test_dm_db_read_failure_errors_internal():
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    sm = make_sessionmaker(engine)  # 未 create_all:查 User 表即抛 no-such-table
    conns, alice, bob, persist = _both_online()
    await route_direct_message(alice, _dm("bob"), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], ErrorMessage) and out[0].code is ErrorCode.INTERNAL
    assert persist.is_empty()  # DB 抖动 → 不落库,回发件人 INTERNAL


# ── 对端根本不存在:DMUndelivered 回发件人,且不落库 ──
async def test_dm_recipient_not_found_returns_undelivered():
    sm = await _seeded_sm({"alice": 1})  # 无 ghost
    conns = ConnectionManager()
    alice = make_conn("alice")
    conns.register(alice)
    persist = WriteBuffer()
    await route_direct_message(alice, _dm("ghost"), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], DMUndelivered) and out[0].to_nick == "ghost"
    assert persist.is_empty()  # 对端不存在 → 硬失败,不落库


# ── 发给自己:CANNOT_DM_SELF,不落库,且在限速前拒(不耗令牌)──
async def test_dm_self_send_rejected_before_rate_limit():
    sm = await _seeded_sm({"alice": 1})
    conns = ConnectionManager()
    alice = make_conn("alice")
    conns.register(alice)
    persist = WriteBuffer()
    before = alice.dm_bucket.tokens
    await route_direct_message(alice, _dm("alice"), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], ErrorMessage) and out[0].code is ErrorCode.CANNOT_DM_SELF
    assert persist.is_empty()
    assert alice.dm_bucket.tokens == before  # 自发在限速前拒(防护序),不耗令牌、不读 DB


# ── 空文本:INVALID_MESSAGE,不落库、不投收件人 ──
async def test_dm_empty_text_rejected():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    conns, alice, bob, persist = _both_online()
    await route_direct_message(alice, _dm("bob", "   "), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], ErrorMessage) and out[0].code is ErrorCode.INVALID_MESSAGE
    assert persist.is_empty() and drain(bob) == []


# ── 超长文本:MESSAGE_TOO_LONG,不落库、不投收件人 ──
async def test_dm_too_long_rejected():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    conns, alice, bob, persist = _both_online()
    over = "x" * (gameconfig.DM_MAX_TEXT_LEN + 1)
    await route_direct_message(alice, _dm("bob", over), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], ErrorMessage) and out[0].code is ErrorCode.MESSAGE_TOO_LONG
    assert persist.is_empty() and drain(bob) == []


# ── 限速:第一条过(投达 + 落库),第二条 RATE_LIMITED(不落库)──
async def test_dm_rate_limited_after_burst():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    conns, alice, bob, persist = _both_online()
    alice.dm_bucket = TokenBucket.create(capacity=1.0, refill_per_sec=0.0, now=time.monotonic())  # 容量 1、不补充
    await route_direct_message(alice, _dm("bob", "1"), conns=conns, persist=persist, sessionmaker=sm)
    await route_direct_message(alice, _dm("bob", "2"), conns=conns, persist=persist, sessionmaker=sm)
    assert sum(isinstance(m, DMDelivered) for m in drain(bob)) == 1  # 只第一条投达
    errs = [m for m in drain(alice) if isinstance(m, ErrorMessage)]
    assert len(errs) == 1 and errs[0].code is ErrorCode.RATE_LIMITED
    assert len(persist.snapshot()) == 1  # 只第一条落库(限速在落库前)


# ── 实时投递尽力而为:收件人 outbound 满 → 丢实时投递但**不丢消息**(已落库,决策 6)──
async def test_dm_realtime_dropped_when_recipient_full_still_persists():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    conns = ConnectionManager()
    alice = make_conn("alice")
    bob = make_conn("bob")
    bob.outbound = asyncio.Queue(maxsize=1)  # 模拟慢客户端:size-1 队列塞满
    bob.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.INTERNAL, "filler")))
    conns.register(alice)
    conns.register(bob)
    persist = WriteBuffer()
    await route_direct_message(alice, _dm("bob", "hey"), conns=conns, persist=persist, sessionmaker=sm)  # 不抛
    assert len(persist.snapshot()) == 1 and isinstance(persist.snapshot()[0], DMWrite)  # 消息仍落库(不丢)
    assert bob.outbound.qsize() == 1  # 实时投递被丢(队列仍只有填充物)
