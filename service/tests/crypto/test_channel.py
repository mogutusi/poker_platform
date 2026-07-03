# 逐会话安全信道原语穷举(P5,见 docs/auth.md §加密信道 / changes/0057 设计 / 0058 落地)。
# 信封 iv‖ct‖mac(seq 藏 ct 内);入站铁序「结构→MAC→解密→seq」。覆盖:hmac 性质 / 派生密钥(会话密钥,无 nonce)/
# 封拆 round-trip / seq 单调(经 open 观察)/ IV 每帧新鲜 / MAC 拒伪 / 先验后解(改 ct→bad_mac 非 decrypt/seq)/
# seq 拒重放(重投·gap 后旧帧)/ 失败不推进序号 / 跨会话(异 token→bad_mac)/ 结构(过短·过长·ct 非 16 整除)/ config 接线 / fuzz。

import secrets

import pytest

from app import gameconfig
from app.auth.channel import (
    _FRAME_MIN_BYTES,
    _IV_BYTES,
    _SM4_BLOCK_BYTES,
    FrameError,
    ReplayWindow,
    SecureChannel,
    derive_keys,
    derive_rest_keys,
    hmac_sm3,
    open_envelope,
    seal_envelope,
)

_MAX = 65536  # 测试用单帧上限


def _pair(max_frame: int = _MAX) -> tuple[SecureChannel, SecureChannel]:
    # 同 session_token 派生两端信道 → 密钥一致(一端 seal / 一端 open,模拟一方向)。
    token = secrets.token_bytes(32)
    return SecureChannel.derive(token, max_frame), SecureChannel.derive(token, max_frame)


def _flip(frame: bytes, index: int) -> bytes:
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
    assert len(hmac_sm3(b"z" * 200, b"m")) == 32  # key>64B 先 SM3 收缩,不崩


# ── derive_keys(会话密钥,无 nonce)──


def test_derive_keys_shapes_and_domain_separation():
    token = secrets.token_bytes(32)
    enc, mac = derive_keys(token)
    assert len(enc) == 16 and len(mac) == 32
    assert enc != mac[:16]  # 域分隔(info 0x01 vs 0x02)
    assert derive_keys(token) == (enc, mac)  # 确定


def test_derive_keys_vary_by_token():
    enc, mac = derive_keys(secrets.token_bytes(32))
    enc2, mac2 = derive_keys(secrets.token_bytes(32))
    assert enc != enc2 and mac != mac2  # 跨会话(token)密钥不同 → 跨会话重放根除的根


# ── seal / open ──


@pytest.mark.parametrize("plaintext", [b"", b"x", b'{"type":"ping"}', b"A" * 16, b"A" * 17, b"B" * 100])
def test_seal_open_round_trip(plaintext):
    sender, receiver = _pair()
    assert receiver.open(sender.seal(plaintext)) == plaintext


def test_seq_strictly_increases_observed_via_open():
    # seq 藏密文内、外部读不到;经 receiver.open 观察其严格递增(乱序旧包被拒)。
    sender, receiver = _pair()
    f1, f2, f3 = sender.seal(b"1"), sender.seal(b"2"), sender.seal(b"3")
    assert receiver.open(f1) == b"1"
    assert receiver.open(f2) == b"2"
    assert receiver.open(f3) == b"3"


def test_fresh_iv_per_frame():
    sender, receiver = _pair()
    f1, f2 = sender.seal(b"same"), sender.seal(b"same")
    assert f1[:_IV_BYTES] != f2[:_IV_BYTES]  # IV 每帧新鲜随机
    assert f1 != f2
    assert receiver.open(f1) == b"same" and receiver.open(f2) == b"same"


# ── 防篡改(MAC)+ 先验后解 ──


def test_tampered_ct_rejected_before_decrypt():
    # 改密文首字节 → 破坏 padding/seq 与 MAC。先验后解:必在 MAC 步拒(bad_mac),
    # 绝不落到解密(decrypt_failed)或 seq(stale_seq)—— 证明 MAC 先于解密。
    sender, receiver = _pair()
    tampered = _flip(sender.seal(b"secret"), _IV_BYTES)  # 首个 ct 字节
    with pytest.raises(FrameError) as ei:
        receiver.open(tampered)
    assert ei.value.reason == "bad_mac"


def test_tampered_iv_rejected():
    sender, receiver = _pair()
    with pytest.raises(FrameError) as ei:
        receiver.open(_flip(sender.seal(b"m"), 0))  # iv 首字节(MAC 覆盖 iv)
    assert ei.value.reason == "bad_mac"


