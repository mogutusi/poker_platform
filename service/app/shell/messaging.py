# 私信(DM)shell 路由(见 messaging.md §私信)。**不进 GameLoop / 不碰 world**:私聊定向到一个人、天然跨房,
# 只需 nick→连接表(shell 拥有)+ nick→uid(读 DB),无法 checkout 单房。DB 权威:发即落库 DMWrite(未读),
# 在线再叠加实时投 DMDelivered(尽力而为)。是 outbound + 写缓冲的「第二个生产者」(put 同步无 await,守不变量 3)。

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.core.errors import Err, ErrorCode
from app.db.dm_records import DMReadCursorWrite, DMWrite
from app.db.queries import as_utc, load_read_receipts, load_uids_by_nicks, load_unread_dms
from app.shell.connection import Connection, ConnectionManager
from app.shell.persist import WriteBuffer
from app.wire import client as wire_client
from app.wire.server import DMDelivered, DMRead, DMUndelivered, ErrorMessage, ServerMessage

log = logging.getLogger(__name__)


async def route_direct_message(
    conn: Connection,
    msg: wire_client.DirectMessage,
    *,
    conns: ConnectionManager,
    persist: WriteBuffer,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # 防护序(同 0033 房聊):空 → 超长 → 自发 → 限速 先拒(廉价、不耗令牌、不读 DB),合法再过桶,过桶才读 DB。
    text = msg.text
    if not text.strip():  # 非空(strip 后判据)
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.INVALID_MESSAGE, "私信文本不能为空")))
        return
    if len(text) > gameconfig.DM_MAX_TEXT_LEN:  # 超长(按原文长度)
        conn.outbound.put_nowait(
            ErrorMessage.from_err(
                Err(ErrorCode.MESSAGE_TOO_LONG, f"私信文本超 {gameconfig.DM_MAX_TEXT_LEN} 字符上限")
            )
        )
        return
    if msg.to_nick == conn.nick:  # 禁止发给自己(身份取连接 nick,不信报文)
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.CANNOT_DM_SELF, "不能给自己发私信")))
        return
    if conn.dm_bucket is None or not conn.dm_bucket.try_consume(time.monotonic()):  # 内容合法才耗令牌
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.RATE_LIMITED, "私信发送过频,请稍候")))
        return

    # 解析 from/to nick → 不可变 uid(收发边界 nick↔uid 转换;落库与游标按 uid 不按可变 nick,见 messaging.md)。
    # **发件人 nick 必须快照**(0074·G):改昵称的 ConnectionManager.rekey 会**就地改写** conn.nick,
    # 若下面 await 后再读 conn.nick,就会拿「改写后的新 nick」去查「用旧 nick 建的表」→ 必然 miss →
    # 私信静默不落库 + 回发假 INTERNAL。全程用同一个快照,键与表天然一致。
    sender_nick = conn.nick
    try:
        uids = await load_uids_by_nicks(sessionmaker, (sender_nick, msg.to_nick))
    except Exception:
        log.exception("direct_message DB read failed from=%s to=%s", sender_nick, msg.to_nick)
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.INTERNAL, "私信读 DB 失败")))
        return
    to_uid = uids.get(msg.to_nick)
    if to_uid is None:  # 对端根本不存在 = 硬错误回执(离线不算,见 messaging.md;离线只落库、登录补收 0040)
        conn.outbound.put_nowait(DMUndelivered(to_nick=msg.to_nick))
        return
    from_uid = uids.get(sender_nick)
    if from_uid is None:  # 鉴权说有发件人、DB 无行 = 内部不一致(同 _build_join;dev 握手已拒非种子 nick)
        conn.outbound.put_nowait(
            ErrorMessage.from_err(Err(ErrorCode.INTERNAL, f"用户 {sender_nick} 无 DB 账号行"))
        )
        return

    msg_id = uuid.uuid4().hex  # 幂等键(比 from_uid:微秒 稳:免同微秒撞键);shell 生成,DB 权威
    created_at = datetime.now(timezone.utc)  # shell 盖墙钟(core 不读钟;DM 全在 shell,可读)
    persist.put(DMWrite(dedupe_key=msg_id, from_uid=from_uid, to_uid=to_uid, text=text, created_at=created_at))  # 必落=未读

    recipient = conns.get(msg.to_nick)  # 在线判断 = ConnectionManager nick 表(presence;离线 None)
    if recipient is not None:  # 在线 → 实时投递(尽力而为;离线仅落库,0040 登录补收)
        _try_deliver(
            recipient, DMDelivered(msg_id=msg_id, from_nick=sender_nick, text=text, created_at=created_at)
        )  # from_nick 同用快照:与落库的 from_uid 同源,免「库里记旧身份、实时投递显新名」


