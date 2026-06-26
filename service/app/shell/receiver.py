# Receiver:一条 dev 连接的一生(见 connection.md「连接生命周期」,明文版):
# 登记(可能顶替)→ 起 Sender → 投 Connect → 收帧循环(parse→Command 盖 origin→inbox + heartbeat)→ 退出清理。
# dev-only:?nick= 明文握手,无 MAC / 无加密;P5 国密信道落地即替换握手与帧编解(dispatch/GameLoop/reduce 不变)。

import asyncio
import logging
import time
from datetime import datetime, timezone

import pydantic
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.core.commands import Command, Connect, Disconnect, JoinRoom, RoomChat
from app.core.errors import Err, ErrorCode
from app.db.queries import load_user_by_nick
from app.shell.connection import Connection, ConnectionManager
from app.shell.history import RoomChatBuffer
from app.shell.messaging import route_direct_message, route_dm_mark_read
from app.shell.persist import WriteBuffer
from app.shell.sender import sender_loop
from app.shell.timer import Timer
from app.wire import client as wire_client
from app.wire.server import ErrorMessage, RoomChatHistory

log = logging.getLogger(__name__)


async def run_receiver(
    conn: Connection,
    conns: ConnectionManager,
    inbox: "asyncio.Queue[Command]",
    timer: Timer,
    sessionmaker: async_sessionmaker[AsyncSession],
    history: RoomChatBuffer,
    persist: WriteBuffer,
) -> None:
    old = conns.register(conn)  # 登记;返回被顶掉的旧连接
    if old is not None:
        await _displace(old)  # 顶替:cancel 旧 Sender + 关旧 ws,不投 Disconnect(connection.md 顶替语义)
    conn.sender_task = asyncio.create_task(sender_loop(conn))
    try:
        timer.heartbeat(conn.nick)
        # 接入(reduce:dev 预置用户 no-op;重连恢复待 P1)。用 await put(背压安全),且在 try 内
        # ——即便 inbox 满/异常,finally 也会 cancel Sender + unregister,不留半初始化的泄漏连接。
        await inbox.put(Connect(origin=None, nick=conn.nick))
        while True:
            raw = await conn.ws.receive_text()  # 让出点
            timer.heartbeat(conn.nick)  # 每帧续命(保活按 nick)
            cmd = await _frame_to_command(conn, raw, conns, persist, sessionmaker, history)
            if cmd is not None:
                await inbox.put(cmd)  # 背压:inbox 满则在此等(只压住这条 Receiver,不拖 GameLoop)
    except Exception:
        log.info("receiver exit nick=%s", conn.nick)  # ws 断/异常 → 进 finally 清理
    finally:
        was_current = conns.is_current(conn)  # 必须在 unregister 前判:被顶替的旧连接为 False
        conns.unregister(conn)  # 只删自己
        if conn.sender_task is not None:
            conn.sender_task.cancel()
        if was_current:
            try:
                inbox.put_nowait(Disconnect(origin=None, nick=conn.nick))  # 仅当前连接断开才标 OFFLINE
            except asyncio.QueueFull:  # inbox 满(已是 CRITICAL 态);清理不抛
                log.critical("inbox full; could not post Disconnect for nick=%s", conn.nick)


async def _frame_to_command(
    conn: Connection,
    raw: str,
    conns: ConnectionManager,
    persist: WriteBuffer,
    sessionmaker: async_sessionmaker[AsyncSession],
    history: RoomChatBuffer,
) -> Command | None:
    # 明文帧 → Command:解析失败(非法 JSON / 未知 type / 字段不合法)直接回发 ErrorMessage(error.md),不进 reduce。
    try:
        msg = wire_client.parse(raw)
    except pydantic.ValidationError as e:
        detail = str(e)[: gameconfig.ERROR_DETAIL_MAX_LEN]
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.INVALID_MESSAGE, detail)))
        return None
    if isinstance(msg, wire_client.JoinRoom):
        return await _build_join(conn, msg, sessionmaker)  # JoinRoom 需读 DB 富化 uid/loaded(见 changes/0030)
    if isinstance(msg, wire_client.RoomChat):
        return _guard_room_chat(conn, msg)  # 房聊进 reduce 前过文本防护 + 限速(见 changes/0033 / messaging.md)
    if isinstance(msg, wire_client.FetchRoomChat):
        _serve_room_chat_history(conn, msg, history)  # 房聊历史 shell 直服务(读环形缓冲回 outbound,见 changes/0036)
        return None
    if isinstance(msg, wire_client.DirectMessage):
        # 私信 shell 路由:防护 → 解析 uid → 落库 DMWrite → 在线投 DMDelivered,不进 GameLoop(见 changes/0038)。
        await route_direct_message(conn, msg, conns=conns, persist=persist, sessionmaker=sessionmaker)
        return None
    if isinstance(msg, wire_client.DMMarkRead):
        # 标记已读 shell 路由:解析 uid → put 已读游标 → 在线回执 DMRead,不进 GameLoop(见 changes/0039)。
        await route_dm_mark_read(conn, msg, conns=conns, persist=persist, sessionmaker=sessionmaker)
        return None
    # 身份盖 origin=会话 nick(不信报文);墙钟 now 由 shell 盖(core 不读钟,仅 StartHand 用,见 wire/client）。
    return wire_client.to_command(msg, origin=conn.nick, now=datetime.now(timezone.utc))


