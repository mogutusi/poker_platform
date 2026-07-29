"""私信(DM)shell 路由穷举(messaging.md §私信 / changes/0038):防护 → 解析 uid → 落库 DMWrite → 在线投 DMDelivered。
纯 shell 路由(不进 GameLoop / 不碰 world):seeded sessionmaker 解析 uid、fake 连接验实时投递与失败回执、写缓冲验落库。"""

import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy.pool import StaticPool

from app import gameconfig
from app.core.errors import Err, ErrorCode
from app.db.dm_records import DMReadCursorWrite, DMWrite
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import DMMessage, DMReadCursor, User
from app.shell.connection import ConnectionManager
from app.shell.messaging import deliver_dm_catch_up, route_direct_message, route_dm_mark_read
from app.shell.persist import WriteBuffer
from app.shell.ratelimit import TokenBucket
from app.wire import client as C
from app.wire.server import DMDelivered, DMRead, DMUndelivered, ErrorMessage
from tests.shell._fakes import drain, make_conn

T_READ = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
T0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)


async def _seed_dms(sm, messages=(), cursors=()):
    # messages: [(msg_id, from_uid, to_uid, text, created_at)];cursors: [(reader_uid, peer_uid, read_through_ts)]。
    async with sm() as s:
        async with s.begin():
            for key, fu, tu, txt, ts in messages:
                s.add(DMMessage(dedupe_key=key, from_uid=fu, to_uid=tu, text=txt, created_at=ts))
            for r, p, ts in cursors:
                s.add(DMReadCursor(reader_uid=r, peer_uid=p, read_through_ts=ts))


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


def _mark(peer_nick: str, read_through: datetime = T_READ) -> C.DMMarkRead:
    return C.DMMarkRead(peer_nick=peer_nick, read_through=read_through)


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


# ════════ 标记已读 route_dm_mark_read(changes/0039)════════
# reader = 连接 nick(读了 peer 发来的消息);peer = 原发件人(收回执)。


# ── 标读:落已读游标(reader,peer)+ peer 在线收 DMRead 回执;reader 无回包 ──
async def test_dm_mark_read_persists_cursor_and_acks_online_peer():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    conns, alice, bob, persist = _both_online()  # alice=reader、bob=peer(原发件人)
    await route_dm_mark_read(alice, _mark("bob"), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(bob)  # peer(原发件人)收已读回执
    assert len(out) == 1 and isinstance(out[0], DMRead)
    assert out[0].reader_nick == "alice" and out[0].read_through == T_READ
    snap = persist.snapshot()
    assert len(snap) == 1 and isinstance(snap[0], DMReadCursorWrite)
    assert (snap[0].reader_uid, snap[0].peer_uid) == (1, 2)  # 游标键 (reader=alice, peer=bob)
    assert snap[0].read_through_ts == T_READ
    assert drain(alice) == []  # reader 成功路径无回包


# ── 标读:peer 离线 → 仅落游标,无回执(补收 0040 兜)──
async def test_dm_mark_read_offline_peer_persists_cursor_only():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    conns = ConnectionManager()
    alice = make_conn("alice")
    conns.register(alice)  # bob 不在线
    persist = WriteBuffer()
    await route_dm_mark_read(alice, _mark("bob"), conns=conns, persist=persist, sessionmaker=sm)
    assert len(persist.snapshot()) == 1 and isinstance(persist.snapshot()[0], DMReadCursorWrite)  # 游标已落
    assert drain(alice) == []  # 离线非错,reader 无回包


# ── 标读未知对端:INVALID_MESSAGE(畸形请求,非 DMUndelivered 投递语义),不落游标 ──
async def test_dm_mark_read_unknown_peer_errors_invalid_message():
    sm = await _seeded_sm({"alice": 1})  # 无 ghost
    conns = ConnectionManager()
    alice = make_conn("alice")
    conns.register(alice)
    persist = WriteBuffer()
    await route_dm_mark_read(alice, _mark("ghost"), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], ErrorMessage) and out[0].code is ErrorCode.INVALID_MESSAGE
    assert persist.is_empty()


# ── 标读与自己的会话:CANNOT_DM_SELF,不落游标 ──
async def test_dm_mark_read_self_rejected():
    sm = await _seeded_sm({"alice": 1})
    conns = ConnectionManager()
    alice = make_conn("alice")
    conns.register(alice)
    persist = WriteBuffer()
    await route_dm_mark_read(alice, _mark("alice"), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], ErrorMessage) and out[0].code is ErrorCode.CANNOT_DM_SELF
    assert persist.is_empty()


# ── 标读但 reader 无 DB 行 = 内部不一致 → INTERNAL,不落游标 ──
async def test_dm_mark_read_reader_missing_db_row_errors_internal():
    sm = await _seeded_sm({"bob": 2})  # 只种子 peer bob;reader alice 无 DB 行
    conns, alice, bob, persist = _both_online()
    await route_dm_mark_read(alice, _mark("bob"), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], ErrorMessage) and out[0].code is ErrorCode.INTERNAL
    assert persist.is_empty() and drain(bob) == []


# ── 标读 DB 读失败(未建表)→ INTERNAL,不落游标 ──
async def test_dm_mark_read_db_read_failure_errors_internal():
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    sm = make_sessionmaker(engine)  # 未 create_all
    conns, alice, bob, persist = _both_online()
    await route_dm_mark_read(alice, _mark("bob"), conns=conns, persist=persist, sessionmaker=sm)
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], ErrorMessage) and out[0].code is ErrorCode.INTERNAL
    assert persist.is_empty()


# ════════ 登录补收 deliver_dm_catch_up(changes/0040)════════


