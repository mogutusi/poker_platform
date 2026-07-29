# 登录凭证校验穷举(P5,见 docs/auth.md §登录握手 / changes/0056)。纯逻辑(无 DB):
# 用已知 K_user 封 blob → authenticate 解验。覆盖:正路 / 错密码 / 错 K_user / 未启用(NULL)/
# 坏 k_user_hex / 坏 iv·blob 长度 / 非 JSON / 缺字段(password/client_nonce/ts)/ 非 dict / 字段类型不符(含 ts 非数值/bool)/ unicode / fail-closed 不崩。

import json
import secrets

import pytest
from ttxsgm import sm4_cbc_enc

from app.auth.credentials import LoginProof, authenticate
from app.auth.passwords import hash_password

_ROUNDS = 500  # 小轮数(authenticate 行为与轮数无关,只为快)
_PASSWORD = "correct horse battery staple"
_NONCE = "nonce-abc-123"
_TS = 1_000_000.0  # blob 内客户端墙钟(重放守卫用,0063;authenticate 只透出、不判新鲜)


def _payload(**over) -> dict:
    # 合法全字段 blob 载荷;over 单点变异(缺字段测试用显式 dict)。
    return {"password": _PASSWORD, "client_nonce": _NONCE, "ts": _TS, **over}


def _key_hex() -> tuple[bytes, str]:
    k = secrets.token_bytes(16)
    return k, k.hex()


def _seal(key: bytes, raw: bytes) -> tuple[bytes, bytes]:
    # 用 key 封任意明文字节(iv 随机)。
    iv = secrets.token_bytes(16)
    return iv, sm4_cbc_enc(key, iv, raw)


def _seal_json(key: bytes, obj) -> tuple[bytes, bytes]:
    return _seal(key, json.dumps(obj).encode("utf-8"))


def test_happy_path_returns_proof_with_client_nonce():
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    iv, blob = _seal_json(key, _payload())
    proof = authenticate(hp, key_hex, iv, blob)
    assert isinstance(proof, LoginProof) and proof.client_nonce == _NONCE
    assert proof.ts == _TS  # ts 透出供端点验 freshness(0063)


def test_wrong_password_rejected():
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    iv, blob = _seal_json(key, _payload(password="WRONG"))
    assert authenticate(hp, key_hex, iv, blob) is None


def test_wrong_k_user_rejected():
    # 用别的 K_user 封 → 服务器用登记的 K_user 解出乱码 → JSON 坏 → None。
    _, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    other = secrets.token_bytes(16)
    iv, blob = _seal_json(other, _payload())
    assert authenticate(hp, key_hex, iv, blob) is None


def test_login_not_enabled_when_columns_null():
    key, key_hex = _key_hex()
    iv, blob = _seal_json(key, _payload())
    hp = hash_password(_PASSWORD, _ROUNDS)
    assert authenticate(None, key_hex, iv, blob) is None  # 无密码哈希
    assert authenticate(hp, None, iv, blob) is None  # 无 K_user


@pytest.mark.parametrize("bad_hex", ["zz" * 16, "abc", "ab" * 8, "ab" * 20])
def test_bad_k_user_hex_rejected(bad_hex):
    # 非 hex / 奇数长 / 长度非 16B → fail-closed None。
    hp = hash_password(_PASSWORD, _ROUNDS)
    iv, blob = _seal(secrets.token_bytes(16), b"whatever--------")  # 16B
    assert authenticate(hp, bad_hex, iv, blob) is None


def test_bad_iv_length_rejected():
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    _, blob = _seal_json(key, _payload())
    assert authenticate(hp, key_hex, secrets.token_bytes(15), blob) is None
    assert authenticate(hp, key_hex, secrets.token_bytes(17), blob) is None


@pytest.mark.parametrize("blob", [b"", b"short", b"A" * 17, b"A" * 31])
def test_bad_blob_length_rejected(blob):
    # 空 / 非 16 整除的 blob → 拒(免喂裸去填充的 sm4_cbc_dec)。
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    assert authenticate(hp, key_hex, secrets.token_bytes(16), blob) is None