def _guard_room_chat(conn: Connection, msg: wire_client.RoomChat) -> Command | None:
    # 房聊进 reduce 前防护(messaging.md 契约 4):空/超长内容**先**拒(根本不到 GameLoop,故不耗令牌),
    # 内容合法**再**过令牌桶限速。失败 → 回 Err 投本连接 outbound + return None(不进 inbox);过则构 RoomChat(身份盖连接 nick)。
    if not msg.text.strip():  # 非空(strip 后判据):空 / 纯空白拒
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.INVALID_MESSAGE, "房聊文本不能为空")))
        return None
    if len(msg.text) > gameconfig.ROOM_CHAT_MAX_TEXT_LEN:  # 超长(按原文长度 = 即将广播的串)
        conn.outbound.put_nowait(
            ErrorMessage.from_err(
                Err(ErrorCode.MESSAGE_TOO_LONG, f"房聊文本超 {gameconfig.ROOM_CHAT_MAX_TEXT_LEN} 字符上限")
            )
        )
        return None
    if conn.chat_bucket is None or not conn.chat_bucket.try_consume(time.monotonic()):  # 内容合法才耗令牌
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.RATE_LIMITED, "房聊发送过频,请稍候")))
        return None
    return RoomChat(origin=conn.nick, text=msg.text)  # 广播原文,不改用户内容


def _serve_room_chat_history(conn: Connection, msg: wire_client.FetchRoomChat, history: RoomChatBuffer) -> None:
    # 房聊历史拉取(messaging.md §持久化 / changes/0036):shell 直服务、不进 GameLoop——读环形缓冲回该连接 outbound。
    # 房名取自报文(shell 不读 world、无法解析当前房;同 JoinRoom 带 room)。历史是公开房聊、非敏感,v1 不校验成员资格。
    conn.outbound.put_nowait(RoomChatHistory(room=msg.room, messages=history.recent(msg.room)))


async def _build_join(
    conn: Connection, msg: wire_client.JoinRoom, sessionmaker: async_sessionmaker[AsyncSession]
) -> Command | None:
    # 进房:按连接 nick 读 DB 取 uid/loaded(身份/积分不信报文,storage.md 载入一次)→ JoinRoom 命令。
    try:
        row = await load_user_by_nick(sessionmaker, conn.nick)
    except Exception:
        # DB 读失败(连接断/超时等):回发错误 + 保活连接(同解析错误,不让异常冒到外层 drop 连接)。
        log.exception("join_room DB read failed nick=%s", conn.nick)
        conn.outbound.put_nowait(ErrorMessage.from_err(Err(ErrorCode.INTERNAL, "进房读 DB 失败")))
        return None
    if row is None:
        # 鉴权说有此用户、DB 说无 = 内部不一致(dev 握手已拒非种子 nick;生产 session 由注册签发,必有行)。
        conn.outbound.put_nowait(
            ErrorMessage.from_err(Err(ErrorCode.INTERNAL, f"用户 {conn.nick} 无 DB 账号行"))
        )
        return None
    uid, loaded = row
    return JoinRoom(origin=conn.nick, room=msg.room, uid=uid, loaded=loaded)


async def _displace(old: Connection) -> None:
    if old.sender_task is not None:
        old.sender_task.cancel()
    try:
        await old.ws.close()  # 关旧 ws → 旧 Receiver 的 receive_text 报错退出(其 is_current=False,不投 Disconnect)
    except Exception:
        pass
