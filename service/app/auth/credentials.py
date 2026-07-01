# 登录凭证校验(P5 鉴权,见 docs/auth.md §登录握手 第 2 步)。纯逻辑:用该用户 K_user 解开登录 blob
# → 取 {password, client_nonce} → verify_password 校验密码。无 IO/无 DB(账号鉴权字段由 queries.load_user_for_login
# 载入后传入)。fail-closed:iv/blob 是登录前的不可信输入,任何缺失/解密坏/JSON 坏/密码错一律返回 None
# (不放行、绝不崩登录端点)。脱敏红线(log.md):password/k_user/明文都不进日志。

import json
from dataclasses import dataclass

from ttxsgm import sm4_cbc_dec

from app.auth.passwords import verify_password

_KEY_BYTES = 16  # K_user = SM4 128-bit;hex 解回须恰为此长度
_SM4_BLOCK_BYTES = 16  # SM4 分组;blob 须为其正整数倍(否则 sm4_cbc_dec 会对畸形输入报错)


@dataclass
class LoginProof:
    # authenticate 成功产出:密码已验通过。client_nonce 供端点做登录包重放防护(auth.md;端点砖落地)。
    client_nonce: str


def authenticate(
    hash_password: str | None, k_user_hex: str | None, iv: bytes, blob: bytes
) -> LoginProof | None:
    # 校验一次登录凭证(fail-closed)。hash_password/k_user_hex 来自 DB(load_user_for_login);iv/blob 来自客户端(不可信)。
    if hash_password is None or k_user_hex is None:
        return None  # 该用户未启用登录(NULL 鉴权列)
    if len(iv) != _SM4_BLOCK_BYTES or len(blob) == 0 or len(blob) % _SM4_BLOCK_BYTES != 0:
        return None  # 结构非法的 iv/blob 直接拒(免把畸形输入喂给裸去填充的 sm4_cbc_dec)
    try:
        k_user = bytes.fromhex(k_user_hex)
        if len(k_user) != _KEY_BYTES:
            return None  # k_user 长度异常(DB 配置错)→ fail-closed
        plaintext = sm4_cbc_dec(k_user, iv, blob)
        payload = json.loads(plaintext)  # bytes → utf-8 → JSON;坏 utf-8/坏 JSON 抛 (ValueError 系)
        password = payload["password"]
        client_nonce = payload["client_nonce"]
    except (ValueError, KeyError, TypeError):
        return None  # 解密坏 / JSON 坏 / 缺字段 / payload 非 dict → 一律不放行
    if not isinstance(password, str) or not isinstance(client_nonce, str):
        return None  # 字段类型不符 → 拒
    if not verify_password(password, hash_password):
        return None  # 密码错(verify_password 常量时间 + fail-closed)
    return LoginProof(client_nonce=client_nonce)