def test_non_json_plaintext_rejected():
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    iv, blob = _seal(key, b"this is not json at all {{{")
    assert authenticate(hp, key_hex, iv, blob) is None


def test_missing_password_field_rejected():
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    iv, blob = _seal_json(key, {"client_nonce": _NONCE, "ts": _TS})  # 无 password
    assert authenticate(hp, key_hex, iv, blob) is None


def test_missing_client_nonce_field_rejected():
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    iv, blob = _seal_json(key, {"password": _PASSWORD, "ts": _TS})  # 无 client_nonce
    assert authenticate(hp, key_hex, iv, blob) is None


@pytest.mark.parametrize(
    "payload",
    [[1, 2, 3], "just a string", 42, {"password": 5, "client_nonce": "x", "ts": 1.0}],
)
def test_non_dict_or_wrong_type_payload_rejected(payload):
    # payload 非 dict(subscript TypeError)或字段非 str → 拒。
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    iv, blob = _seal_json(key, payload)
    assert authenticate(hp, key_hex, iv, blob) is None


def test_missing_ts_field_rejected():
    # 缺 ts(pre-0063 老 blob 形)→ fail-closed None:重放守卫要求 blob 自带时间戳(changes/0063 决策 1)。
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    iv, blob = _seal_json(key, {"password": _PASSWORD, "client_nonce": _NONCE})
    assert authenticate(hp, key_hex, iv, blob) is None


@pytest.mark.parametrize("bad_ts", ["1000000", True, False, None, [1.0], float("nan"), float("inf"), float("-inf")])
def test_non_numeric_ts_rejected(bad_ts):
    # ts 须为 JSON **有限**数值;str/bool/None/list/NaN/±Infinity 一律拒(bool 是 int 子类显式拒;
    # NaN 与任何数比较恒 False → 端点的 |now-ts|>窗 永不成立 → freshness 形同虚设,故在此 fail-closed)。
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    iv, blob = _seal_json(key, _payload(ts=bad_ts))
    assert authenticate(hp, key_hex, iv, blob) is None


def test_huge_integer_ts_rejected_without_escaping():
    # 0074:巨整数 ts(json 把 400 位字面量解析成 Python int)——float()/math.isfinite() 对它抛 OverflowError。
    # 该异常若逃出 authenticate 会冒成端点 500(login.py 对 authenticate 无 try),既破 fail-closed 铁律,
    # 又使「500 vs 401」成为 K_user 猜测正确性的预言机(错钥在 json 解析即败 → 401)。此处钉:必须返回 None、不抛。
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    for huge in (int("9" * 400), -int("9" * 400)):
        iv, blob = _seal_json(key, _payload(ts=huge))
        assert authenticate(hp, key_hex, iv, blob) is None  # 修复前:OverflowError 逃逸


def test_integer_ts_accepted_as_float():
    # JSON 整数 ts(客户端取整秒)合法,透出为 float。
    key, key_hex = _key_hex()
    hp = hash_password(_PASSWORD, _ROUNDS)
    iv, blob = _seal_json(key, _payload(ts=1_000_000))
    proof = authenticate(hp, key_hex, iv, blob)
    assert isinstance(proof, LoginProof) and proof.ts == 1_000_000.0


def test_unicode_password_round_trips():
    key, key_hex = _key_hex()
    pw = "密码🔒Ω"
    hp = hash_password(pw, _ROUNDS)
    iv, blob = _seal_json(key, _payload(password=pw))
    proof = authenticate(hp, key_hex, iv, blob)
    assert isinstance(proof, LoginProof) and proof.client_nonce == _NONCE


def test_fuzz_garbage_never_crashes():
    # 不可信 iv/blob 的畸形输入一律 None、绝不崩(登录端点健壮性)。
    hp = hash_password(_PASSWORD, _ROUNDS)
    _, key_hex = _key_hex()
    for i in range(400):
        iv = secrets.token_bytes(i % 20)
        blob = secrets.token_bytes((i * 7) % 48)
        assert authenticate(hp, key_hex, iv, blob) is None
