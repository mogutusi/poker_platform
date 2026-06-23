"""ConnectionManager:登记/顶替/身份判定/路由(connection.md 顶替语义)。"""

from app.shell.connection import ConnectionManager
from tests.shell._fakes import make_conn


def test_register_returns_displaced_old_connection():
    mgr = ConnectionManager()
    a1 = make_conn("alice")
    assert mgr.register(a1) is None  # 首登:无旧连接
    a2 = make_conn("alice")
    assert mgr.register(a2) is a1  # 同 nick 再登:返回被顶掉的旧连接
    assert mgr.get("alice") is a2  # 当前连接已是新的


def test_is_current_distinguishes_displaced():
    mgr = ConnectionManager()
    a1 = make_conn("alice")
    mgr.register(a1)
    a2 = make_conn("alice")
    mgr.register(a2)
    assert mgr.is_current(a2) is True
    assert mgr.is_current(a1) is False  # 旧连接:退出时据此**不**投 Disconnect


def test_unregister_only_deletes_self():
    mgr = ConnectionManager()
    a1 = make_conn("alice")
    mgr.register(a1)
    a2 = make_conn("alice")
    mgr.register(a2)
    mgr.unregister(a1)  # 旧连接退出:登记的是 a2,不是 a1 → no-op
    assert mgr.get("alice") is a2  # 新连接未被误删
    mgr.unregister(a2)
    assert mgr.get("alice") is None


def test_get_missing_is_none():
    assert ConnectionManager().get("nobody") is None
