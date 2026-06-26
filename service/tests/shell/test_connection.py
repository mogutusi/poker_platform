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


def test_online_nicks_is_set_of_registered():
    mgr = ConnectionManager()
    assert mgr.online_nicks() == set()
    mgr.register(make_conn("alice"))
    mgr.register(make_conn("bob"))
    assert mgr.online_nicks() == {"alice", "bob"}


def test_online_nicks_returns_fresh_copy():
    mgr = ConnectionManager()
    mgr.register(make_conn("alice"))
    s = mgr.online_nicks()
    s.add("eve")  # 改返回的集合不得影响内部表(返回的是拷贝)
    assert mgr.online_nicks() == {"alice"}


def test_rename_rehangs_connection_to_new_nick():
    # 改昵称(presence.md):连接从 old 键重挂 new 键 + 改 Connection.nick,路由按新 nick 找得到。
    mgr = ConnectionManager()
    conn = make_conn("alice")
    mgr.register(conn)
    mgr.rename("alice", "alicia")
    assert mgr.get("alice") is None  # 旧键已移
    assert mgr.get("alicia") is conn and conn.nick == "alicia"  # 新键挂上 + Connection.nick 改


def test_rename_missing_old_is_noop():
    mgr = ConnectionManager()
    mgr.rename("nobody", "somebody")  # 未连接时改名只改库 → 连接层 no-op,不建空连接
    assert mgr.get("nobody") is None and mgr.get("somebody") is None
