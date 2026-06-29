# DB 读路径(查询;与写侧 orm_persister.py 分文件)。载入一次 / 不做实时判定的语义见 storage.md / db.md。

from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.db.models import DMMessage, DMReadCursor, User


def _as_utc(dt: datetime) -> datetime:
    # DM 时间戳一律 UTC(shell 盖 datetime.now(timezone.utc))。sqlite 读 DateTime(timezone=True) 丢 tz 标签 → naive;
    # 补回 UTC,使登录补收的 wire 形(DMDelivered.created_at / DMRead.read_through)与实时路径一致(序列化均带 Z,
    # 守 wire-protocol-guide「同形」契约;见 changes/0040 自 review)。postgres 本就返回 aware,此处幂等无害。
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def load_user_by_nick(
    sessionmaker: async_sessionmaker[AsyncSession], nick: str
) -> tuple[int, int] | None:
    # 按昵称读账号:返回 (uid, points) 供 JoinRoom 富化;无此行返回 None(调用方判内部不一致)。
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.nickname == nick))).scalar_one_or_none()
        return None if user is None else (user.id, user.points)


async def load_uids_by_nicks(
    sessionmaker: async_sessionmaker[AsyncSession], nicks: Iterable[str]
) -> dict[str, int]:
    # 批量 nick → 不可变 uid(私信收发边界做 nick↔uid 转换,见 messaging.md);缺的 nick 不在返回 dict
    # (调用方据「key 缺失」判对端不存在 / 发件人内部不一致)。空入参省一次查询。
    names = list(nicks)
    if not names:
        return {}
    async with sessionmaker() as session:
        rows = (await session.execute(select(User.nickname, User.id).where(User.nickname.in_(names)))).all()
        return {nick: uid for nick, uid in rows}


async def load_unread_dms(
    sessionmaker: async_sessionmaker[AsyncSession], to_uid: int
) -> list[tuple[str, str, str, datetime]]:
    # 登录补收(messaging.md §私信):to_uid 收到、且尚未读的私信,旧→新。返回 (msg_id, from_nick, text, created_at)。
    # 未读判据 = 无该对端已读游标(从没读过 ta)或该消息 created_at 晚于游标。LEFT JOIN 游标 (reader=me, peer=发件人)。
    async with sessionmaker() as session:
        stmt = (
            select(DMMessage.dedupe_key, User.nickname, DMMessage.text, DMMessage.created_at)
            .join(User, User.id == DMMessage.from_uid)  # 取发件人显示名(wire 用 nick)
            .join(
                DMReadCursor,
                and_(DMReadCursor.reader_uid == to_uid, DMReadCursor.peer_uid == DMMessage.from_uid),
                isouter=True,  # 无游标行(从没标读该对端)也要算未读 → 左连接
            )
            .where(DMMessage.to_uid == to_uid)
            .where(
                or_(
                    DMReadCursor.read_through_ts.is_(None),  # 无游标 = 全未读
                    DMMessage.created_at > DMReadCursor.read_through_ts,  # 晚于游标 = 未读
                )
            )
            .order_by(DMMessage.created_at)  # 旧→新,客户端按序渲染
        )
        return [
            (key, nick, text, _as_utc(ts)) for key, nick, text, ts in (await session.execute(stmt)).all()
        ]  # _as_utc:sqlite 读回 naive → 补 UTC,补收 wire 形与实时一致


async def load_read_receipts(
    sessionmaker: async_sessionmaker[AsyncSession], peer_uid: int
) -> list[tuple[str, datetime]]:
    # 登录补收的已读回执(messaging.md 游标一表两用):谁(reader)把我(peer_uid)发的消息读到了几时。
    # 返回 (reader_nick, read_through_ts);供补发 DMRead 回执,让发件人重连后看到对方的已读进度。
    async with sessionmaker() as session:
        stmt = (
            select(User.nickname, DMReadCursor.read_through_ts)
            .join(User, User.id == DMReadCursor.reader_uid)  # 取读者显示名
            .where(DMReadCursor.peer_uid == peer_uid)
        )
        return [(nick, _as_utc(ts)) for nick, ts in (await session.execute(stmt)).all()]  # 同上,补 UTC tz
