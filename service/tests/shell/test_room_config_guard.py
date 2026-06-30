"""房间参数配置进 reduce 前的 shell 上下限防护(config.md / changes/0043):
SetSmallBlind/SetBuyIn 的 amount 越界先拒(core 不 import gameconfig,故 bounds 归 shell,同房聊文本防护);
合法则构 Command(身份盖连接 nick)。授权(0 号位)/ 时机(非局中)由 reduce 兜,不在此。"""

from app import gameconfig
from app.core.commands import SetBuyIn, SetSmallBlind
from app.shell.receiver import _guard_room_config
from app.wire import client as wire_client
from tests.shell._fakes import drain, make_conn


# ── SetSmallBlind ──
def test_valid_small_blind_passes_with_connection_nick():
    conn = make_conn("alice")
    cmd = _guard_room_config(conn, wire_client.SetSmallBlind(amount=5))
    assert isinstance(cmd, SetSmallBlind) and cmd.origin == "alice" and cmd.amount == 5
    assert drain(conn) == []


def test_small_blind_below_min_rejected():
    conn = make_conn("alice")
    assert _guard_room_config(conn, wire_client.SetSmallBlind(amount=gameconfig.MIN_SMALL_BLIND - 1)) is None
    errs = drain(conn)
    assert len(errs) == 1 and errs[0].code.value == "INVALID_SMALL_BLIND"


def test_small_blind_above_max_rejected():
    conn = make_conn("alice")
    assert _guard_room_config(conn, wire_client.SetSmallBlind(amount=gameconfig.MAX_SMALL_BLIND + 1)) is None
    errs = drain(conn)
    assert len(errs) == 1 and errs[0].code.value == "INVALID_SMALL_BLIND"


def test_small_blind_at_bounds_pass():
    conn = make_conn("alice")
    lo = _guard_room_config(conn, wire_client.SetSmallBlind(amount=gameconfig.MIN_SMALL_BLIND))
    hi = _guard_room_config(conn, wire_client.SetSmallBlind(amount=gameconfig.MAX_SMALL_BLIND))
    assert isinstance(lo, SetSmallBlind) and isinstance(hi, SetSmallBlind)  # 闭区间端点放行
    assert drain(conn) == []


# ── SetBuyIn ──
def test_valid_buy_in_passes_with_connection_nick():
    conn = make_conn("alice")
    cmd = _guard_room_config(conn, wire_client.SetBuyIn(amount=200))
    assert isinstance(cmd, SetBuyIn) and cmd.origin == "alice" and cmd.amount == 200
    assert drain(conn) == []


def test_buy_in_below_min_rejected():
    conn = make_conn("alice")
    assert _guard_room_config(conn, wire_client.SetBuyIn(amount=gameconfig.MIN_BUY_IN - 1)) is None
    errs = drain(conn)
    assert len(errs) == 1 and errs[0].code.value == "INVALID_BUY_IN"


def test_buy_in_above_max_rejected():
    conn = make_conn("alice")
    assert _guard_room_config(conn, wire_client.SetBuyIn(amount=gameconfig.MAX_BUY_IN + 1)) is None
    errs = drain(conn)
    assert len(errs) == 1 and errs[0].code.value == "INVALID_BUY_IN"


def test_buy_in_at_bounds_pass():
    conn = make_conn("alice")
    lo = _guard_room_config(conn, wire_client.SetBuyIn(amount=gameconfig.MIN_BUY_IN))
    hi = _guard_room_config(conn, wire_client.SetBuyIn(amount=gameconfig.MAX_BUY_IN))
    assert isinstance(lo, SetBuyIn) and isinstance(hi, SetBuyIn)
    assert drain(conn) == []
