# dispatch:事件 → 物理落点(见 connection.md「dispatch」)。GameLoop commit 后**同步**派发:
# 只 put_nowait(Sender 队列)/ 调本地快设施(Timer),不 await(守不变量 3)。错误回发同此处。

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from app.core.commands import Command, Disconnect
from app.core.domain import World
from app.core.errors import Err
from app.core.events import Broadcast, ClearAction, Event, Personal, Persist, TurnChanged
from app.core.records import HandRecordWrite
from app.shell.connection import Connection, ConnectionManager
from app.shell.persist import WriteBuffer
from app.shell.timer import Timer
from app.wire.server import ErrorMessage, ServerMessage

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Dispatcher:
    def __init__(
        self,
        world: World,
        conns: ConnectionManager,
        persist: WriteBuffer,
        timer: Timer,
        inbox: "asyncio.Queue[Command]",
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.world = world
        self.conns = conns
        self.persist = persist
        self.timer = timer
        self.inbox = inbox
        self._now = now or _utcnow  # 盖 end_time 用的墙钟;可注入供测试定值(core 不读钟,墙钟只在 shell)

    def dispatch(self, ev: Event) -> None:
        match ev:
            case Broadcast(room=r, msg=m):
                room = self.world.rooms.get(r)  # reduce 可能刚销毁该房(最后一人离开)→ 跳过
                if room is None:
                    return
                for nick in room.users_in_room:  # 逻辑成员 → 按 nick 取连接(OFFLINE/无连接者跳过)
                    conn = self.conns.get(nick)
                    if conn is not None:
                        self._enqueue(conn, m)
                # 房聊历史不再在此追加:0071 起挂 Room.chat_history,由 reduce._room_chat 写(随房生灭)
            case Personal(nick=n, msg=m):  # 底牌 / StateSnapshot / 离开者回执,按 nick 私发
                conn = self.conns.get(n)
                if conn is not None:
                    self._enqueue(conn, m)
            case Persist(payload=p):
                # 手牌记录的 end_time 由 shell 在派发本 Persist 时盖墙钟(≈手结束时刻,非 flush 时刻;core 不读钟,见 db.md)。
                if isinstance(p, HandRecordWrite) and p.end_time is None:
                    p = replace(p, end_time=self._now())
                self.persist.put(p)
            case TurnChanged(room=r, acting_nick=n, epoch=e):  # B 组:同步调 Timer(倒计时长 Timer 自取配置)
                self.timer.on_turn_changed(r, n, e)
            case ClearAction(room=r):
                self.timer.clear_action(r)

    def send_error(self, cmd: Command, err: Err) -> None:
        # 业务失败:Err → ErrorMessage 回发**发起连接**(cmd.origin),不广播;系统命令(origin=None)只落日志。
        if cmd.origin is None:
            log.warning("system cmd %s failed: %s %s", type(cmd).__name__, err.code, err.detail)
            return
        conn = self.conns.get(cmd.origin)
        if conn is not None:
            self._enqueue(conn, ErrorMessage.from_err(err))

    def _enqueue(self, conn: Connection, msg: ServerMessage) -> None:
        try:
            conn.outbound.put_nowait(msg)
        except asyncio.QueueFull:  # ≤20 人正常不会满;满 = 该连接 Sender 卡死(慢客户端)
            log.warning("slow client dropped nick=%s", conn.nick)
            self._drop_connection(conn)

    def _drop_connection(self, conn: Connection) -> None:
        # 慢客户端:停路由到它(unregister)+ 终结它的两条协程 + 投 Disconnect 标 OFFLINE;重连靠 StateSnapshot 补回。
        self.conns.unregister(conn)  # 必须先摘键:Receiver 的 finally 据 is_current 决定投不投 Disconnect,这里已投过
        # 对齐 receiver.py 的退出清理路径(0083 / BUG-6):只 unregister 会留下一条**还活着的**连接——
        # ws 没关、Receiver 还阻塞在 receive,客户端继续发帧就继续往 inbox 投命令(幽灵命令源),
        # 且它重连时同一 nick 会同时挂两个 Receiver。
        # 为什么 cancel Receiver 而不是只关 ws:触发条件正是「读慢写健」的非对称慢客户端——它的下行已经堵住
        # (所以 outbound 才会满),关闭帧同样发不出去,而上行畅通,Receiver 照旧产命令;关 ws 堵不住这个源。
        # cancel 是同步的(只置标志,下个 await 才生效),不违反「dispatch 全程不 await」(不变量 3)。
        if conn.sender_task is not None:
            conn.sender_task.cancel()
        if conn.receiver_task is not None:
            # 其 finally 里 `was_current` 已为 False ⇒ 不会重复投 Disconnect。这一点**靠 receiver.py 那个 finally
            # 全程无 await 撑着**(与本函数一样对事件循环原子):谁先跑,另一边看到的都是「已经不是当前连接」。
            # 往那个 finally 里加任何 await(比如"礼貌地"关一下 ws)就会重新打开窗口、变成双份 Disconnect。
            conn.receiver_task.cancel()
        self.timer.arm_cleanup(conn.nick)  # 断线装表(0070:凡投 Disconnect 处必 arm,占座窗口自此起算)
        try:
            self.inbox.put_nowait(Disconnect(origin=None, nick=conn.nick))
        except asyncio.QueueFull:
            # 本调用在 GameLoop 内同步执行(dispatch ← commit 后 for ev)。inbox 满 = GameLoop 自身卡死
            # (architecture.md「inbox 满」CRITICAL,进程级 bug),绝不让 QueueFull 冒出去崩掉唯一状态写者:
            # 丢该 Disconnect + 落 CRITICAL。残留:丢了 Disconnect → 该 nick 仍记为在线(非 OFFLINE),而
            # `_cleanup` 只回收 OFFLINE 座位(reduce.py)→ **不会自动退座**;座位占用至该 nick 重连(走顶替再连
            # 补回)或进程重启。shell 不写 world(不变量 2),无法在此标 OFFLINE,故不兜——这是 inbox 满(已
            # CRITICAL)窗口内可接受的已知占座泄漏。
            log.critical("inbox full; could not post Disconnect for dropped slow client nick=%s", conn.nick)
