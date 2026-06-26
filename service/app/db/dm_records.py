# 私信(DM)的 delayDB 写载荷(见 messaging.md §私信 / db.md「Persist 接口」)。
#
# 与 core/records.py 的载荷分家:那些是 **reduce 产**(摊牌/结束),core 构造;DM 载荷是 **shell 私信路由产**
# (写缓冲的第二个生产者),core 永不碰——故置 db 层、不入 core。共享基类 PersistPayload(core/events.py)
# 使其经同一 WriteBuffer.put 分流(_state_key 归类)、同一 PersistWriter/OrmPersister 落库。
# 隐私(log.md 红线):正文落库,但不写日志、不得带 hole_cards/deck。frozen dataclass(同 records.py 实现)。

from dataclasses import dataclass
from datetime import datetime

from app.core.events import PersistPayload


@dataclass(frozen=True)
class DMWrite(PersistPayload):
    # 一条私信(事件写,追加;发即落库 = 未读)。键 dedupe_key=msg_id 幂等 INSERT,绝不覆盖(见 db.md 两类写)。
    dedupe_key: str  # = msg_id(shell uuid4 生成);delayDB 幂等键 + wire 引用
    from_uid: int  # 发件人不可变 User.id(绝不用可变 nickname;收发边界做 nick↔uid 转换)
    to_uid: int  # 收件人不可变 User.id
    text: str  # 私信正文;不含 hole_cards/deck(log.md 红线),不写日志
    created_at: datetime  # shell 盖墙钟;展示时间 + 「未读/已读」比较与保留清理排序键(0039)
