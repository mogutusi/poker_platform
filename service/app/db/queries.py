# DB 读路径(查询;与写侧 orm_persister.py 分文件)。载入一次 / 不做实时判定的语义见 storage.md / db.md。

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import NamedTuple

from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from app.db.models import DMMessage, DMReadCursor, HandParticipant, HandRecord, User


class LoginUser(NamedTuple):
    # 登录握手(auth.md)按 name 载入的账号鉴权投影(changes/0056)。name 命中 → 非 NULL;
    # hash_password/k_user 可能 NULL(name 设了但未启用登录)→ 由 authenticate fail-closed。
    uid: int  # 不可变账号主键(= User.id)
    name: str  # 登录账号(唯一,不可变)
    nickname: str  # 游戏昵称(握手后投 Connect)
    hash_password: str | None  # 密码哈希 "salt$rounds$digest"(0053);NULL = 未设密码
    k_user: str | None  # SM4 密钥 hex;NULL = 未发密钥(未启用登录)


async def load_user_for_login(
    sessionmaker: async_sessionmaker[AsyncSession], name: str
) -> LoginUser | None:
    # 按登录账号 name 读鉴权字段供 /user/login(auth.md §登录握手)。无此行返回 None
    # (name 唯一 + NULL 不匹配 → 未启用登录的历史行天然跳过)。秘密 hash_password/k_user 只回给 authenticate,不进日志。
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.name == name))).scalar_one_or_none()
        if user is None:
            return None
        return LoginUser(user.id, user.name, user.nickname, user.hash_password, user.k_user)


async def load_profile_by_name(
    sessionmaker: async_sessionmaker[AsyncSession], name: str
) -> tuple[str, str, int] | None:
    # 按登录账号 name 读资料投影 (name, nickname, points) 供 POST /user/me(rest.md §用户资料)。
    # points 是 DB 结算值(滞后;精确余额在 ws,rest.md 共同原则 1);无此行返回 None(会话在、行没了 = 内部不一致)。
    # 与 load_user_for_login 分开:资料投影不带 hash_password/k_user 秘密列,端点拿不到就漏不了。
    async with sessionmaker() as session:
        user = (await session.execute(select(User).where(User.name == name))).scalar_one_or_none()
        return None if user is None else (user.name, user.nickname, user.points)


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


async def top_users_by_points(
    sessionmaker: async_sessionmaker[AsyncSession], limit: int
) -> list[tuple[str, int]]:
    # 排行榜(rest.md §排行榜):按 DB 结算积分降序取前 limit,返回 (nickname, points)。
    # points 是**结算后全局积分**(桌上筹码 Seat.points 内存不落库,storage.md)。同分按 nickname 升序 → rank 稳定可复现。
    async with sessionmaker() as session:
        stmt = (
            select(User.nickname, User.points)
            .order_by(User.points.desc(), User.nickname.asc())
            .limit(limit)
        )
        return [(nick, pts) for nick, pts in (await session.execute(stmt)).all()]


async def list_hands(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    room: str | None = None,
    participant_uid: int | None = None,
    before_id: int | None = None,
    limit: int,
) -> list[tuple[int, str, datetime, datetime, int, tuple[tuple[str, int, int], ...]]]:
    # 手牌历史(rest.md §手牌历史):新→旧游标分页。每手返回 (id, dedupe_key, start, end, final_pot, participants),
    # participants = ((nickname, initial_points, final_points)...) 按 nickname 升序。游标 = HandRecord.id(单调唯一,事件写按手尾追加):
    # before_id 给则取 id<before_id(严格小于,不重上页末条);participant_uid 给则只取该玩家参与过的手(仍返回该手全部参与者)。
    # 隐私:HandRecord/HandParticipant 只存结果、绝无底牌(models.py / core.md 不变量 3),查询天然无牌面。一会话两查(手 + 其参与者)避 N+1。
    async with sessionmaker() as session:
        stmt = select(
            HandRecord.id, HandRecord.dedupe_key, HandRecord.start_time, HandRecord.end_time, HandRecord.final_pot
        )
        if room is not None:
            stmt = stmt.where(HandRecord.room == room)  # 精确匹配 room 列(健壮,免 dedupe_key LIKE,见 changes/0052)
        if participant_uid is not None:
            stmt = stmt.where(
                HandRecord.id.in_(select(HandParticipant.hand_id).where(HandParticipant.uid == participant_uid))
            )
        if before_id is not None:
            stmt = stmt.where(HandRecord.id < before_id)
        stmt = stmt.order_by(HandRecord.id.desc()).limit(limit)
        hand_rows = (await session.execute(stmt)).all()
        hand_ids = [hid for hid, *_ in hand_rows]
        parts: dict[int, list[tuple[str, int, int]]] = {}
        if hand_ids:
            pstmt = (
                select(
                    HandParticipant.hand_id,
                    User.nickname,
                    HandParticipant.initial_points,
                    HandParticipant.final_points,
                )
                .join(User, User.id == HandParticipant.uid)  # 取参与者显示名
                .where(HandParticipant.hand_id.in_(hand_ids))
            )
            for hid, nick, init, fin in (await session.execute(pstmt)).all():
                parts.setdefault(hid, []).append((nick, init, fin))
        return [
            (hid, dk, _as_utc(st), _as_utc(et), pot, tuple(sorted(parts.get(hid, []))))
            for hid, dk, st, et, pot in hand_rows
        ]


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