async def route_dm_mark_read(
    conn: Connection,
    msg: wire_client.DMMarkRead,
    *,
    conns: ConnectionManager,
    persist: WriteBuffer,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # 标记已读(messaging.md §私信):reader=连接 nick(不信报文)、peer=msg.peer_nick、read_through=客户端回传。
    # 解析 uid → put(DMReadCursorWrite)(状态写,按 (reader,peer) 覆盖)→ peer 在线则 enqueue(DMRead) 回执。
    # v1 不限速:游标状态写幂等(同键覆盖,刷 N 次只落 1 次)、廉价,同 FetchRoomChat 免限速判据(背压兜洪泛)。
    reader_nick = conn.nick  # 快照:rekey 会就地改写 conn.nick,await 后再读会与建表键不一致(0074·G,同 route_direct_message)
    if msg.peer_nick == reader_nick:  # 无自己↔自己会话(对称 DM 禁自发)
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.CANNOT_DM_SELF, "不能标记与自己的会话已读")))
        return
    try:
        uids = await load_uids_by_nicks(sessionmaker, (reader_nick, msg.peer_nick))
    except Exception:
        log.exception("dm_mark_read DB read failed reader=%s peer=%s", reader_nick, msg.peer_nick)
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.INTERNAL, "标记已读读 DB 失败")))
        return
    peer_uid = uids.get(msg.peer_nick)
    if peer_uid is None:  # 标读一个不存在的会话 = 畸形请求(非投递语义,故 INVALID_MESSAGE 而非 DMUndelivered)
        conn.outbound.put_nowait(
            ErrorMessage.from_err(Err(ErrorCode.INVALID_MESSAGE, f"未知对端 {msg.peer_nick}"))
        )
        return
    reader_uid = uids.get(reader_nick)
    if reader_uid is None:  # 鉴权说有读者、DB 无行 = 内部不一致(同 route_direct_message)
        conn.outbound.put_nowait(
            ErrorMessage.from_err(Err(ErrorCode.INTERNAL, f"用户 {reader_nick} 无 DB 账号行"))
        )
        return
    # 游标不许指向未来(BUG-11 的另一半,0098):`created_at` 全由 shell 盖钟,客户端回传的值源自它收到的
    # `DMDelivered.created_at`,所以合法游标不可能超过此刻。不钳的话,一个远期游标会让「什么都读过了」——
    # 此后到达的私信永远不进登录补收,过了保留期还会被 cleanup_dms 当「已读且过期」真删掉(未读永不删的保护
    # 就此失效)。
    # **先归一再取小,存的必须就是比过的那个值**:客户端送的可能是 naive、也可能带非 UTC 偏移。sqlite 落
    # `DateTime(timezone=True)` 时**丢掉 tz 标签、只存墙钟数字**,所以原样存一个 `+08:00` 的「过去」时刻,
    # 读回来会被当成 UTC —— 凭空变成 8 小时后的未来游标,恰好绕过这里的钳位(自 review 实测,见 changes/0098)。
    # 两步都不能省:`as_utc` 只给 naive 补标签,`astimezone` 才把带别的偏移的值真正换算到 UTC。
    # 只做前者的话,一个 `+08:00` 的合法过去时刻会原样落库 → 丢标签 → 读回当 UTC → 跳到 8 小时后。
    now = datetime.now(timezone.utc)  # shell 盖墙钟(同 route_direct_message)
    read_through = min(as_utc(msg.read_through).astimezone(timezone.utc), now)
    persist.put(
        DMReadCursorWrite(reader_uid=reader_uid, peer_uid=peer_uid, read_through_ts=read_through)
    )  # 状态写:按 (reader,peer) 覆盖只留最新进度;回拨由唯一写者挡(见 orm_persister._upsert_dm_cursor)
    peer = conns.get(msg.peer_nick)  # 发件人(对端)在线判断
    if peer is not None:  # 在线 → 实时回执(尽力而为,同 DMDelivered)
        _try_deliver(peer, DMRead(reader_nick=reader_nick, read_through=read_through))  # 回执报采纳后的值,别报客户端自报的


