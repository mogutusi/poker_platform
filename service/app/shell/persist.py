# delayDB 写缓冲 + 写回协程(db.md):
# - WriteBuffer:双缓冲,状态写按键覆盖(同键只留最新)、事件写逐条追加;两个生产者(GameLoop.dispatch +
#   未来私信路由)同步 put(无 await,守不变量 3)。
# - PersistWriter:唯一消费者,周期「先 swap 同步取走清空,再 await 落库」;失败回灌(更新者优先)、毒丸、优雅 drain。
#   落库后端抽象在 Persister 协议之后(真实现 to_orm+session 留 P4 三;dev 用 NullPersister 丢弃)。

import asyncio
import logging
import time
from typing import Protocol

from app import gameconfig
from app.core.events import PersistPayload
from app.core.records import HandRecordWrite, PointsWrite

log = logging.getLogger(__name__)

StateKey = tuple[str, ...]  # 状态写覆盖键 = (table, pk...),如 ("user", uid);同键后写盖前写


def _state_key(payload: PersistPayload) -> StateKey | None:
    # 按 db.md「两类写」归类:状态写返回 StateKey(覆盖键)、事件写返回 None(追加)。
    # 新增持久化实体在此登记;拿不准默认事件写(覆盖一个本该追加的实体会静默丢数据,代价更高)。
    match payload:
        case PointsWrite():
            # 全局积分按不可变 uid 覆盖;key 全用 str(匹配 StateKey 类型),真主键在 to_orm 时取 payload.uid 原值。
            return ("user", str(payload.uid))
        case HandRecordWrite():
            return None  # 手牌记录是事件写,逐条追加(dedupe_key 幂等,内存不去重)
        case _:
            log.warning("未知 Persist 载荷 %s,默认归事件写(追加)", type(payload).__name__)
            return None


class WriteBuffer:
    def __init__(self) -> None:
        self._dirty: dict[StateKey, PersistPayload] = {}  # 状态写:同键覆盖,只落最新
        self._appends: list[PersistPayload] = []  # 事件写:逐条追加(DB 侧 dedupe_key 幂等)

    # —— 生产者侧:同步无 await(与 put_nowait 同级,守不变量 3)——
    def put(self, payload: PersistPayload) -> None:
        # 分流:状态写覆盖进 dict(N 次同键变更合成 1 次落库)、事件写追加进 list。
        key = _state_key(payload)
        if key is None:
            self._appends.append(payload)
        else:
            self._dirty[key] = payload

    # —— 消费者侧(PersistWriter):双缓冲,先 swap 同步取走并清空,之后才 await 落库 ——
    def swap(self) -> tuple[dict[StateKey, PersistPayload], list[PersistPayload]]:
        # 取走当前批次并置空:返回的批次成为 PersistWriter 私有局部,await 落库期间 GameLoop 新写进新空缓冲,
        # 既不丢也不混(绝不持缓冲本体跨 await,见 db.md「先 swap 后 await」)。
        dirty, appends = self._dirty, self._appends
        self._dirty, self._appends = {}, []
        return dirty, appends

    def requeue(self, dirty: dict[StateKey, PersistPayload], appends: list[PersistPayload]) -> None:
        # 落库失败整批回灌下周期重试:
        # - 状态写 setdefault(更新者优先):回灌的是旧值,若期间已有更新写则保留新的,绝不旧盖新(db.md 正确性要点)。
        # - 事件写前插:放回缓冲头部重 INSERT,保持「先发生的先落」;失败整批回滚故无重复(dedupe_key 兜底)。
        for key, payload in dirty.items():
            self._dirty.setdefault(key, payload)
        self._appends[:0] = appends

    def is_empty(self) -> bool:
        return not self._dirty and not self._appends

    # —— 只读视图(测试/调试;PersistWriter 走 swap 而非此)——
    def snapshot(self) -> list[PersistPayload]:
        return list(self._dirty.values()) + list(self._appends)

    def __len__(self) -> int:
        return len(self._dirty) + len(self._appends)


class Persister(Protocol):
    # 落库后端缝:把一批写刷进 DB。失败抛异常(PersistWriter 据此整批回灌重试);成功返回 None。
    # 真实现(P4 三)= to_orm + session.merge(UPSERT 状态写)/add(INSERT 事件写)+ commit;一批一个短事务。
    async def flush(
        self, dirty: dict[StateKey, PersistPayload], appends: list[PersistPayload]
    ) -> None: ...


