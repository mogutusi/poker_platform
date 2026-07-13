#!/usr/bin/env python3
# 国密已知答案测试向量生成器:从后端**同一套原语**(lib/ttxsgm + app/auth/channel.py)确定性生成
# frontend/crypto-test-vectors.json,供前端 TS 加密实现写单测——逐字节对上向量,再连真服务器
# (changes/0069;治理同 gen_wire_ts.py:产物提交进 git、漂移由 tests/crypto/test_vectors_uptodate.py 守门)。
#
# 覆盖面按「前端最易踩的坑」选:SM4-CBC 的 PKCS#7 填充(长度 0/15/16/17/33,**整块也再补一块**)、
# HMAC-SM3 的超块长 key 收缩、KDF 四个域字节(ws 0x01/0x02、REST 0x03/0x04)、ws 帧与 REST 信封的
# 完整成帧(seq=1 起、8B 大端、MAC 盖 iv‖ct)、登录 blob 双向。**零随机**:key/iv/seq/明文全部固定。
#
# 用法:
#   python scripts/gen_crypto_vectors.py           # 生成/覆盖 frontend/crypto-test-vectors.json
#   python scripts/gen_crypto_vectors.py --check   # 不写盘;产物与源不一致则退出码 1

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # service/ 根,使 `app` 可导入

from ttxsgm import KDF_sm3, sm3_hash_bytes, sm4_cbc_enc

from app.auth.channel import derive_keys, derive_rest_keys, hmac_sm3

OUTPUT = Path(__file__).resolve().parents[2] / "frontend" / "crypto-test-vectors.json"

# ── 固定素材(向量专用样例值,非真实密钥)──
_KEY16 = bytes.fromhex("0123456789abcdeffedcba9876543210")  # SM4 密钥样例(16B)
_IV = bytes.fromhex("000102030405060708090a0b0c0d0e0f")  # CBC IV 样例(真实通信中每帧新随机,向量固定以可复现)
_TOKEN = bytes.fromhex("00112233445566778899aabbccddeeff" "ffeeddccbbaa99887766554433221100")  # 会话 token 样例(32B)
_KUSER = bytes.fromhex("a0a1a2a3a4a5a6a7a8a9aaabacadaeaf")  # K_user 样例(16B)


def _sm4_case(plaintext: bytes) -> dict:
    return {
        "plaintext_hex": plaintext.hex(),
        "ciphertext_hex": sm4_cbc_enc(_KEY16, _IV, plaintext).hex(),
    }


def _hmac_case(key: bytes, msg: bytes) -> dict:
    return {"key_hex": key.hex(), "msg_hex": msg.hex(), "mac_hex": hmac_sm3(key, msg).hex()}


def _frame(enc_key: bytes, mac_key: bytes, seq: int, plaintext: bytes) -> dict:
    # 与 channel.seal_envelope 同构(那边 IV 随机,这里固定 _IV 以可复现;成帧规则一字不差)。
    ct = sm4_cbc_enc(enc_key, _IV, seq.to_bytes(8, "big") + plaintext)
    mac = hmac_sm3(mac_key, _IV + ct)
    return {
        "seq": seq,
        "iv_hex": _IV.hex(),
        "plaintext_utf8": plaintext.decode("utf-8"),
        "ct_hex": ct.hex(),
        "mac_hex": mac.hex(),
        "frame_hex": (_IV + ct + mac).hex(),
    }


