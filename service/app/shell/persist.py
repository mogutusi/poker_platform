# delayDB 写缓冲(0018 桩):同步入缓冲 + 日志,先不接 DB。
# P4 换双缓冲 swap + PersistWriter(先 swap 后 await)+ to_orm(见 db.md / storage.md)。

import logging

from app.core.events import PersistPayload

log = logging.getLogger(__name__)


class WriteBuffer:
    def __init__(self) -> None:
        self._items: list[PersistPayload] = []  # 桩:仅暂存;P4 换状态写覆盖 + 事件写追加双缓冲

    def put(self, payload: PersistPayload) -> None:
        # dispatch 同步入缓冲,无 await(GameLoop 处理期间不阻塞,守不变量 3)。
        self._items.append(payload)
        log.debug("persist buffered: %s", type(payload).__name__)

    def snapshot(self) -> list[PersistPayload]:
        return list(self._items)  # 供测试/调试只读;P4 由 PersistWriter swap 落库

    def __len__(self) -> int:
        return len(self._items)