class NullPersister:
    # dev / 无 DB:不落库,只记日志(dev 端点本就无 DB,见 lifespan)。drain 仍能清空缓冲。
    async def flush(
        self, dirty: dict[StateKey, PersistPayload], appends: list[PersistPayload]
    ) -> None:
        if dirty or appends:
            log.debug("NullPersister 丢弃 %d 状态写 + %d 事件写(dev 无 DB)", len(dirty), len(appends))


class PersistWriter:
    # delayDB 写回协程(全进程唯一 DB 写者):周期 swap → 落库,失败回灌、毒丸、优雅 drain(见 db.md)。
    def __init__(
        self,
        buf: WriteBuffer,
        persister: Persister,
        *,
        flush_interval_s: float | None = None,
        max_retry: int | None = None,
        drain_timeout_s: float | None = None,
    ) -> None:
        self._buf = buf
        self._persister = persister
        # 可调参数缺省取 gameconfig;测试传小值直驱(同 timer 的 timeout_s 覆盖法,免 monkeypatch 模块常量)。
        self._interval = gameconfig.DB_FLUSH_INTERVAL_MS / 1000 if flush_interval_s is None else flush_interval_s
        self._max_retry = gameconfig.DB_WRITE_MAX_RETRY if max_retry is None else max_retry
        self._drain_timeout = (
            gameconfig.DB_DRAIN_TIMEOUT_MS / 1000 if drain_timeout_s is None else drain_timeout_s
        )
        self._fail_streak = 0  # 同批连续失败计数;达 max_retry 触发毒丸

    async def run(self) -> None:
        # 主循环:唯一让出点 = sleep + flush 内 await。绝不在此直改 world / 旁路 ws。
        while True:
            await asyncio.sleep(self._interval)
            await self.flush_once()

    async def flush_once(self) -> bool:
        # 取走一批落库(先 swap 同步取走清空、再 await,绝不持缓冲跨 await,守 db.md 双缓冲)。
        # 成功清失败计数;失败整批回灌(更新者优先)+ 失败计数 +1,达 max_retry 丢批(毒丸)。返回是否处理了非空批。
        if self._buf.is_empty():
            return False
        dirty, appends = self._buf.swap()
        try:
            await self._persister.flush(dirty, appends)
        except asyncio.CancelledError:
            # 关闭取消落在 flush 半途(批已 swap 出、未落库):先回灌再 re-raise,使后续 drain 能补落,
            # 否则这批「已对玩家生效、未落库」的写静默丢失(db.md drain 红线)。drain 在写者 task 收割后单线
            # 跑 flush,无并发竞 swap;重落幂等(状态写 UPSERT 覆盖、事件写 dedupe_key ON CONFLICT)故安全。
            self._buf.requeue(dirty, appends)
            raise
        except Exception:
            self._fail_streak += 1
            if self._fail_streak >= self._max_retry:
                # 毒丸:同批连续失败超阈值 → 丢批 + CRITICAL,别卡死后续(留人工介入);清计数继续下批
                log.critical(
                    "delayDB 毒丸:%d 状态写 + %d 事件写连续失败 %d 次,丢弃",
                    len(dirty), len(appends), self._fail_streak,
                )
                self._fail_streak = 0
            else:
                self._buf.requeue(dirty, appends)  # 整批回灌,下周期重试
                log.error("delayDB 落库失败已回灌(streak=%d)", self._fail_streak, exc_info=True)
            return True
        self._fail_streak = 0
        log.debug("delayDB flushed %d 状态写 + %d 事件写", len(dirty), len(appends))
        return True

    async def drain(self) -> None:
        # 优雅关闭终结 flush:循环 flush 直到缓冲空或超 DB_DRAIN_TIMEOUT_MS;超时 CRITICAL + 放弃(进程要退)。
        # swap 一次取走整批 ⇒ 成功的 flush_once 一轮即清空;只有失败回灌才使缓冲仍非空、需重试。
        deadline = time.monotonic() + self._drain_timeout
        while not self._buf.is_empty():
            if time.monotonic() >= deadline:
                log.critical("delayDB drain 超时,%d 笔未落写丢弃(进程退出)", len(self._buf))
                return
            await self.flush_once()
            if not self._buf.is_empty():
                # 仍非空 = 刚才落库失败回灌 → 按落库周期节流重试,防紧自旋 + 日志刷屏(deadline 兜底退出)
                await asyncio.sleep(self._interval)