# ── 补收未读:bob 发给 alice 的两条(无游标)→ alice 连接补收两条 DMDelivered,旧→新有序 ──
async def test_catch_up_delivers_unread_oldest_first():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    await _seed_dms(sm, messages=[("m1", 2, 1, "first", T0), ("m2", 2, 1, "second", T1)])  # bob(2)→alice(1)
    alice = make_conn("alice")
    await deliver_dm_catch_up(alice, sessionmaker=sm)
    out = drain(alice)
    assert all(isinstance(m, DMDelivered) for m in out)
    assert [(m.msg_id, m.from_nick, m.text) for m in out] == [("m1", "bob", "first"), ("m2", "bob", "second")]
    # 时间戳:sqlite 读回 naive 经 _as_utc 补 UTC → tz-aware + 值正确(与实时路径同形,序列化带 Z)
    assert (out[0].created_at, out[1].created_at) == (T0, T1)
    assert all(m.created_at.tzinfo is not None for m in out)


# ── 补收尊重游标:已读到 T0(含)→ 只补 T0 之后的未读(m1@T0 已读、m2@T1 未读)──
async def test_catch_up_respects_read_cursor():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    await _seed_dms(
        sm,
        messages=[("m1", 2, 1, "read", T0), ("m2", 2, 1, "unread", T1)],
        cursors=[(1, 2, T0)],  # alice(reader=1) 读 bob(peer=2) 到 T0(含)
    )
    alice = make_conn("alice")
    await deliver_dm_catch_up(alice, sessionmaker=sm)
    out = drain(alice)
    assert [(m.msg_id, m.text) for m in out] == [("m2", "unread")]  # 只补 T0 之后的未读


# ── 补收已读回执:bob 把 alice 发的读到 T1 → alice 连接补收 DMRead{reader=bob, read_through=T1} ──
async def test_catch_up_delivers_read_receipts():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    await _seed_dms(sm, cursors=[(2, 1, T1)])  # bob(reader=2) 读 alice(peer=1) 到 T1
    alice = make_conn("alice")
    await deliver_dm_catch_up(alice, sessionmaker=sm)
    out = drain(alice)
    reads = [m for m in out if isinstance(m, DMRead)]
    assert len(reads) == 1 and reads[0].reader_nick == "bob"
    assert reads[0].read_through == T1 and reads[0].read_through.tzinfo is not None  # 时间戳值 + tz 补回


# ── 补收 outbound 满:首条即停本轮(余项下次重连补;游标未推进、不丢)──
async def test_catch_up_stops_when_outbound_full():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    await _seed_dms(sm, messages=[("m1", 2, 1, "x", T0), ("m2", 2, 1, "y", T1)])
    alice = make_conn("alice")
    alice.outbound = asyncio.Queue(maxsize=1)
    alice.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.INTERNAL, "filler")))  # 占满
    await deliver_dm_catch_up(alice, sessionmaker=sm)  # 不抛;首条 _enqueue_or_stop=False → 停
    out = drain(alice)
    assert len(out) == 1 and isinstance(out[0], ErrorMessage)  # 仅填充物,无 DMDelivered 挤入


# ── 补收空:无未读、无回执 → 不投任何帧 ──
async def test_catch_up_empty_when_nothing_pending():
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    alice = make_conn("alice")
    await deliver_dm_catch_up(alice, sessionmaker=sm)
    assert drain(alice) == []


# ── 补收 me 无 DB 行 → no-op(不投、不抛)──
async def test_catch_up_unknown_user_is_noop():
    sm = await _seeded_sm({"bob": 2})  # 无 alice
    alice = make_conn("alice")
    await deliver_dm_catch_up(alice, sessionmaker=sm)
    assert drain(alice) == []


# ── 补收 DB 读失败(未建表)→ best-effort no-op(不投、不抛)──
async def test_catch_up_db_failure_is_noop():
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    sm = make_sessionmaker(engine)  # 未 create_all
    alice = make_conn("alice")
    await deliver_dm_catch_up(alice, sessionmaker=sm)  # 不抛
    assert drain(alice) == []


# ── 0074·G:改昵称(rekey)落在解析 uid 的 DB await 窗内 → 发件人 nick 被就地改写 ──
async def test_rename_during_uid_lookup_does_not_lose_dm():
    # 修复前:uids 用「旧 nick」建表,await 后却用被 rekey 就地改写的 conn.nick 查 → 必然 miss →
    # 私信静默不落库 + 回发假 INTERNAL。修复后:发件人 nick 全程用进入路由时的快照,键与表天然一致。
    sm = await _seeded_sm({"alice": 1, "bob": 2})
    conns, alice, bob, persist = _both_online()

    real_sm = sm
    def sm_with_rename():
        # 取 session 的那一刻(即 load_uids_by_nicks 正要跑)模拟并发改昵称的 rekey:就地改写 conn.nick
        conns.rekey(alice, "Neo")
        return real_sm()

    await route_direct_message(
        alice, _dm("bob", "hey"), conns=conns, persist=persist, sessionmaker=sm_with_rename
    )
    assert alice.nick == "Neo"  # rekey 确已就地改写(窗口真实发生)
    assert drain(alice) == []  # 修复前:这里会收到假 ErrorMessage(INTERNAL 无 DB 账号行)
    snap = persist.snapshot()
    assert len(snap) == 1 and isinstance(snap[0], DMWrite)
    assert (snap[0].from_uid, snap[0].to_uid, snap[0].text) == (1, 2, "hey")  # 仍按发起时身份落库
    out = drain(bob)
    assert len(out) == 1 and out[0].from_nick == "alice"  # 实时投递也用快照,与落库同源
