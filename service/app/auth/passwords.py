# 密码存储原语(P5 鉴权,见 docs/auth.md §密码存储)。每用户随机盐 + N 轮 SM3 迭代拉伸,
# 存 "salt$rounds$digest";校验用 hmac.compare_digest 常量时间比对。纯函数、无 IO/无全局依赖:
# 轮数由调用方(注册/改密)从 gameconfig.PWD_HASH_ROUNDS 传入,校验从存储串读回轮数
# (改配置不废旧哈希)。脱敏红线(log.md):明文密码/盐/摘要都不进日志。

import hmac
import secrets

from ttxsgm import sm3_hash_bytes

_SALT_BYTES = 16  # 每用户盐长度(字节);128-bit,挡彩虹表 + 挡「同密码→同哈希」
_FIELD_SEP = "$"  # 存储串分隔:salt_hex$rounds$digest_hex


def _derive(password: str, salt: bytes, rounds: int) -> bytes:
    # 把 (密码, 盐) 迭代 rounds 轮 SM3 拉伸成 32B 摘要。首个原像 = password||salt(盐进第一轮足矣,
    # 之后 h=SM3(h) 即 SM3^rounds;迭代抬高暴力成本)。rounds<1 会退化成「不哈希」(近乎存明文),
    # 故显式 raise —— 正常路径由 gameconfig ge=1 保证不触发,这里是原语自守的红线。
    if rounds < 1:
        raise ValueError(f"rounds must be >= 1, got {rounds}")
    h = password.encode("utf-8") + salt
    for _ in range(rounds):
        h = sm3_hash_bytes(h)
    return h


def hash_password(password: str, rounds: int) -> str:
    # 生成新盐、迭代哈希,返回可存 DB 的 "salt$rounds$digest"(全 hex)。rounds 由调用方从
    # gameconfig.PWD_HASH_ROUNDS 传(注册/改密);写进串内,供校验按存储轮数重放(改配置不废旧哈希)。
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _derive(password, salt, rounds)
    return _FIELD_SEP.join((salt.hex(), str(rounds), digest.hex()))


def verify_password(password: str, stored: str) -> bool:
    # 常量时间校验:按存储串的盐 + 轮数重算摘要,compare_digest 比对。fail-closed:存储串结构非法
    # (段数≠3 / 盐或摘要非 hex / 轮数非整或 <1)一律 False —— 无法校验绝不放行,且绝不因一行脏 DB
    # 数据崩掉登录路径(「脏数据」与「密码错」都归「不通过」,安全侧的正确取舍)。
    parts = stored.split(_FIELD_SEP)
    if len(parts) != 3:
        return False
    salt_hex, rounds_str, digest_hex = parts
    try:
        salt = bytes.fromhex(salt_hex)
        rounds = int(rounds_str)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    if rounds < 1:
        return False
    actual = _derive(password, salt, rounds)
    return hmac.compare_digest(actual, expected)  # 字节层常量时间;长度不等自然 False,不崩
