# delayDB 写缓冲(db.md):双缓冲——状态写按键覆盖(同键只留最新)、事件写逐条追加。
# 两个生产者(GameLoop.dispatch + 未来私信路由)同步 put(无 await,守不变量 3);
# 唯一消费者 PersistWriter(P4 二)「先 swap 同步取走清空,再 await 落库」。本篇只落缓冲本体,不接 DB/async。

import logging

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
