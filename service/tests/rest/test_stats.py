"""个人统计(0111):VPIP/PFR/3-Bet/C-Bet/WTSD 等的口径、位置、起手牌格子、时间过滤。"""

import json
from datetime import datetime, timedelta, timezone

from app.db.queries import PlayerHandRow
from app.rest.stats import combo_of, compute_player_stats, position_names

T = datetime(2026, 1, 1, tzinfo=timezone.utc)
ME, V1, V2 = 1, 2, 3


def row(hid, net, actions, *, hole="AsKs", board="", showed=False, seat=0, seats=(0, 1, 2), button=0, when=T):
    return PlayerHandRow(hid, when, 0, net, 2, button, board, seat, hole, None, showed, seats, json.dumps(actions))


def test_combo_names():
    assert combo_of("AsKs") == "AKs" and combo_of("Kd9s") == "K9o" and combo_of("9sKd") == "K9o"
    assert combo_of("7c7d") == "77" and combo_of("Th2h") == "T2s"


def test_positions_match_frontend_naming():
    assert position_names([0, 1, 2], 0) == {0: "BTN", 1: "SB", 2: "BB"}
    assert position_names([3, 7], 7) == {7: "BTN/SB", 3: "BB"}


def test_three_bet_cbet_showdown():
    hands = [
        # 1. V1 开池,我 3-bet,V1 跟;翻牌我 c-bet,V1 跟;打到摊牌我赢
        row(1, 40, [
            ["pre_flop", V1, "raise", 6], ["pre_flop", ME, "raise", 18], ["pre_flop", V1, "call", 18],
            ["flop", V1, "check", 0], ["flop", ME, "bet", 20], ["flop", V1, "call", 20],
        ], board="AhKd2c7s9d", showed=True),
        # 2. 我开池,V1 3-bet,我弃(fold to 3-bet)
        row(2, -6, [
            ["pre_flop", ME, "raise", 6], ["pre_flop", V1, "raise", 18], ["pre_flop", ME, "fold", 6],
        ], hole="7c2d"),
        # 3. 只跟注大盲,翻牌被下注后弃
        row(3, -2, [
            ["pre_flop", ME, "call", 2], ["pre_flop", V2, "check", 2],
            ["flop", V2, "bet", 4], ["flop", ME, "fold", 0],
        ], hole="Qh9h", board="2s3s4s"),
    ]
    s = compute_player_stats("me", ME, hands, None).summary
    assert s.hands == 3 and s.net == 32
    assert (s.vpip.made, s.vpip.chances) == (3, 3)
    assert (s.pfr.made, s.pfr.chances) == (2, 3)
    assert (s.three_bet.made, s.three_bet.chances) == (1, 1)
    assert (s.fold_to_three_bet.made, s.fold_to_three_bet.chances) == (1, 1)
    assert (s.cbet.made, s.cbet.chances) == (1, 1)
    assert (s.saw_flop.made, s.saw_flop.chances) == (2, 3)
    assert (s.wtsd.made, s.wtsd.chances) == (1, 2)
    assert (s.wsd.made, s.wsd.chances) == (1, 1)
    assert s.af == 1.0  # 翻牌后:bet 1 次,call 0 次 → 只有主动下注,按次数给
    assert s.showdown_net == 40 and s.non_showdown_net == -8


def test_combos_positions_and_since():
    hands = [
        row(1, 10, [["pre_flop", ME, "raise", 6]], hole="AsKs", seat=0, button=0, when=T - timedelta(days=3)),
        row(2, -4, [["pre_flop", ME, "call", 2]], hole="AhKh", seat=1, button=0),
        row(3, 6, [["pre_flop", ME, "check", 2]], hole="Kd9s", seat=2, button=0),
    ]
    st = compute_player_stats("me", ME, hands, None)
    cells = {c.combo: c for c in st.combos}
    assert (cells["AKs"].hands, cells["AKs"].net, cells["AKs"].won) == (2, 6, 1)
    assert cells["K9o"].vpip == 0  # 大盲过牌不算自愿入池
    assert {p.position: p.hands for p in st.positions} == {"BTN": 1, "SB": 1, "BB": 1}
    assert [g.net for g in st.graph] == [10, 6, 12]
    assert [r.id for r in st.recent] == [3, 2, 1]  # 新→旧
    today = compute_player_stats("me", ME, hands, T - timedelta(hours=1))
    assert today.summary.hands == 2 and today.summary.net == 2


async def test_other_players_stats_only_with_dev_switch(monkeypatch):
    import pytest
    from fastapi import HTTPException

    from app import gameconfig
    from app.auth.session import SessionStore
    from app.rest.stats import make_stats_router
    from tests.rest._sealed import T0, TTL, call
    from tests.rest.test_hand_details_rest import _seeded

    sm = await _seeded()
    store = SessionStore(TTL)
    sid, session = store.create("alice", "alice", T0)
    endpoint = next(r.endpoint for r in make_stats_router(lambda: sm, store, now=lambda: T0).routes if r.path == "/stats/me")

    monkeypatch.setattr(gameconfig.config, "DEV_STATS_ANY_USER", False)
    mine = await call(endpoint, sid, session, {}, seq=1)
    assert mine["nickname"] == "alice" and mine["can_view_others"] is False
    with pytest.raises(HTTPException) as ei:
        await call(endpoint, sid, session, {"user": "bob"}, seq=2)
    assert ei.value.status_code == 403  # 别人的统计(含底牌热力图)默认不给看

    monkeypatch.setattr(gameconfig.config, "DEV_STATS_ANY_USER", True)
    bob = await call(endpoint, sid, session, {"user": "bob"}, seq=3)
    assert bob["nickname"] == "bob" and bob["summary"]["hands"] == 2 and bob["can_view_others"] is True
