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
from app.db.dm_records import DMWrite
from app.db.queries import load_uids_by_nicks
from app.shell.connection import Connection, ConnectionManager
from app.shell.persist import WriteBuffer
from app.wire import client as wire_client
from app.wire.server import DMDelivered, DMUndelivered, ErrorMessage

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
    try:
        uids = await load_uids_by_nicks(sessionmaker, (conn.nick, msg.to_nick))
    except Exception:
        log.exception("direct_message DB read failed from=%s to=%s", conn.nick, msg.to_nick)
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.INTERNAL, "私信读 DB 失败")))
        return
    to_uid = uids.get(msg.to_nick)
    if to_uid is None:  # 对端根本不存在 = 硬错误回执(离线不算,见 messaging.md;离线只落库、登录补收 0039)
        conn.outbound.put_nowait(DMUndelivered(to_nick=msg.to_nick))
        return
    from_uid = uids.get(conn.nick)
    if from_uid is None:  # 鉴权说有发件人、DB 无行 = 内部不一致(同 _build_join;dev 握手已拒非种子 nick)
        conn.outbound.put_nowait(
            ErrorMessage.from_err(Err(ErrorCode.INTERNAL, f"用户 {conn.nick} 无 DB 账号行"))
        )
        return

    msg_id = uuid.uuid4().hex  # 幂等键(比 from_uid:微秒 稳:免同微秒撞键);shell 生成,DB 权威
    created_at = datetime.now(timezone.utc)  # shell 盖墙钟(core 不读钟;DM 全在 shell,可读)
    persist.put(DMWrite(dedupe_key=msg_id, from_uid=from_uid, to_uid=to_uid, text=text, created_at=created_at))  # 必落=未读

    recipient = conns.get(msg.to_nick)  # 在线判断 = ConnectionManager nick 表(presence;离线 None)
    if recipient is not None:  # 在线 → 实时投递(尽力而为;离线仅落库,0039 登录补收)
        _try_deliver(
            recipient, DMDelivered(msg_id=msg_id, from_nick=conn.nick, text=text, created_at=created_at)
        )


def _try_deliver(recipient: Connection, dm: DMDelivered) -> None:
    # 实时投递尽力而为:收件人 outbound 满(慢客户端)→ 丢这次实时投递 + WARNING,**不丢消息**(已落库,
    # 登录补收兜,0039)。不在此 drop 收件人连接——本协程是发件人的 Receiver,drop 收件人(投 Disconnect)
    # 是 GameLoop / 其自身背压的职责,跨协程 drop 越界(契合「DB 权威 + 实时投递只是优化」,messaging.md)。
    try:
        recipient.outbound.put_nowait(dm)
    except asyncio.QueueFull:
        log.warning("dm realtime delivery dropped (recipient outbound full) to=%s", recipient.nick)
