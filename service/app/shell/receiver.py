# Receiver:一条连接的一生(见 connection.md「连接生命周期」):
# 登记(可能顶替)→ 起 Sender → 投 Connect → 收帧循环(收→[解密]→parse→Command 盖 origin→inbox + heartbeat)→ 退出清理。
# 帧编解按 conn.channel 分流(dispatch/GameLoop/reduce 全程不知有加密,守分层):
#   channel None(dev ?nick=)→ 明文文本帧 receive_text → parse;
#   channel 非 None(加密 ?sid=)→ 二进制帧 receive_bytes → channel.open(验 MAC→解密→验 seq)→ 明文 → parse(见 changes/0061)。
# FrameError(伪造/重放/损坏)= 安全信号 → 关连接(区别于「解密成功但 JSON/type 非法」的 INVALID_MESSAGE 续跑)。

import asyncio
import logging
import time
from datetime import datetime, timezone

import pydantic
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import gameconfig
from app.auth.channel import FrameError
from app.core.commands import (
    Command,
    Connect,
    Disconnect,
    JoinRoom,
    RoomChat,
    RoomCreate,
    SetBuyIn,
    SetSmallBlind,
)
from app.core.errors import Err, ErrorCode
from app.db.queries import load_user_by_nick
from app.shell.connection import Connection, ConnectionManager
from app.shell.history import RoomChatBuffer
from app.shell.messaging import deliver_dm_catch_up, route_direct_message, route_dm_mark_read
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
        timer.cancel_cleanup(conn.nick)  # 重连/顶替落在占座窗口内:拆断线倒计时(0070;竞态由 reduce OFFLINE 兜)
        # 接入(reduce:dev 预置用户 no-op;重连恢复待 P1)。用 await put(背压安全),且在 try 内
        # ——即便 inbox 满/异常,finally 也会 cancel Sender + unregister,不留半初始化的泄漏连接。
        await inbox.put(Connect(origin=None, nick=conn.nick))
        # 登录补收:读 DB 补发离线期间的未读私信 + 已读回执(shell 路由,不进 GameLoop;best-effort,见 changes/0040)。
        await deliver_dm_catch_up(conn, sessionmaker=sessionmaker)
        while True:
            payload = await _recv_frame(conn)  # 让出点:收帧 + 按 channel 解密/取明文
            if payload is None:
                break  # FrameError/会话过期 = 安全信号:关连接(finally 清理),不进业务
            cmd = await _frame_to_command(conn, payload, conns, persist, sessionmaker, history)
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
            timer.arm_cleanup(conn.nick)  # 断线装表:占座窗口自此起算(0070;凡投 Disconnect 处必 arm)
            try:
                inbox.put_nowait(Disconnect(origin=None, nick=conn.nick))  # 仅当前连接断开才投
            except asyncio.QueueFull:  # inbox 满(已是 CRITICAL 态);清理不抛
                log.critical("inbox full; could not post Disconnect for nick=%s", conn.nick)


async def _recv_frame(conn: Connection) -> str | bytes | None:
    # 收一帧并归一成「明文载荷(str/bytes)」交 parse:
    #   channel None(dev)→ 明文文本帧,原样返回;
    #   channel 非 None(加密)→ 会话未过期(exp 兜底强制到活连接,0070)→ 二进制帧 → channel.open
    #   (结构→验 MAC→解密→验 seq,见 auth/channel.py)→ 明文 bytes。
    # 返回 None = FrameError(伪造/重放/损坏)或会话过期:log.warning(只 reason,不含明文/密钥,脱敏红线)
    # + 关 ws + 让上层 break 关连接。
    if conn.channel is None:
        return await conn.ws.receive_text()  # 让出点
    frame = await conn.ws.receive_bytes()  # 让出点
    if conn.session is not None and time.time() >= conn.session.expires_at:
        # 会话 exp 兜底(auth.md):过期密钥的报文一律拒服务——正常客户端已提前无感轮换,撞到 = 未按时换钥。
        log.warning("frame rejected nick=%s reason=session_expired", conn.nick)
        try:
            await conn.ws.close(code=4401)  # 同握手拒码:须重新登录换会话
        except Exception:
            pass
        return None
    try:
        return conn.channel.open(frame)
    except FrameError as e:
        log.warning("frame rejected nick=%s reason=%s", conn.nick, e.reason)
        try:
            await conn.ws.close(code=4400)  # 拒帧即关连接(安全信号);close 幂等失败无害
        except Exception:
            pass
        return None


async def _frame_to_command(
    conn: Connection,
    raw: str | bytes,
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
    if isinstance(msg, (wire_client.SetSmallBlind, wire_client.SetBuyIn)):
        return _guard_room_config(conn, msg)  # 房配进 reduce 前按 gameconfig 上下限防护(见 changes/0043)
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


def _guard_room_config(
    conn: Connection, msg: "wire_client.SetSmallBlind | wire_client.SetBuyIn"
) -> Command | None:
    # 房间参数配置进 reduce 前防护:按 gameconfig 上下限拒越界(core 不 import config,故 bounds 归 shell,
    # 同房聊文本防护 _guard_room_chat;见 changes/0043 / 0015)。越界回对应 Err + return None(不进 inbox);
    # 合法构 Command(身份盖连接 nick)。授权(任何在房成员,无房主)/ 时机(非局中)由 reduce 兜——shell 不读 world、无法判。
    if isinstance(msg, wire_client.SetSmallBlind):
        if not (gameconfig.MIN_SMALL_BLIND <= msg.amount <= gameconfig.MAX_SMALL_BLIND):
            conn.outbound.put_nowait(
                ErrorMessage.from_err(
                    Err(ErrorCode.INVALID_SMALL_BLIND, f"小盲额须在 [{gameconfig.MIN_SMALL_BLIND}, {gameconfig.MAX_SMALL_BLIND}]")
                )
            )
            return None
        return SetSmallBlind(origin=conn.nick, amount=msg.amount)
    if not (gameconfig.MIN_BUY_IN <= msg.amount <= gameconfig.MAX_BUY_IN):
        conn.outbound.put_nowait(
            ErrorMessage.from_err(
                Err(ErrorCode.INVALID_BUY_IN, f"买入额须在 [{gameconfig.MIN_BUY_IN}, {gameconfig.MAX_BUY_IN}]")
            )
        )
        return None
    return SetBuyIn(origin=conn.nick, amount=msg.amount)


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
    # 房不存在则动态建房(谁都可创建,见 core.md 房间生命周期):盖 gameconfig 建房默认配置;加入已存在房时 reduce 忽略它。
    create = RoomCreate(
        small_blind=gameconfig.DEV_SMALL_BLIND,
        buy_in=gameconfig.DEV_BUY_IN,
        seats=gameconfig.DEV_SEATS,
    )
    return JoinRoom(origin=conn.nick, room=msg.room, uid=uid, loaded=loaded, create=create)


async def _displace(old: Connection) -> None:
    if old.sender_task is not None:
        old.sender_task.cancel()
    try:
        await old.ws.close()  # 关旧 ws → 旧 Receiver 的 receive_text 报错退出(其 is_current=False,不投 Disconnect)
    except Exception:
        pass
