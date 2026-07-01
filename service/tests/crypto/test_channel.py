# 逐帧安全信道原语穷举(P5,见 docs/auth.md §WS 安全信道 / changes/0054)。
# 覆盖:hmac 性质 / 派生密钥(长度·确定·跨 nonce/token 异·域分隔)/ 封拆 round-trip /
# seq 单调 / IV 每帧新鲜 / MAC 拒伪 / seq 拒重放(重投·gap 后旧帧)/ 先验后解(改 ct → bad_mac 而非
# decrypt_failed,证 MAC 先于解密)/ 失败不推进序号 / 跨连接重放 / 帧过短·过长·ct 非 16 整除 / config 接线。

import secrets

import pytest

from app import gameconfig
from app.auth.channel import (
    _FRAME_MIN_BYTES,
    _IV_BYTES,
    _SEQ_BYTES,
    _SM4_BLOCK_BYTES,
    FrameError,
    SecureChannel,
    derive_keys,
    hmac_sm3,
)

_MAX = 65536  # 测试用单帧上限(够放常规帧)


def _pair(max_frame: int = _MAX) -> tuple[SecureChannel, SecureChannel]:
    # 同 (token, nonce) 派生两端信道 → 密钥一致(client 封 / server 拆同一方向)。
    token = secrets.token_bytes(32)
    nonce = secrets.token_bytes(16)
    return (
        SecureChannel.derive(token, nonce, max_frame),
        SecureChannel.derive(token, nonce, max_frame),
    )


def _flip(frame: bytes, index: int) -> bytes:
    # 翻转指定字节的所有位(篡改)。
    buf = bytearray(frame)
    buf[index] ^= 0xFF
    return bytes(buf)


# ── hmac_sm3 ──


def test_hmac_sm3_properties():
    key, msg = b"k" * 32, b"message"
    mac = hmac_sm3(key, msg)
    assert len(mac) == 32
    assert hmac_sm3(key, msg) == mac  # 确定
    assert hmac_sm3(b"k" * 32 + b"x", msg) != mac  # 换 key
    assert hmac_sm3(key, b"message!") != mac  # 换 msg


def test_hmac_sm3_long_key_shrinks():
    # key > 块长(64B)先 SM3 收缩再用;不崩、仍出 32B。
    assert len(hmac_sm3(b"z" * 200, b"m")) == 32


# ── derive_keys ──


def test_derive_keys_shapes_and_domain_separation():
    token, nonce = secrets.token_bytes(32), secrets.token_bytes(16)
    enc, mac = derive_keys(token, nonce)
    assert len(enc) == 16 and len(mac) == 32
    assert enc != mac[:16]  # 域分隔(info 0x01 vs 0x02)→ 两钥不同源
    assert derive_keys(token, nonce) == (enc, mac)  # 确定


def test_derive_keys_vary_by_nonce_and_token():
    token, nonce = secrets.token_bytes(32), secrets.token_bytes(16)
    enc, mac = derive_keys(token, nonce)
    enc_n, mac_n = derive_keys(token, secrets.token_bytes(16))  # 换 nonce
    enc_t, mac_t = derive_keys(secrets.token_bytes(32), nonce)  # 换 token
    assert enc_n != enc and mac_n != mac  # 跨连接(nonce)密钥不同 → 跨重连重放根除的根
    assert enc_t != enc and mac_t != mac  # 跨用户(token)密钥不同


# ── seal / open ──


@pytest.mark.parametrize(
    "plaintext",
    [b"", b"x", b'{"type":"ping"}', b"A" * 16, b"A" * 17, b"B" * 100],
)
def test_seal_open_round_trip(plaintext):
    sender, receiver = _pair()
    assert receiver.open(sender.seal(plaintext)) == plaintext


def test_seq_increments_per_seal():
    sender, _ = _pair()
    seqs = [int.from_bytes(sender.seal(b"m")[:_SEQ_BYTES], "big") for _ in range(3)]
    assert seqs == [1, 2, 3]  # 每连接从 1 起、严格递增


def test_fresh_iv_per_frame():
    sender, receiver = _pair()
    f1, f2 = sender.seal(b"same"), sender.seal(b"same")
    iv1 = f1[_SEQ_BYTES : _SEQ_BYTES + _IV_BYTES]
    iv2 = f2[_SEQ_BYTES : _SEQ_BYTES + _IV_BYTES]
    assert iv1 != iv2  # IV 每帧新鲜随机(非计数器)
    assert f1 != f2  # 同明文两封整帧不同
    assert receiver.open(f1) == b"same" and receiver.open(f2) == b"same"


# ── 防篡改(MAC)──


