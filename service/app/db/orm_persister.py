# Persister 真实现(P4 三之二):to_orm(delayDB Write 载荷 → ORM)+ OrmPersister(async session 落库)。
# 落库语义见 db.md「两类写 / 事务分组」:状态写=定向列 UPDATE(只盖 Write 携带的列)、事件写=幂等 INSERT。
# 不 import shell:靠结构化协议(duck typing)满足 shell/persist.py 的 Persister;只 import core.records + db.models + sqlalchemy。

import logging
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.events import PersistPayload
from app.core.records import HandRecordWrite, PointsWrite
from app.db.dm_records import DMReadCursorWrite, DMWrite
from app.db.models import DMMessage, DMReadCursor, HandParticipant, HandRecord, User

log = logging.getLogger(__name__)

# StateKey 与 shell/persist.py 同义(= 状态写覆盖键),此处内联其结构以免 app/db import shell(守分层)。
StateKey = tuple[str, ...]


class OrmPersister:
    # delayDB 落库后端:PersistWriter 周期把一批写交本类的 flush。全进程唯一 DB 写者 ⇒ 无行锁、无并发竞争。
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def flush(
        self, dirty: dict[StateKey, PersistPayload], appends: list[PersistPayload]
    ) -> None:
        # 一批一个短事务:状态写 + 事件写同事务;任意一步抛(IntegrityError / 取消)整批回滚 → PersistWriter 回灌重试。
        # 重试幂等安全:状态写是覆盖式 UPDATE、事件写靠 dedupe_key SELECT 去重(见下)。
        async with self._sessionmaker() as session:
            async with session.begin():
                for payload in dirty.values():
                    await self._apply_state_write(session, payload)
                for payload in appends:
                    await self._apply_event_write(session, payload)

    async def cleanup_dms(self, cutoff: datetime) -> int:
        # 私信保留清理(db.md / changes/0041):删「已读 且 created_at < cutoff」的私信,返删除行数(供日志)。
        # 已读 = 收件人对该发件人的游标读过该条(EXISTS 子查询;read_through_ts >= created_at,与 0040 未读判据互补)。
        # 未读(无满足游标)永不删;已读但 created_at >= cutoff(未过期)留。DELETE 归唯一写者,一短事务。
        already_read = (
            select(DMReadCursor.reader_uid)
            .where(DMReadCursor.reader_uid == DMMessage.to_uid)  # 收件人 = 读者
            .where(DMReadCursor.peer_uid == DMMessage.from_uid)  # 发件人 = 对端
            .where(DMReadCursor.read_through_ts >= DMMessage.created_at)  # 读过该条(inclusive)
            .exists()
        )
        async with self._sessionmaker() as session:
            async with session.begin():
                result = await session.execute(
                    delete(DMMessage).where(DMMessage.created_at < cutoff).where(already_read)
                )
                return result.rowcount or 0  # rowcount 删除行数(sqlite/pg 可靠);None/-1 兜 0

    async def _apply_state_write(self, session: AsyncSession, payload: PersistPayload) -> None:
        match payload:
            case PointsWrite(uid=uid, points=points):
                # 定向 UPDATE:只盖 points 列,绝不碰 nickname 等 PointsWrite 不拥有的列(否则 merge 会把 nickname 写 NULL)。
                # 内存权威 + 载入一次 ⇒ 写积分时 user 行必已存在;行不存在则 0 命中、无害(dev 未种子时如此)。
                await session.execute(update(User).where(User.id == uid).values(points=points))
            case DMReadCursorWrite(reader_uid=reader, peer_uid=peer, read_through_ts=ts):
                await self._upsert_dm_cursor(session, reader, peer, ts)
            case _:
                log.warning("OrmPersister unknown state write %s, skipped", type(payload).__name__)

    async def _upsert_dm_cursor(
        self, session: AsyncSession, reader: int, peer: int, ts: datetime
    ) -> None:
        # 已读游标 UPSERT(状态写,但行可能不预存——首次读某会话):唯一写者 ⇒ SELECT-by-PK → 无则 INSERT、有则改
        # read_through_ts(race-free、跨方言,免 ON CONFLICT 二分;同 _insert_dm 思路)。不同于 PointsWrite 的纯 UPDATE
        # (User 行 seed/load 必存),游标行非必存,故走 UPSERT(见 db.md 状态写「行可能不预存」子情形 / changes/0039)。
        existing = await session.get(DMReadCursor, (reader, peer))  # 复合主键按 (reader_uid, peer_uid) 顺序传元组
        if existing is None:
            session.add(DMReadCursor(reader_uid=reader, peer_uid=peer, read_through_ts=ts))
        else:
            existing.read_through_ts = ts  # 后写覆盖:只留最新进度(状态写语义)

    async def _apply_event_write(self, session: AsyncSession, payload: PersistPayload) -> None:
        match payload:
            case HandRecordWrite():
                await self._insert_hand_record(session, payload)
            case DMWrite():
                await self._insert_dm(session, payload)
            case _:
                log.warning("OrmPersister unknown event write %s, skipped", type(payload).__name__)

    async def _insert_dm(self, session: AsyncSession, payload: DMWrite) -> None:
        # 私信幂等 INSERT(同 _insert_hand_record):唯一写者 ⇒ 先 SELECT dedupe_key 在不在、不在才插
        # (race-free、跨方言;unique 索引兜底)。重放(drain / 崩溃后重试)安全跳过。FK(from/to_uid→user)
        # 由方言强制(sqlite 装 PRAGMA),坏 uid → IntegrityError → 整批回滚。
        existing = await session.scalar(
            select(DMMessage.id).where(DMMessage.dedupe_key == payload.dedupe_key)
        )
        if existing is not None:
            return
        session.add(
            DMMessage(
                dedupe_key=payload.dedupe_key,
                from_uid=payload.from_uid,
                to_uid=payload.to_uid,
                text=payload.text,
                created_at=payload.created_at,
            )
        )

    async def _insert_hand_record(self, session: AsyncSession, payload: HandRecordWrite) -> None:
        # 幂等:全进程唯一写者 ⇒ 先查 dedupe_key 在不在、不在才插,无并发竞争(race-free)、跨方言(免 sqlite/pg 的 ON CONFLICT 二分)。
        # dedupe_key 的 unique 索引仍是兜底:真撞 → IntegrityError → 整批回滚,下批 SELECT 即见、跳过。
        existing = await session.scalar(
            select(HandRecord.id).where(HandRecord.dedupe_key == payload.dedupe_key)
        )
        if existing is not None:
            return  # 已落过(崩溃后重放 / drain 重试),整单跳过
        if payload.end_time is None:
            # 契约:end_time 由 shell 在 dispatch 盖墙钟(见 db.md / changes/0028 决策 4);到此仍 None = 调用方违约。
            raise ValueError(f"HandRecordWrite.end_time 未盖戳(dedupe_key={payload.dedupe_key})")
        record = HandRecord(
            dedupe_key=payload.dedupe_key,
            room=payload.room,
            start_time=payload.start_time,
            end_time=payload.end_time,
            final_pot=payload.final_pot,
        )
        session.add(record)
        await session.flush()  # flush 取自增 record.id,供参与者 FK 引用(同事务,未 commit)
        for part in payload.participants:
            session.add(
                HandParticipant(
                    hand_id=record.id,
                    uid=part.uid,
                    initial_points=part.initial_points,
                    final_points=part.final_points,
                )
            )