async def deliver_dm_catch_up(
    conn: Connection, *, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # 登录补收(messaging.md §私信):(重)连时 shell 读 DB → 补发未读 DMDelivered 列表 + 已读回执 DMRead 列表
    # 到本连接 outbound。**不进 GameLoop / 不读 world**(纯 DB 读 + outbound)。每次连都跑(幂等:客户端按 msg_id 去重)。
    # best-effort:DB 读失败 → log + return(连接刚建,不回错;下次重连重试)。键用 uid、wire 转 nick(查询里已 JOIN User)。
    try:
        uids = await load_uids_by_nicks(sessionmaker, (conn.nick,))
        me_uid = uids.get(conn.nick)
        if me_uid is None:
            return  # 无 DB 账号行(dev 握手已拒非种子 nick;防御性早退,无补可发)
        unread = await load_unread_dms(sessionmaker, me_uid)  # (msg_id, from_nick, text, created_at) 旧→新
        receipts = await load_read_receipts(sessionmaker, me_uid)  # (reader_nick, read_through_ts)
    except Exception:
        # 补收非致命(无 DB / 抖动):跳过本轮,下次重连重试。warning 不带 traceback(避免每连噪声)。
        log.warning("dm catch-up skipped (DB read failed) nick=%s", conn.nick)
        return
    for msg_id, from_nick, text, created_at in unread:
        if not _enqueue_or_stop(
            conn, DMDelivered(msg_id=msg_id, from_nick=from_nick, text=text, created_at=created_at)
        ):
            log.warning("dm catch-up truncated (outbound full) nick=%s", conn.nick)
            return  # outbound 满:停本轮,余项下次重连补(游标未推进,不丢)
    for reader_nick, read_through in receipts:
        if not _enqueue_or_stop(conn, DMRead(reader_nick=reader_nick, read_through=read_through)):
            log.warning("dm catch-up truncated (outbound full) nick=%s", conn.nick)
            return


def _enqueue_or_stop(conn: Connection, msg: ServerMessage) -> bool:
    # 补收专用:入队成功返 True;outbound 满返 False(调用方停本轮——余项下次重连补,因游标未因补收推进故不丢)。
    try:
        conn.outbound.put_nowait(msg)
        return True
    except asyncio.QueueFull:
        return False


def _try_deliver(recipient: Connection, msg: ServerMessage) -> None:
    # 实时投递/回执尽力而为:收件人 outbound 满(慢客户端)→ 丢这次实时投递 + WARNING,**不丢数据**(消息/游标
    # 已落库,登录补收兜,0040)。不在此 drop 收件人连接——本协程是发起方的 Receiver,drop 对方(投 Disconnect)
    # 是 GameLoop / 其自身背压的职责,跨协程 drop 越界(契合「DB 权威 + 实时投递只是优化」,messaging.md)。
    try:
        recipient.outbound.put_nowait(msg)
    except asyncio.QueueFull:
        log.warning("dm realtime delivery dropped (recipient outbound full) to=%s", recipient.nick)