def test_tampered_ct_rejected_before_decrypt():
    # 改密文首字节 → 破坏 padding 与 MAC。先验后解:必在 MAC 步拒(bad_mac),绝不落到解密(decrypt_failed)。
    sender, receiver = _pair()
    frame = sender.seal(b"secret")
    tampered = _flip(frame, _SEQ_BYTES + _IV_BYTES)  # 首个 ct 字节
    with pytest.raises(FrameError) as ei:
        receiver.open(tampered)
    assert ei.value.reason == "bad_mac"  # 证明 MAC 先于解密(否则会是 decrypt/padding 错)


def test_tampered_iv_rejected():
    sender, receiver = _pair()
    with pytest.raises(FrameError) as ei:
        receiver.open(_flip(sender.seal(b"m"), _SEQ_BYTES))  # iv 首字节(MAC 覆盖 iv)
    assert ei.value.reason == "bad_mac"


def test_tampered_mac_rejected():
    sender, receiver = _pair()
    with pytest.raises(FrameError) as ei:
        receiver.open(_flip(sender.seal(b"m"), -1))  # mac 末字节
    assert ei.value.reason == "bad_mac"


# ── 防重放(seq)──


def test_replay_same_frame_rejected():
    sender, receiver = _pair()
    frame = sender.seal(b"m")
    assert receiver.open(frame) == b"m"
    with pytest.raises(FrameError) as ei:
        receiver.open(frame)  # 重投同帧
    assert ei.value.reason == "stale_seq"


def test_out_of_order_gap_then_old_rejected():
    sender, receiver = _pair()
    f1, f2, f3 = sender.seal(b"1"), sender.seal(b"2"), sender.seal(b"3")
    assert receiver.open(f1) == b"1"  # seq 1
    assert receiver.open(f3) == b"3"  # seq 3 > 1 → 收(gap 允许)
    with pytest.raises(FrameError) as ei:
        receiver.open(f2)  # seq 2 ≤ 3 → 拒(旧帧)
    assert ei.value.reason == "stale_seq"


def test_failed_open_does_not_advance_in_seq():
    # 被拒的帧不推进入站序号:篡改帧被拒后,原始合法帧仍可开。
    sender, receiver = _pair()
    frame = sender.seal(b"m")
    with pytest.raises(FrameError):
        receiver.open(_flip(frame, -1))  # bad_mac,不推进 _in_seq
    assert receiver.open(frame) == b"m"  # 原帧仍可开(seq 未被吃掉)


def test_cross_connection_replay_rejected():
    # 不同 server_nonce ⇒ 逐连接密钥不同 ⇒ 一条连接的帧在另一条 MAC 必败(跨重连重放根除)。
    token = secrets.token_bytes(32)
    conn_a = SecureChannel.derive(token, secrets.token_bytes(16), _MAX)
    conn_b = SecureChannel.derive(token, secrets.token_bytes(16), _MAX)
    with pytest.raises(FrameError) as ei:
        conn_b.open(conn_a.seal(b"m"))
    assert ei.value.reason == "bad_mac"


# ── 结构校验 ──


def test_frame_too_short_rejected():
    _, receiver = _pair()
    with pytest.raises(FrameError) as ei:
        receiver.open(b"\x00" * (_FRAME_MIN_BYTES - 1))
    assert ei.value.reason == "frame_too_short"


def test_frame_too_large_rejected():
    sender, receiver = _pair(max_frame=256)
    big = sender.seal(b"A" * 400)  # 封出的帧 > 256
    assert len(big) > 256
    with pytest.raises(FrameError) as ei:
        receiver.open(big)
    assert ei.value.reason == "frame_too_large"


def test_bad_ct_length_rejected():
    _, receiver = _pair()
    # 头(seq+iv=24)+ ct 20B(非 16 整除)+ mac(32)= 76 ≥ min,ct_len=20 → bad_ct_length。
    frame = b"\x00" * _SEQ_BYTES + b"\x00" * _IV_BYTES + b"\x00" * 20 + b"\x00" * 32
    assert len(frame) >= _FRAME_MIN_BYTES and (20 % _SM4_BLOCK_BYTES) != 0
    with pytest.raises(FrameError) as ei:
        receiver.open(frame)
    assert ei.value.reason == "bad_ct_length"


# ── config 接线 ──


def test_wired_to_gameconfig_max_bytes():
    token, nonce = secrets.token_bytes(32), secrets.token_bytes(16)
    max_bytes = gameconfig.WS_FRAME_MAX_BYTES
    assert isinstance(max_bytes, int) and max_bytes >= 256
    sender = SecureChannel.derive(token, nonce, max_bytes)
    receiver = SecureChannel.derive(token, nonce, max_bytes)
    assert receiver.open(sender.seal(b'{"ok":true}')) == b'{"ok":true}'
