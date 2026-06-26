"""P4(一):delayDB 写缓冲 `WriteBuffer` 双缓冲穷举(db.md「两类写」+「先 swap 后 await」+「回灌更新者优先」)。

纯同步数据结构,脱 DB/async 单测:状态写按键覆盖、事件写追加、swap 双缓冲取走清空、requeue 更新者优先。
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.events import PersistPayload
from app.core.records import HandRecordWrite, PointsWrite
from app.db.dm_records import DMWrite
from app.shell.persist import WriteBuffer

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _points(uid: int, points: int) -> PointsWrite:
    return PointsWrite(uid=uid, points=points)


def _record(key: str) -> HandRecordWrite:
    return HandRecordWrite(dedupe_key=key, start_time=T0, final_pot=0, participants=())


def _dm(key: str) -> DMWrite:
    return DMWrite(dedupe_key=key, from_uid=1, to_uid=2, text="hi", created_at=T0)


@dataclass(frozen=True)
class _UnknownWrite(PersistPayload):
    # 未在 _state_key 归类的载荷(模拟未来新实体未登记);应保守归事件写
    tag: str


# ── 状态写:同键覆盖(只留最新)──
def test_state_write_covers_same_key():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    buf.put(_points(1, 250))  # 同 uid → 覆盖
    assert len(buf) == 1
    dirty, appends = buf.swap()
    assert appends == []
    assert list(dirty.values()) == [_points(1, 250)]  # 只留最新值


def test_state_writes_distinct_keys_coexist():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    buf.put(_points(2, 50))
    assert len(buf) == 2  # 不同 uid 各占一项
    dirty, _ = buf.swap()
    assert {k: v.points for k, v in dirty.items()} == {("user", "1"): 100, ("user", "2"): 50}


# ── 事件写:逐条追加(不覆盖)──
def test_append_writes_accumulate():
    buf = WriteBuffer()
    buf.put(_record("dev:1"))
    buf.put(_record("dev:2"))
    buf.put(_record("dev:2"))  # 相同 dedupe_key 仍逐条进缓冲(DB 侧 ON CONFLICT 幂等,不在内存去重)
    assert len(buf) == 3
    _, appends = buf.swap()
    assert [r.dedupe_key for r in appends] == ["dev:1", "dev:2", "dev:2"]


def test_mixed_state_and_append():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    buf.put(_record("dev:1"))
    assert len(buf) == 2
    dirty, appends = buf.swap()
    assert len(dirty) == 1 and len(appends) == 1


# ── swap:双缓冲取走清空 ──
def test_swap_empties_buffer():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    buf.put(_record("dev:1"))
    assert not buf.is_empty()
    buf.swap()
    assert buf.is_empty() and len(buf) == 0


def test_put_after_swap_isolated_from_taken_batch():
    # 双缓冲:swap 后新 put 进**新空缓冲**,不污染已取走的批次(PersistWriter await 落库期间 GameLoop 新写不丢不混)
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    dirty, appends = buf.swap()
    buf.put(_points(1, 999))  # swap 之后写
    assert list(dirty.values()) == [_points(1, 100)]  # 已取走批次不受影响
    assert len(buf) == 1  # 新值在新缓冲


# ── requeue:落库失败回灌「更新者优先」──
def test_requeue_state_updater_wins():
    # 回灌的是上一批旧值;若期间 GameLoop 写了更新值,必须保留更新的(setdefault,绝不旧盖新)
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    dirty, appends = buf.swap()  # 取走 {uid1:100}
    buf.put(_points(1, 250))  # 期间内存权威已更新到 250
    buf.requeue(dirty, appends)  # 回灌旧的 100
    assert len(buf) == 1
    again, _ = buf.swap()
    assert again[("user", "1")].points == 250  # 更新者优先:保留 250,不被旧 100 盖


def test_requeue_state_fills_absent_key():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    dirty, appends = buf.swap()
    buf.requeue(dirty, appends)  # 期间无新写 → 旧值回灌生效
    again, _ = buf.swap()
    assert again[("user", "1")].points == 100


def test_requeue_appends_reinserted_before_new():
    # 事件写回灌:放回缓冲重 INSERT(前插,保持「先发生的先落」)
    buf = WriteBuffer()
    buf.put(_record("dev:1"))
    dirty, appends = buf.swap()
    buf.put(_record("dev:2"))  # 期间新事件
    buf.requeue(dirty, appends)
    _, all_appends = buf.swap()
    assert [r.dedupe_key for r in all_appends] == ["dev:1", "dev:2"]  # 回灌的在前


# ── 分类:未知 payload 保守归事件写(覆盖会静默丢数据)──
def test_unknown_payload_defaults_to_append():
    buf = WriteBuffer()
    buf.put(_UnknownWrite(tag="x"))
    dirty, appends = buf.swap()
    assert dirty == {} and len(appends) == 1  # 未归类 → 追加,不覆盖


# ── 分类:私信 DMWrite 是事件写(逐条追加,不覆盖;dedupe_key=msg_id 幂等,见 changes/0038)──
def test_dm_write_classified_as_event_write():
    buf = WriteBuffer()
    buf.put(_dm("m1"))
    buf.put(_dm("m2"))  # 不同 msg_id 各占一条(绝不像状态写那样按键覆盖)
    dirty, appends = buf.swap()
    assert dirty == {}  # 无状态写
    assert [p.dedupe_key for p in appends] == ["m1", "m2"]  # 逐条追加


# ── is_empty / 向后兼容 snapshot ──
def test_is_empty_initial():
    assert WriteBuffer().is_empty()


def test_snapshot_returns_all_payloads():
    buf = WriteBuffer()
    buf.put(_points(1, 100))
    buf.put(_record("dev:1"))
    snap = buf.snapshot()
    assert len(snap) == 2
    assert any(isinstance(p, PointsWrite) for p in snap)
    assert any(isinstance(p, HandRecordWrite) for p in snap)
    assert len(buf) == 2  # snapshot 只读,不清空
