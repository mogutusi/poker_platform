# 密码存储原语穷举(P5,见 docs/auth.md §密码存储 / changes/0053)。
# 覆盖:round-trip / 错密码 / 存储格式 / 盐唯一 / 轮数写进串且 verify 按存储轮数(改配置不废旧哈希)/
# fail-closed 非法串 / 篡改拒 / rounds<1 护栏 / 空 & unicode 密码 / 确定性 / config 接线。
# 除接线测走 gameconfig 真值外,一律用小 rounds 提速(原语正确性与轮数无关)。

import pytest

from app import gameconfig
from app.auth.passwords import _derive, hash_password, verify_password

_ROUNDS = 1000  # 测试用小轮数(原语行为与轮数无关,小值只为快)


def test_round_trip_correct_password():
    stored = hash_password("hunter2", _ROUNDS)
    assert verify_password("hunter2", stored) is True


def test_wrong_password_rejected():
    stored = hash_password("hunter2", _ROUNDS)
    assert verify_password("hunter3", stored) is False
    assert verify_password("Hunter2", stored) is False  # 大小写敏感
    assert verify_password("", stored) is False


def test_storage_format_shape():
    # "salt_hex$rounds$digest_hex":盐 16B=32hex、轮数原样、SM3 摘要 32B=64hex。
    stored = hash_password("pw", _ROUNDS)
    parts = stored.split("$")
    assert len(parts) == 3
    salt_hex, rounds_str, digest_hex = parts
    assert len(salt_hex) == 32 and bytes.fromhex(salt_hex)  # 合法 hex,16 字节
    assert rounds_str == str(_ROUNDS)
    assert len(digest_hex) == 64 and bytes.fromhex(digest_hex)  # 合法 hex,32 字节


def test_salt_is_random_per_hash():
    # 同密码两次哈希 → 盐不同 → 串不同,但都能校验过(盐挡「同密码同哈希」)。
    a = hash_password("samepw", _ROUNDS)
    b = hash_password("samepw", _ROUNDS)
    assert a != b
    assert a.split("$")[0] != b.split("$")[0]  # 盐不同
    assert verify_password("samepw", a) and verify_password("samepw", b)


def test_verify_uses_stored_rounds_not_current():
    # 轮数写进串;verify 按存储轮数重放,与「当前配置轮数」无关 → 改 PWD_HASH_ROUNDS 不废旧哈希。
    stored = hash_password("pw", 7)
    assert stored.split("$")[1] == "7"
    assert gameconfig.PWD_HASH_ROUNDS != 7  # 前置:现行配置 ≠ 存储轮数,下一条才真证明「独立于当前配置」
    assert verify_password("pw", stored) is True  # 按存储的 7 轮校验过(而非现行配置轮数)


def test_rounds_minimum_boundary_round_trips():
    # 边界:rounds=1(合法最小,gameconfig ge=1)端到端可用——正确密码过、错密码拒。
    stored = hash_password("pw", 1)
    assert stored.split("$")[1] == "1"
    assert verify_password("pw", stored) is True
    assert verify_password("nope", stored) is False


@pytest.mark.parametrize(
    "bad_stored",
    [
        "",  # 空串
        "notahash",  # 无分隔
        "aa$5",  # 只 2 段
        "aa$5$bb$cc",  # 4 段
        "xyz$5$" + "ab" * 32,  # 盐非 hex
        "ab" * 16 + "$notint$" + "ab" * 32,  # 轮数非整
        "ab" * 16 + "$5$zz",  # 摘要非 hex
        "ab" * 16 + "$0$" + "ab" * 32,  # 轮数 <1(护栏)
        "ab" * 16 + "$-3$" + "ab" * 32,  # 轮数负
    ],
)
def test_fail_closed_on_malformed_stored(bad_stored):
    # 无法解析/无法校验一律 False,绝不放行、绝不崩(脏 DB 行不该炸登录)。
    assert verify_password("pw", bad_stored) is False


def test_digest_length_mismatch_rejected():
    # 摘要 hex 合法但字节数不对(非 32B)→ compare_digest 长度不等 → False,不崩。
    salt_hex = "ab" * 16
    short_digest = "ab" * 4  # 仅 4 字节
    assert verify_password("pw", f"{salt_hex}$5${short_digest}") is False


def test_tampered_digest_rejected():
    # 篡改摘要末位 → 校验失败(检测存储被改)。
    stored = hash_password("pw", _ROUNDS)
    salt_hex, rounds_str, digest_hex = stored.split("$")
    flipped = digest_hex[:-1] + ("0" if digest_hex[-1] != "0" else "1")
    assert verify_password("pw", f"{salt_hex}${rounds_str}${flipped}") is False


def test_hash_password_rejects_rounds_below_one():
    # 原语红线:rounds<1 会退化成近乎存明文 → raise(正常路径由 gameconfig ge=1 兜)。
    with pytest.raises(ValueError):
        hash_password("pw", 0)
    with pytest.raises(ValueError):
        hash_password("pw", -1)


def test_derive_rejects_rounds_below_one():
    with pytest.raises(ValueError):
        _derive("pw", b"\x00" * 16, 0)


def test_empty_password_round_trips():
    stored = hash_password("", _ROUNDS)
    assert verify_password("", stored) is True
    assert verify_password("x", stored) is False


def test_unicode_password_round_trips():
    pw = "密码🔒Ω"
    stored = hash_password(pw, _ROUNDS)
    assert verify_password(pw, stored) is True
    assert verify_password("密码🔒", stored) is False


def test_derive_is_deterministic():
    # 同 (密码, 盐, 轮数) → 同摘要(哈希无随机性,随机只在 hash_password 的新盐)。
    salt = b"\x01" * 16
    assert _derive("pw", salt, _ROUNDS) == _derive("pw", salt, _ROUNDS)
    assert _derive("pw", salt, _ROUNDS) != _derive("pw2", salt, _ROUNDS)
    assert _derive("pw", salt, _ROUNDS) != _derive("pw", b"\x02" * 16, _ROUNDS)  # 盐变摘要变
    assert _derive("pw", salt, _ROUNDS) != _derive("pw", salt, _ROUNDS + 1)  # 轮数变摘要变


def test_wired_to_gameconfig_rounds():
    # 接线:调用方从 gameconfig.PWD_HASH_ROUNDS 取轮数(注册/改密的真实用法),round-trip 通。
    rounds = gameconfig.PWD_HASH_ROUNDS
    assert isinstance(rounds, int) and rounds >= 1
    stored = hash_password("cfgpw", rounds)
    assert stored.split("$")[1] == str(rounds)
    assert verify_password("cfgpw", stored) is True
