"""/hands 的牌面明细与隐私边界 + /leaderboard/stats 统计(0110)。"""

from datetime import datetime, timedelta, timezone

from sqlalchemy.pool import StaticPool

from app.auth.session import SessionStore
from app.db.engine import create_all, make_engine, make_sessionmaker
from app.db.models import HandDetail, HandParticipant, HandParticipantDetail, HandRecord, User
from app.db.queries import StatsRow
from app.rest.hands import make_hands_router
from app.rest.leaderboard import compute_stats, make_leaderboard_router
from tests.rest._sealed import T0, TTL, call

_T = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _seeded():
    # hand1:alice 赢 bob,bob 弃牌,没摊牌。hand2:bob 与 carol 摊牌(alice 没参与)。
    engine = make_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    await create_all(engine)
    sm = make_sessionmaker(engine)
    async with sm() as s:
        async with s.begin():
            s.add_all([User(id=1, nickname="alice", points=100), User(id=2, nickname="bob", points=200),
                       User(id=3, nickname="carol", points=300)])
            s.add_all([
                HandRecord(id=1, dedupe_key="r1:1", room="r1", start_time=_T, end_time=_T, final_pot=20),
                HandRecord(id=2, dedupe_key="r1:2", room="r1", start_time=_T, end_time=_T, final_pot=40),
            ])
            await s.flush()
            s.add_all([
                HandParticipant(hand_id=1, uid=1, initial_points=10, final_points=20),
                HandParticipant(hand_id=1, uid=2, initial_points=10, final_points=0),
                HandParticipant(hand_id=2, uid=2, initial_points=20, final_points=40),
                HandParticipant(hand_id=2, uid=3, initial_points=20, final_points=0),
                HandDetail(hand_id=1, board="", button_position=0, big_blind=2),
                HandDetail(hand_id=2, board="AhKdQc2s7h", button_position=1, big_blind=2),
                HandParticipantDetail(hand_id=1, uid=1, seat_position=0, hole_cards="AsAd", vpip=True, showed=False),
                HandParticipantDetail(hand_id=1, uid=2, seat_position=1, hole_cards="7c2d", vpip=False, showed=False),
                HandParticipantDetail(hand_id=2, uid=2, seat_position=1, hole_cards="KcKs", vpip=True, showed=True),
                HandParticipantDetail(hand_id=2, uid=3, seat_position=2, hole_cards="3c4d", vpip=True, showed=True),
            ])
    return sm


def _hands_endpoint(sm, nickname: str):
    store = SessionStore(TTL)
    sid, session = store.create(nickname, nickname, T0)
    router = make_hands_router(lambda: sm, store, now=lambda: T0)
    return next(r.endpoint for r in router.routes if r.path == "/hands"), sid, session


async def test_own_cards_visible_others_hidden_unless_shown():
    sm = await _seeded()
    endpoint, sid, session = _hands_endpoint(sm, "alice")
    hands = {h["id"]: h for h in (await call(endpoint, sid, session, {"limit": 10}))["hands"]}
    h1 = {p["nickname"]: p for p in hands[1]["participants"]}
    assert h1["alice"]["hole_cards"] == ["As", "Ad"]  # 自己的牌
    assert h1["bob"]["hole_cards"] is None  # bob 没亮牌 → 别人看不到
    h2 = {p["nickname"]: p for p in hands[2]["participants"]}
    assert h2["bob"]["hole_cards"] == ["Kc", "Ks"] and h2["carol"]["hole_cards"] == ["3c", "4d"]  # 摊牌亮过
    assert hands[2]["board"] == ["Ah", "Kd", "Qc", "2s", "7h"]
    assert hands[2]["button_position"] == 1 and hands[2]["big_blind"] == 2
    assert h2["bob"]["seat_position"] == 1


async def test_stats_endpoint_ranks_by_net():
    sm = await _seeded()
    store = SessionStore(TTL)
    sid, session = store.create("alice", "alice", T0)
    router = make_leaderboard_router(lambda: sm, store, now=lambda: T0)
    endpoint = next(r.endpoint for r in router.routes if r.path == "/leaderboard/stats")
    entries = (await call(endpoint, sid, session, {}))["entries"]
    assert [(e["nickname"], e["net"], e["hands"]) for e in entries] == [
        ("bob", 10, 2), ("alice", 10, 1), ("carol", -20, 1),
    ]
    bob = entries[0]
    assert bob["vpip"] == 0.5 and bob["win_rate"] == 0.5
    assert bob["most_won_from"] == {"nickname": "carol", "amount": 20}
    assert bob["most_lost_to"] == {"nickname": "alice", "amount": 10}
    assert bob["bb_per_100"] == 250.0  # (+20/2 − 10/2) = 5bb / 2 手 × 100


def _row(hid, nick, net, *, when=_T, bb=2, vpip=True):
    return StatsRow(hid, when, bb, nick, 0, net, vpip)


def test_compute_stats_splits_losses_between_winners():
    # 一手里 carol 输 30,alice 赢 20、bob 赢 10 → carol 的 30 按 2:1 分给 alice、bob
    rows = [_row(1, "alice", 20), _row(1, "bob", 10), _row(1, "carol", -30)]
    by = {e.nickname: e for e in compute_stats(rows, None)}
    assert by["alice"].most_won_from.nickname == "carol" and by["alice"].most_won_from.amount == 20
    assert by["bob"].most_won_from.amount == 10
    assert by["carol"].most_lost_to.nickname == "alice" and by["carol"].most_lost_to.amount == 20
    assert by["alice"].most_lost_to is None


def test_compute_stats_since_filters_old_hands_and_handles_missing_details():
    old = _T - timedelta(days=2)
    rows = [_row(1, "alice", 50, when=old), _row(1, "bob", -50, when=old),
            _row(2, "alice", -5, bb=None, vpip=None), _row(2, "bob", 5, bb=None, vpip=None)]
    today = {e.nickname: e for e in compute_stats(rows, _T - timedelta(hours=1))}
    assert today["alice"].net == -5 and today["alice"].hands == 1
    assert today["alice"].vpip is None and today["alice"].bb_per_100 is None  # 旧手没明细
    total = {e.nickname: e for e in compute_stats(rows, None)}
    assert total["alice"].net == 45 and total["alice"].vpip == 1.0
