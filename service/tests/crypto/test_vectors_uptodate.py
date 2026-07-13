# 加密测试向量漂移守门(同 tests/wire/test_codegen_uptodate.py 的治理,见 changes/0069):
# 改了加密原语/信封格式却没重生成 frontend/crypto-test-vectors.json → 产物与源不一致 → 本测试红。
# 另钉两条向量自身的形制:PKCS#7 整块再补一块(前端最易踩)、KDF 域字节与 channel.py 一致。

from scripts.gen_crypto_vectors import OUTPUT, build, generate


def test_vectors_file_is_up_to_date():
    expected = generate()
    assert OUTPUT.exists(), f"缺生成产物 {OUTPUT};运行 python scripts/gen_crypto_vectors.py"
    assert OUTPUT.read_text(encoding="utf-8") == expected, (
        "frontend/crypto-test-vectors.json 与加密原语不一致。重生成:cd service && python scripts/gen_crypto_vectors.py"
    )


def test_full_block_plaintext_gains_extra_pad_block():
    # PKCS#7 的「整块也再补一块」用向量自身可验:len∈{0,16} 的密文都比明文长恰一整块(16B)。
    cases = {len(bytes.fromhex(c["plaintext_hex"])): len(bytes.fromhex(c["ciphertext_hex"])) for c in build()["sm4_cbc"]["cases"]}
    assert cases[0] == 16 and cases[16] == 32  # 空串→1 块;恰一块→2 块
    assert cases[15] == 16 and cases[17] == 32  # 非整块补到下一块边界


def test_vector_frames_open_via_real_channel():
    # 最强互证:向量里的 ws 帧 / REST 信封必须能被服务器真实的 open_envelope 原样打开——
    # 杀「向量的成帧复现与 channel.py 实现漂移」(生成器若自立门户,这里必红)。
    from app.auth.channel import derive_keys, derive_rest_keys, open_envelope

    v = build()
    for section, derive in (("ws_frame", derive_keys), ("rest_envelope", derive_rest_keys)):
        enc_key, mac_key = derive(bytes.fromhex(v[section]["session_token_hex"]))
        case = v[section]["case"]
        seq, plaintext = open_envelope(enc_key, mac_key, bytes.fromhex(case["frame_hex"]), 65536)
        assert seq == case["seq"] and plaintext.decode("utf-8") == case["plaintext_utf8"]


def test_kdf_domains_match_channel_derivation():
    # 向量里的四把密钥必须等于「SM3(token‖域字节) 截断」——杀「向量与 channel.py 派生规则漂移」。
    from ttxsgm import sm3_hash_bytes

    v = build()["kdf"]
    token = bytes.fromhex(v["session_token_hex"])
    assert v["ws_enc_key_hex"] == sm3_hash_bytes(token + b"\x01")[:16].hex()
    assert v["ws_mac_key_hex"] == sm3_hash_bytes(token + b"\x02")[:32].hex()
    assert v["rest_enc_key_hex"] == sm3_hash_bytes(token + b"\x03")[:16].hex()
    assert v["rest_mac_key_hex"] == sm3_hash_bytes(token + b"\x04")[:32].hex()