def build() -> dict:
    ws_enc, ws_mac = derive_keys(_TOKEN)
    rest_enc, rest_mac = derive_rest_keys(_TOKEN)
    login_req = b'{"password":"pw123","client_nonce":"nonce-1","ts":1000000}'
    login_resp = (
        b'{"session_id":"sid-1","session_token":"' + _TOKEN.hex().encode() + b'","exp":1003600.0,"rotate":false}'
    )
    return {
        "_readme": (
            "国密已知答案测试向量(由 service/scripts/gen_crypto_vectors.py 从后端原语生成,勿手改)。"
            "用途:前端 TS 加密实现的单元测试基准——每节先逐字节对上,再连真服务器。"
            "字节约定:所有 *_hex 为小写 hex;JSON 明文以 *_utf8 给出精确串(线上任意 JSON 格式皆可,向量需字节精确)。"
        ),
        "sm3": {
            "_note": "标准 GB/T 32905 SM3;含空串与分组边界(55/64 字节)输入。",
            "cases": [
                {"input_utf8": "", "hash_hex": sm3_hash_bytes(b"").hex()},
                {"input_utf8": "abc", "hash_hex": sm3_hash_bytes(b"abc").hex()},
                {"input_utf8": "天地玄黄", "hash_hex": sm3_hash_bytes("天地玄黄".encode()).hex()},
                {"input_hex": bytes(range(55)).hex(), "hash_hex": sm3_hash_bytes(bytes(range(55))).hex()},
                {"input_hex": bytes(range(64)).hex(), "hash_hex": sm3_hash_bytes(bytes(range(64))).hex()},
            ],
        },
        "sm4_cbc": {
            "_note": (
                "SM4-CBC + PKCS#7 填充。坑:明文恰为 16 字节整数倍时(含空串)**仍要再补一整块** 0x10×16——"
                "看 len=0/16 两例的密文都比明文多整块。解密方向用同一组用例反着验(dec(ct)==plaintext)。"
            ),
            "key_hex": _KEY16.hex(),
            "iv_hex": _IV.hex(),
            "cases": [_sm4_case(bytes(range(n))) for n in (0, 15, 16, 17, 33)],
        },
        "hmac_sm3": {
            "_note": "标准 HMAC 构造:块长 64B;key>64B 先 SM3 收缩;短 key 补零到 64;ipad=0x36/opad=0x5C。",
            "cases": [
                _hmac_case(_KEY16, b""),
                _hmac_case(_KEY16, b"abc"),
                _hmac_case(bytes(range(64)), b"message"),
                _hmac_case(bytes(range(100)), b"message"),  # 超块长 → 先 SM3(key)
            ],
        },
        "kdf": {
            "_note": "KDF_sm3(x, n) = SM3(x) 的前 n 字节。四个域:ws enc/mac = token‖0x01/0x02,REST = token‖0x03/0x04。",
            "session_token_hex": _TOKEN.hex(),
            "ws_enc_key_hex": ws_enc.hex(),
            "ws_mac_key_hex": ws_mac.hex(),
            "rest_enc_key_hex": rest_enc.hex(),
            "rest_mac_key_hex": rest_mac.hex(),
        },
        "ws_frame": {
            "_note": (
                "ws 二进制帧 = iv(16B)‖ct‖mac(32B);ct = SM4(enc_key, iv, seq(8B 大端)‖明文 JSON);"
                "mac = HMAC(mac_key, iv‖ct)。首帧 seq=1。密钥用上面 kdf 节的 ws 对。"
            ),
            "session_token_hex": _TOKEN.hex(),
            "case": _frame(ws_enc, ws_mac, 1, b'{"type":"room_chat","text":"hi"}'),
        },
        "rest_envelope": {
            "_note": (
                "REST 信封 frame 字段的内容(请求 body 形为 {sid, frame});成帧同 ws 帧但用 REST 域密钥,"
                "内层无参数时明文就是 {}。响应帧内层 seq 回显请求 seq。"
            ),
            "session_token_hex": _TOKEN.hex(),
            "case": _frame(rest_enc, rest_mac, 1, b"{}"),
        },
        "login_blob": {
            "_note": (
                "登录请求 blob = SM4(K_user, iv, 请求 JSON) 的 hex(无 seq、无 MAC——引导信道只有这一处例外);"
                "响应 blob 同法加密,给出解密方向用例。"
            ),
            "k_user_hex": _KUSER.hex(),
            "iv_hex": _IV.hex(),
            "request": {
                "plaintext_utf8": login_req.decode(),
                "blob_hex": sm4_cbc_enc(_KUSER, _IV, login_req).hex(),
            },
            "response": {
                "plaintext_utf8": login_resp.decode(),
                "blob_hex": sm4_cbc_enc(_KUSER, _IV, login_resp).hex(),
            },
        },
    }


def generate() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str]) -> int:
    rendered = generate()
    if "--check" in argv:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            print(f"OUT OF DATE: {OUTPUT}(重生成:python scripts/gen_crypto_vectors.py)", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
