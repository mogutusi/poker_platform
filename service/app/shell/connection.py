# 连接登记/路由/顶替(见 connection.md)。连接绑 nick、不绑房间(模型 2);
# channel=None ⇒ 明文 dev 帧(?nick=);channel 非 None ⇒ 加密 ws 帧(?sid=,逐会话信道,见 changes/0061)。

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from app import gameconfig
from app.auth.channel import SecureChannel
from app.shell.ratelimit import TokenBucket

log = logging.getLogger(__name__)


@dataclass
class Connection:
    nick: str  # 会话身份;一个 nick 全局一条有效连接
    session_id: str  # 会话句柄(公开 selector / dev 用 nick),审计/日志关联
    ws: Any  # 物理 ws(FastAPI WebSocket 或测试 fake)
    outbound: "asyncio.Queue[Any]"  # 有界;装明文 ServerMessage,满 = 慢客户端(见 dispatch._enqueue)
    channel: SecureChannel | None = None  # 逐会话安全信道(引用会话的 SecureChannel);None=明文 dev 帧、非 None=加密帧(Sender seal / Receiver open,见 changes/0061)
    sender_task: asyncio.Task | None = None  # 本连接 Sender 协程句柄;起 Sender 前为 None,退出/顶替时 cancel
    chat_bucket: TokenBucket | None = None  # 房聊发件人维度令牌桶(每连接,见 messaging.md / ratelimit);create 时建满桶
    dm_bucket: TokenBucket | None = None  # 私信发件人维度令牌桶(每连接,见 messaging.md §私信);与房聊各一桶
    # 注:用户在哪个房间是 world.users[nick].room,不是连接字段(连接绑 nick、不绑房)。

    @classmethod
    def create(cls, nick: str, session_id: str, ws: Any, channel: SecureChannel | None = None) -> "Connection":
        now = time.monotonic()
        return cls(
            nick=nick,
            session_id=session_id,
            ws=ws,
            outbound=asyncio.Queue(maxsize=gameconfig.OUTBOUND_MAX),
            channel=channel,  # None = 明文 dev(?nick=);非 None = 加密会话信道(?sid=)
            chat_bucket=TokenBucket.create(
                gameconfig.ROOM_CHAT_RATE_BURST, gameconfig.ROOM_CHAT_RATE_PER_SEC, now
            ),
            dm_bucket=TokenBucket.create(gameconfig.DM_RATE_BURST, gameconfig.DM_RATE_PER_SEC, now),
        )


class ConnectionManager:
    def __init__(self) -> None:
        self._by_nick: dict[str, Connection] = {}  # nick → Connection(全局,房间无关)

    def register(self, conn: Connection) -> Connection | None:
        old = self._by_nick.get(conn.nick)  # 同 nick 旧连接 = 被顶替(调用方关旧 ws + cancel 旧 Sender)
        self._by_nick[conn.nick] = conn
        return old

    def unregister(self, conn: Connection) -> None:
        if self._by_nick.get(conn.nick) is conn:  # 仅删自己:顶替后旧连接退出不误删已上位的新连接
            del self._by_nick[conn.nick]

    def is_current(self, conn: Connection) -> bool:
        return self._by_nick.get(conn.nick) is conn  # 退出时判「我还是当前连接吗」(决定是否投 Disconnect)

    def get(self, nick: str) -> Connection | None:
        return self._by_nick.get(nick)  # Personal / 广播成员 / 错误回发,全按 nick O(1)

    def online_nicks(self) -> set[str]:
        return set(self._by_nick)  # 全体有 live 连接的 nick(新拷贝,调用方可改;presence「在线」源,见 presence.md)

    def rename(self, old: str, new: str) -> None:
        # 改昵称(仅大厅,见 presence.md):连接从 old 键重挂到 new 键 + 改 Connection.nick,
        # 否则私聊/路由按新 nick 找不到旧连接。无 old 连接(未连接时改名只改库)→ no-op。
        # 前提:调用方(P7 改昵称 REST)须先过 DB nickname 唯一约束 + 仅大厅判定,故 new 键不会撞活连接。
        conn = self._by_nick.pop(old, None)
        if conn is not None:
            conn.nick = new
            self._by_nick[new] = conn

    def rekey(self, conn: Connection, new: str) -> None:
        # 身份安全版重挂(0065 自 review 抓修):只动**这一个** Connection 对象——按其当前 nick 查表、
        # `is` 同一才摘旧键(防改名 await 窗内别的 rename/顶替已换了该键,误摘/误挂他人连接);
        # new 键若已被占(只可能是 DB 无行背书的孤儿键,CAS 后正主不可能占)则覆盖 + WARNING,
        # 被覆盖孤儿的 unregister 有 `is` 判定、退出无害。conn 不在表(已断)→ 只改 .nick,无键可挂。
        if self._by_nick.get(conn.nick) is conn:
            del self._by_nick[conn.nick]
            if new in self._by_nick:
                log.warning("rekey overwrote orphan connection key nick=%s", new)
            conn.nick = new
            self._by_nick[new] = conn
        else:
            conn.nick = new  # 已被顶替/已断:不动表,只同步对象自身的 nick