def test_tampered_mac_rejected():
    sender, receiver = _pair()
    with pytest.raises(FrameError) as ei:
        receiver.open(_flip(sender.seal(b"m"), -1))  # mac 末字节
    assert ei.value.reason == "bad_mac"


# ── 防重放(seq,解密后验)──


def test_replay_same_frame_rejected():
    sender, receiver = _pair()
    frame = sender.seal(b"m")
    assert receiver.open(frame) == b"m"
    with pytest.raises(FrameError) as ei:
        receiver.open(frame)  # 重投同帧 → 解密后 seq ≤ 已见
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
    sender, receiver = _pair()
    frame = sender.seal(b"m")
    with pytest.raises(FrameError):
        receiver.open(_flip(frame, -1))  # bad_mac,不推进 _in_seq
    assert receiver.open(frame) == b"m"  # 原帧仍可开(seq 未被吃掉)


def test_cross_session_replay_rejected():
    # 不同 session_token ⇒ 密钥不同 ⇒ 一会话的帧在另一会话 MAC 必败(跨会话重放根除)。
    a = SecureChannel.derive(secrets.token_bytes(32), _MAX)
    b = SecureChannel.derive(secrets.token_bytes(32), _MAX)
    with pytest.raises(FrameError) as ei:
        b.open(a.seal(b"m"))
    assert ei.value.reason == "bad_mac"


# ── 结构校验 ──


def test_frame_too_short_rejected():
    _, receiver = _pair()
    with pytest.raises(FrameError) as ei:
        receiver.open(b"\x00" * (_FRAME_MIN_BYTES - 1))
    assert ei.value.reason == "frame_too_short"


def test_frame_too_large_rejected():
    sender, receiver = _pair(max_frame=256)
    big = sender.seal(b"A" * 400)
    assert len(big) > 256
    with pytest.raises(FrameError) as ei:
        receiver.open(big)
    assert ei.value.reason == "frame_too_large"


def test_bad_ct_length_rejected():
    _, receiver = _pair()
    # iv(16) + ct 20B(非 16 整除) + mac(32) = 68 ≥ min,ct_len=20 → bad_ct_length。
    frame = b"\x00" * _IV_BYTES + b"\x00" * 20 + b"\x00" * 32
    assert len(frame) >= _FRAME_MIN_BYTES and (20 % _SM4_BLOCK_BYTES) != 0
    with pytest.raises(FrameError) as ei:
        receiver.open(frame)
    assert ei.value.reason == "bad_ct_length"


# ── config 接线 + fuzz ──


def test_wired_to_gameconfig_max_bytes():
    token = secrets.token_bytes(32)
    max_bytes = gameconfig.WS_FRAME_MAX_BYTES
    assert isinstance(max_bytes, int) and max_bytes >= 256
    sender = SecureChannel.derive(token, max_bytes)
    receiver = SecureChannel.derive(token, max_bytes)
    assert receiver.open(sender.seal(b'{"ok":true}')) == b'{"ok":true}'


def test_fuzz_open_never_crashes():
    # 随机字节 + 真帧逐位变异喂 open,一律 FrameError、0 崩溃、0 误开。
    token = secrets.token_bytes(32)
    ch = SecureChannel.derive(token, _MAX)
    for i in range(2000):
        blob = secrets.token_bytes(i % 200)
        with pytest.raises(FrameError):
            ch.open(blob)
    real = SecureChannel.derive(token, _MAX).seal(b'{"type":"ping"}')  # 真帧,逐位变异
    for pos in range(len(real)):
        with pytest.raises(FrameError):
            SecureChannel.derive(token, _MAX).open(_flip(real, pos))


# ── REST 域(0062):derive_rest_keys / 无状态信封 / 跨信道拒 / ReplayWindow ──


def test_derive_rest_keys_domain_separated_from_ws():
    # 同一 token 派生的 REST 密钥对与 ws 密钥对四钥互异(info 0x03/0x04 vs 0x01/0x02 分域)。
    token = secrets.token_bytes(32)
    ws_enc, ws_mac = derive_keys(token)
    rest_enc, rest_mac = derive_rest_keys(token)
    assert len(rest_enc) == 16 and len(rest_mac) == 32
    assert derive_rest_keys(token) == (rest_enc, rest_mac)  # 确定
    assert len({ws_enc, rest_enc, ws_mac[:16], rest_mac[:16]}) == 4  # 四钥两两不同


