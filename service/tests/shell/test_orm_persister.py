"""P4(三之二):OrmPersister 落库写路径穷举(db.md「两类写 / 事务分组 / 幂等」)。

aiosqlite 内存库(StaticPool 共享单连接 + make_engine 装 PRAGMA foreign_keys=ON)真落库,验:
状态写定向 UPDATE(只盖 points、保住 nickname)、事件写 record+participants 同事务 INSERT、dedupe_key
幂等、end_time 契约、批内原子回滚、FK 强制、PersistWriter 端到端 + 失败回灌。
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.pool import StaticPool

from app.core.records import HandRecordWrite, ParticipantWrite, PointsWrite
from app.db.dm_records import DMReadCursorWrite, DMWrite
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import DMMessage, DMReadCursor, HandParticipant, HandRecord, User
from app.db.orm_persister import OrmPersister
from app.shell.persist import PersistWriter, WriteBuffer

T_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
T_END = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
T_DM = datetime(2026, 1, 1, 0, 3, tzinfo=timezone.utc)
T_DM2 = datetime(2026, 1, 1, 0, 9, tzinfo=timezone.utc)
# 保留清理用:OLD < CUTOFF < RECENT(私信「已读且 created_at < cutoff」才删)
T_R_OLD = datetime(2026, 1, 1, tzinfo=timezone.utc)
T_R_CUTOFF = datetime(2026, 3, 1, tzinfo=timezone.utc)
T_R_RECENT = datetime(2026, 6, 1, tzinfo=timezone.utc)


async def _setup(seed=((1, "alice", 1000), (2, "bob", 1000))):
    # 内存 sqlite(StaticPool 让 :memory: 跨连接存活)+ FK 强制(make_engine 装);建表 + 种子 user。
    engine = make_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            for uid, nick, pts in seed:
                s.add(User(id=uid, nickname=nick, points=pts))
    return sm


def _record(key, parts=(), end_time=T_END, final_pot=0, room="r1"):
    return HandRecordWrite(
        dedupe_key=key, room=room, start_time=T_START, final_pot=final_pot, participants=parts, end_time=end_time
    )


def _naive(dt):
    # sqlite 无原生 tz 类型:DateTime(timezone=True) 读回是 naive(UTC 墙值保留、tz 标签丢失;postgres 则保留)。
    # 测试比较墙值即可——记录约定存 UTC,这是 sqlite-dev 的已知特性(见 changes/0028)。
    return dt.replace(tzinfo=None) if dt is not None else None


# ── 状态写:定向 UPDATE 只盖 points,保住 nickname(决策 1:绝不整行 merge 写 NULL)──
async def test_points_write_updates_only_points():
    sm = await _setup()
    await OrmPersister(sm).flush({("user", "1"): PointsWrite(uid=1, points=250)}, [])
    async with sm() as s:
        u = await s.get(User, 1)
        assert u.points == 250
        assert u.nickname == "alice"  # 没被写成 NULL


# ── 状态写:同 uid 给最新值即落最新(WriteBuffer 已按键合并,这里验 UPDATE 落地)──
async def test_state_write_lands_latest_value():
    sm = await _setup()
    await OrmPersister(sm).flush({("user", "1"): PointsWrite(uid=1, points=777)}, [])
    async with sm() as s:
        assert (await s.get(User, 1)).points == 777


# ── 状态写:user 行不存在 → 0 命中、无害(不抛)──
async def test_points_write_missing_user_is_noop():
    sm = await _setup()
    await OrmPersister(sm).flush({("user", "99"): PointsWrite(uid=99, points=500)}, [])
    async with sm() as s:
        assert await s.get(User, 99) is None


# ── 事件写:record + participants 同事务 INSERT,FK 正确、字段对齐 ──
async def test_hand_record_inserts_record_and_participants():
    sm = await _setup()
    rec = _record(
        "r1:1",
        parts=(
            ParticipantWrite(uid=1, initial_points=100, final_points=180),
            ParticipantWrite(uid=2, initial_points=100, final_points=20),
        ),
        final_pot=200,
    )
    await OrmPersister(sm).flush({}, [rec])
    async with sm() as s:
        hr = (await s.execute(select(HandRecord).where(HandRecord.dedupe_key == "r1:1"))).scalar_one()
        assert hr.final_pot == 200
        assert hr.room == "r1"  # room 列随记录落库(0052,供 GET /hands?room= 过滤)
        assert (_naive(hr.start_time), _naive(hr.end_time)) == (_naive(T_START), _naive(T_END))
        parts = (
            await s.execute(select(HandParticipant).where(HandParticipant.hand_id == hr.id))
        ).scalars().all()
        assert {(x.uid, x.initial_points, x.final_points) for x in parts} == {
            (1, 100, 180),
            (2, 100, 20),
        }


# ── 事件写幂等:同 dedupe_key 落两次 → 只一行 record + 一行 participant(SELECT-then-INSERT 跳过)──
async def test_hand_record_idempotent_on_dedupe_key():
    sm = await _setup()
    rec = _record("r1:1", parts=(ParticipantWrite(uid=1, initial_points=100, final_points=100),))
    p = OrmPersister(sm)
    await p.flush({}, [rec])
    await p.flush({}, [rec])  # 重放(崩溃后 / drain 重试)
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(HandRecord)) == 1
        assert await s.scalar(select(func.count()).select_from(HandParticipant)) == 1


# ── 幂等:同 dedupe_key 在**同一批**出现两次 → 只一行(批内 SELECT 见到刚 flush 的行而跳过)──
async def test_hand_record_dedupe_within_single_batch():
    sm = await _setup()
    rec = _record("r1:1", parts=(ParticipantWrite(uid=1, initial_points=100, final_points=100),))
    await OrmPersister(sm).flush({}, [rec, rec])  # 同批两份
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(HandRecord)) == 1
        assert await s.scalar(select(func.count()).select_from(HandParticipant)) == 1


# ── end_time 契约:dispatch 未盖(None)→ flush 抛 ValueError(调用方违约)──
async def test_hand_record_end_time_none_raises():
    sm = await _setup()
    with pytest.raises(ValueError):
        await OrmPersister(sm).flush({}, [_record("r1:1", end_time=None)])


# ── 批内原子:状态写 + 中途抛错的事件写同批 → 整批回滚(points 不变、无 record)──
async def test_batch_rolls_back_atomically_on_event_error():
    sm = await _setup()
    with pytest.raises(ValueError):
        await OrmPersister(sm).flush(
            {("user", "1"): PointsWrite(uid=1, points=999)},
            [_record("r1:1", end_time=None)],  # end_time None → 中途 ValueError
        )
    async with sm() as s:
        assert (await s.get(User, 1)).points == 1000  # 状态写也回滚了
        assert await s.scalar(select(func.count()).select_from(HandRecord)) == 0


# ── FK 强制:参与者 uid 无对应 user → IntegrityError,整批回滚(record 也不留)──
async def test_participant_fk_violation_raises_and_rolls_back():
    sm = await _setup()
    rec = _record("r1:1", parts=(ParticipantWrite(uid=999, initial_points=1, final_points=1),))
    with pytest.raises(Exception):  # noqa: B017  IntegrityError(FK)
        await OrmPersister(sm).flush({}, [rec])
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(HandRecord)) == 0


# ── 私信事件写:DMWrite → DMMessage INSERT,字段对齐(changes/0038)──
async def test_dm_write_inserts_message():
    sm = await _setup()
    dm = DMWrite(dedupe_key="m1", from_uid=1, to_uid=2, text="hey bob", created_at=T_DM)
    await OrmPersister(sm).flush({}, [dm])
    async with sm() as s:
        row = (await s.execute(select(DMMessage).where(DMMessage.dedupe_key == "m1"))).scalar_one()
        assert (row.from_uid, row.to_uid, row.text) == (1, 2, "hey bob")
        assert _naive(row.created_at) == _naive(T_DM)


# ── 私信幂等:同 dedupe_key 落两次 → 只一行(SELECT-then-INSERT 跳过,同手牌记录)──
async def test_dm_write_idempotent_on_dedupe_key():
    sm = await _setup()
    dm = DMWrite(dedupe_key="m1", from_uid=1, to_uid=2, text="hi", created_at=T_DM)
    p = OrmPersister(sm)
    await p.flush({}, [dm])
    await p.flush({}, [dm])  # 重放(drain / 崩溃后)
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(DMMessage)) == 1


# ── 私信幂等:同 dedupe_key 同一批两份 → 只一行(批内 SELECT 见刚 add 的行)──
async def test_dm_write_dedupe_within_single_batch():
    sm = await _setup()
    dm = DMWrite(dedupe_key="m1", from_uid=1, to_uid=2, text="hi", created_at=T_DM)
    await OrmPersister(sm).flush({}, [dm, dm])
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(DMMessage)) == 1


# ── 私信 FK 强制:to_uid 无对应 user → IntegrityError,整批回滚(无行落库)──
async def test_dm_write_fk_violation_raises_and_rolls_back():
    sm = await _setup()
    dm = DMWrite(dedupe_key="m1", from_uid=1, to_uid=999, text="hi", created_at=T_DM)  # to_uid=999 无此 user
    with pytest.raises(Exception):  # noqa: B017  IntegrityError(FK)
        await OrmPersister(sm).flush({}, [dm])
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(DMMessage)) == 0


# ── 已读游标状态写:首次 → INSERT 新行(行非必存,走 UPSERT 而非纯 UPDATE,changes/0039)──
async def test_dm_cursor_inserts_when_absent():
    sm = await _setup()
    cur = DMReadCursorWrite(reader_uid=1, peer_uid=2, read_through_ts=T_DM)
    await OrmPersister(sm).flush({("dm_cursor", "1", "2"): cur}, [])
    async with sm() as s:
        row = await s.get(DMReadCursor, (1, 2))
        assert row is not None and _naive(row.read_through_ts) == _naive(T_DM)


# ── 已读游标状态写:已存在 → UPDATE 覆盖 read_through_ts,不新增行 ──
async def test_dm_cursor_updates_when_present():
    sm = await _setup()
    p = OrmPersister(sm)
    await p.flush({("dm_cursor", "1", "2"): DMReadCursorWrite(reader_uid=1, peer_uid=2, read_through_ts=T_DM)}, [])
    await p.flush({("dm_cursor", "1", "2"): DMReadCursorWrite(reader_uid=1, peer_uid=2, read_through_ts=T_DM2)}, [])
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(DMReadCursor)) == 1  # 同 (reader,peer) 仍一行
        assert _naive((await s.get(DMReadCursor, (1, 2))).read_through_ts) == _naive(T_DM2)  # 覆盖为最新


# ── 已读游标 FK 强制:peer_uid 无对应 user → IntegrityError,整批回滚 ──
async def test_dm_cursor_fk_violation_raises_and_rolls_back():
    sm = await _setup()
    cur = DMReadCursorWrite(reader_uid=1, peer_uid=999, read_through_ts=T_DM)  # peer 999 无此 user
    with pytest.raises(Exception):  # noqa: B017  IntegrityError(FK)
        await OrmPersister(sm).flush({("dm_cursor", "1", "999"): cur}, [])
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(DMReadCursor)) == 0


# ── 保留清理:删「已读 + 过期」,留「未读(虽老)」与「已读但未过期」(db.md / changes/0041)──
async def test_cleanup_dms_deletes_read_and_expired_only():
    sm = await _setup(seed=((1, "alice", 1000), (2, "bob", 1000), (3, "carol", 1000)))
    p = OrmPersister(sm)
    await p.flush(
        {},
        [
            DMWrite(dedupe_key="read_old", from_uid=2, to_uid=1, text="x", created_at=T_R_OLD),
            DMWrite(dedupe_key="read_recent", from_uid=2, to_uid=1, text="y", created_at=T_R_RECENT),
            DMWrite(dedupe_key="unread_old", from_uid=3, to_uid=1, text="z", created_at=T_R_OLD),  # 无游标 → 未读
        ],
    )
    # alice(1) 读 bob(2) 到 T_R_RECENT(覆盖 read_old@OLD 与 read_recent@RECENT);未覆盖 carol(3)
    await p.flush(
        {("dm_cursor", "1", "2"): DMReadCursorWrite(reader_uid=1, peer_uid=2, read_through_ts=T_R_RECENT)}, []
    )
    deleted = await p.cleanup_dms(T_R_CUTOFF)
    assert deleted == 1  # 只 read_old(已读 + created_at < cutoff)
    async with sm() as s:
        keys = {k for (k,) in (await s.execute(select(DMMessage.dedupe_key))).all()}
    assert keys == {"read_recent", "unread_old"}  # 已读未过期 + 未读(虽老)均留


# ── 保留清理:未读永不删(即便远早于 cutoff)──
async def test_cleanup_dms_keeps_unread_however_old():
    sm = await _setup()  # alice/bob;无游标 → m1 未读
    p = OrmPersister(sm)
    await p.flush({}, [DMWrite(dedupe_key="m1", from_uid=2, to_uid=1, text="x", created_at=T_R_OLD)])
    assert await p.cleanup_dms(T_R_RECENT) == 0  # cutoff 很晚、m1 很老但未读 → 不删
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(DMMessage)) == 1


# ── 保留清理:已读但未过期(created_at >= cutoff)留 ──
async def test_cleanup_dms_keeps_read_but_unexpired():
    sm = await _setup()
    p = OrmPersister(sm)
    await p.flush({}, [DMWrite(dedupe_key="m1", from_uid=2, to_uid=1, text="x", created_at=T_R_RECENT)])
    await p.flush(
        {("dm_cursor", "1", "2"): DMReadCursorWrite(reader_uid=1, peer_uid=2, read_through_ts=T_R_RECENT)}, []
    )
    assert await p.cleanup_dms(T_R_CUTOFF) == 0  # 已读但 created_at(RECENT) >= cutoff → 未过期,留
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(DMMessage)) == 1


# ── 保留清理:空库返 0 ──
async def test_cleanup_dms_empty_returns_zero():
    sm = await _setup()
    assert await OrmPersister(sm).cleanup_dms(T_R_RECENT) == 0


# ── 边界:created_at == cutoff(已读)→ 留(锁死严格 `<`,非 `<=`)──
async def test_cleanup_dms_keeps_message_exactly_at_cutoff():
    sm = await _setup()
    p = OrmPersister(sm)
    await p.flush({}, [DMWrite(dedupe_key="m1", from_uid=2, to_uid=1, text="x", created_at=T_R_CUTOFF)])
    await p.flush(
        {("dm_cursor", "1", "2"): DMReadCursorWrite(reader_uid=1, peer_uid=2, read_through_ts=T_R_RECENT)}, []
    )
    assert await p.cleanup_dms(T_R_CUTOFF) == 0  # created_at == cutoff 不满足 `< cutoff` → 留(若误写 `<=` 会误删)
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(DMMessage)) == 1


# ── 边界:read_through_ts == created_at(恰读到该条)+ 过期 → 删(锁死 inclusive `>=`,非 `>`)──
async def test_cleanup_dms_deletes_when_read_through_equals_created_at():
    sm = await _setup()
    p = OrmPersister(sm)
    await p.flush({}, [DMWrite(dedupe_key="m1", from_uid=2, to_uid=1, text="x", created_at=T_R_OLD)])
    await p.flush(
        {("dm_cursor", "1", "2"): DMReadCursorWrite(reader_uid=1, peer_uid=2, read_through_ts=T_R_OLD)}, []
    )  # 游标恰好读到 m1(read_through == created_at)
    assert await p.cleanup_dms(T_R_CUTOFF) == 1  # 已读(inclusive)+ 过期 → 删(若误写 `>` 会判未读、漏删)
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(DMMessage)) == 0


# ── 关联隔离:收件人只读过**别的对端**(carol)的消息 → bob 发来的仍未读、不删(锁死 peer==from_uid 关联)──
async def test_cleanup_dms_wrong_peer_cursor_does_not_overmatch():
    sm = await _setup(seed=((1, "alice", 1000), (2, "bob", 1000), (3, "carol", 1000)))
    p = OrmPersister(sm)
    await p.flush({}, [DMWrite(dedupe_key="from_bob", from_uid=2, to_uid=1, text="x", created_at=T_R_OLD)])
    await p.flush(
        {("dm_cursor", "1", "3"): DMReadCursorWrite(reader_uid=1, peer_uid=3, read_through_ts=T_R_RECENT)}, []
    )  # alice 读的是 carol(3) 的进度,与 bob(2) 无关
    assert await p.cleanup_dms(T_R_CUTOFF) == 0  # from_bob 无 (alice,bob) 游标 → 未读,虽老不删(关联非常量)
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(DMMessage)) == 1


# ── 端到端:PersistWriter.flush_once 把缓冲一批经 OrmPersister 落库并清空 ──
async def test_persistwriter_end_to_end_with_orm():
    sm = await _setup()
    buf = WriteBuffer()
    buf.put(PointsWrite(uid=1, points=300))
    buf.put(_record("r1:1", parts=(ParticipantWrite(uid=1, initial_points=50, final_points=120),)))
    writer = PersistWriter(buf, OrmPersister(sm), flush_interval_s=0.001, drain_timeout_s=0.5)
    assert await writer.flush_once() is True
    assert buf.is_empty()
    async with sm() as s:
        assert (await s.get(User, 1)).points == 300
        assert await s.scalar(select(func.count()).select_from(HandRecord)) == 1


# ── 端到端失败回灌:OrmPersister 抛(FK 坏)→ flush_once 回灌、缓冲非空、待下批重试 ──
async def test_persistwriter_requeues_on_orm_failure():
    sm = await _setup()
    buf = WriteBuffer()
    buf.put(_record("r1:1", parts=(ParticipantWrite(uid=999, initial_points=1, final_points=1),)))
    writer = PersistWriter(buf, OrmPersister(sm), flush_interval_s=0.001, drain_timeout_s=0.5, max_retry=100)
    assert await writer.flush_once() is True
    assert not buf.is_empty()  # 整批回灌,留待重试(毒丸阈值 100,未触发)