def test_stateless_envelope_round_trip_echoes_seq():
    # 无状态 seal/open:调用方给 seq(REST 响应回显绑定的基础),round-trip 还原 seq + 明文。
    enc, mac = derive_rest_keys(secrets.token_bytes(32))
    frame = seal_envelope(enc, mac, 42, b'{"limit":10}')
    seq, plaintext = open_envelope(enc, mac, frame, _MAX)
    assert (seq, plaintext) == (42, b'{"limit":10}')


def test_cross_protocol_frame_rejected_by_mac():
    # 跨信道重放被密钥分域根治:ws 密钥封的帧喂 REST 密钥 open(或反向)→ bad_mac(结构同、密钥异)。
    token = secrets.token_bytes(32)
    ws_enc, ws_mac = derive_keys(token)
    rest_enc, rest_mac = derive_rest_keys(token)
    ws_frame = seal_envelope(ws_enc, ws_mac, 7, b'{"type":"leave_room"}')
    with pytest.raises(FrameError) as ei:
        open_envelope(rest_enc, rest_mac, ws_frame, _MAX)  # ws 帧 → REST 域
    assert ei.value.reason == "bad_mac"
    rest_frame = seal_envelope(rest_enc, rest_mac, 7, b"{}")
    with pytest.raises(FrameError) as ei:
        open_envelope(ws_enc, ws_mac, rest_frame, _MAX)  # REST 信封 → ws 域
    assert ei.value.reason == "bad_mac"


def test_replay_window_monotonic_and_duplicate():
    w = ReplayWindow(4)
    assert w.accept(1) and w.accept(2) and w.accept(3)  # 顺序推进
    assert not w.accept(2)  # 窗内重复 = 重放,拒
    assert not w.accept(0) and not w.accept(-5)  # 非正 seq 一律拒


def test_replay_window_accepts_out_of_order_within_window():
    # 并发/乱序:seq 5 先到、3 后到(窗宽 4,floor=1)→ 都收;3 再来 = 重放拒。
    w = ReplayWindow(4)
    assert w.accept(5)
    assert w.accept(3)
    assert not w.accept(3)


def test_replay_window_rejects_too_old():
    # 滑出窗口(seq ≤ top-size)无从判重 → 一律拒:top=10、size=4 → floor=6,seq 6 拒、7 收。
    w = ReplayWindow(4)
    assert w.accept(10)
    assert not w.accept(6)
    assert w.accept(7)


def test_replay_window_slides_and_prunes():
    # 推进时剔除滑出的旧项(内部集合不无界长):大步推进后旧 seq 全拒、窗内新 seq 仍可乱序收。
    w = ReplayWindow(3)
    assert all(w.accept(s) for s in (1, 2, 3))
    assert w.accept(100)  # top=100,floor=97;1/2/3 全滑出
    assert not w.accept(3) and not w.accept(97)  # 太旧
    assert w.accept(99) and w.accept(98)  # 窗内乱序
    assert not w.accept(99)  # 重复
    assert len(w._seen) <= 3  # 集合被剪(不超窗宽)


def test_secure_channel_delegates_to_stateless_envelope():
    # 委托回归:SecureChannel.seal 出的帧,用同钥无状态 open_envelope 可拆且 seq 从 1 递增(0062 抽取未变行为)。
    token = secrets.token_bytes(32)
    ch = SecureChannel.derive(token, _MAX)
    enc, mac = derive_keys(token)
    for expect_seq in (1, 2, 3):
        seq, plaintext = open_envelope(enc, mac, ch.seal(b"x"), _MAX)
        assert (seq, plaintext) == (expect_seq, b"x")


def test_kdf_info_bytes_pinned_known_answer():
    # 客户端契约钉死(known-answer):ws=info 0x01/0x02、REST=info 0x03/0x04(auth.md §加密信道)。
    # 不钉此项则悄悄换 info 字节(如 enc/mac 互换)全绿——前端按文档实现即全线 MAC 败(杀该变异)。
    from ttxsgm import KDF_sm3

    token = secrets.token_bytes(32)
    assert derive_keys(token) == (KDF_sm3(token + b"\x01", 16), KDF_sm3(token + b"\x02", 32))
    assert derive_rest_keys(token) == (KDF_sm3(token + b"\x03", 16), KDF_sm3(token + b"\x04", 32))
