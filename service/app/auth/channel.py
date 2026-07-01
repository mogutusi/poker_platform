# 逐帧安全信道原语(P5 鉴权,见 docs/auth.md §WS 安全信道)。encrypt-then-MAC 逐帧:
# 出站 seq(8B BE)‖iv(16B 新鲜随机)‖ct‖mac(32B);入站铁序「① 结构 → ② seq>已见 → ③ 验 MAC → ④ 才解密」。
# 纯计算 + per-connection 状态(密钥 + 双向序号),无 IO;绝不进 world(非确定外部态,同 timer.md 墙钟)。
# 脱敏红线(log.md):明文/密钥/密文/session_token 都不进日志,FrameError.reason 只带分类不带内容。

import hmac
import secrets

from ttxsgm import KDF_sm3, sm3_hash_bytes, sm4_cbc_dec, sm4_cbc_enc

_ENC_KEY_BYTES = 16  # SM4 密钥长度(128-bit)
_MAC_KEY_BYTES = 32  # HMAC-SM3 密钥长度
_IV_BYTES = 16  # SM4-CBC IV 长度(每帧新鲜随机)
_SEQ_BYTES = 8  # 帧序号宽度(大端无符号 64-bit)
_MAC_BYTES = 32  # HMAC-SM3 输出长度(SM3 摘要 32B)
_SM4_BLOCK_BYTES = 16  # SM4 分组长度;ct 段必为其整数倍
_HMAC_BLOCK_BYTES = 64  # SM3 压缩分组长度(HMAC ipad/opad 按此补齐)
# 最小帧 = 头(seq+iv)+ 至少一个密文分组 + mac;空明文经 PKCS#7 也占一整块
_FRAME_MIN_BYTES = _SEQ_BYTES + _IV_BYTES + _SM4_BLOCK_BYTES + _MAC_BYTES
_KDF_INFO_ENC = b"\x01"  # 派生 enc_key 的域分隔字节
_KDF_INFO_MAC = b"\x02"  # 派生 mac_key 的域分隔字节


def hmac_sm3(key: bytes, msg: bytes) -> bytes:
    # 标准 HMAC,底层 SM3(避开裸 sm3 的长度扩展)。块 64B:超长 key 先 SM3 收缩,再补 0 到块长。
    if len(key) > _HMAC_BLOCK_BYTES:
        key = sm3_hash_bytes(key)
    key = key.ljust(_HMAC_BLOCK_BYTES, b"\x00")
    ipad = bytes(b ^ 0x36 for b in key)
    opad = bytes(b ^ 0x5C for b in key)
    return sm3_hash_bytes(opad + sm3_hash_bytes(ipad + msg))


def derive_keys(session_token: bytes, server_nonce: bytes) -> tuple[bytes, bytes]:
    # 由会话票据 + 每连接一次性 server_nonce 派生本连接 (enc_key, mac_key)。info 字节 0x01/0x02 域分隔
    # 使两钥输入不同、互不可导。server_nonce 每连接新随机 ⇒ 逐连接密钥不同 ⇒ 旧连接的帧在新连接 MAC 必败
    # (跨重连重放被根除,故序号每连接从头计数也安全)。session_token 永不再上线(只在此本地派生)。
    enc_key = KDF_sm3(session_token + server_nonce + _KDF_INFO_ENC, _ENC_KEY_BYTES)
    mac_key = KDF_sm3(session_token + server_nonce + _KDF_INFO_MAC, _MAC_KEY_BYTES)
    return enc_key, mac_key


class FrameError(Exception):
    # 逐帧校验失败(结构/序号/MAC/解密)。Receiver 捕获 → 丢帧 + 关连接(auth.md)。
    # reason 是分类标签(供日志定位),绝不含明文/密钥/密文内容(脱敏红线)。
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SecureChannel:
    # per-connection 安全信道:本连接派生密钥 + 双向序号计数器(shell 状态,绝不进 world)。
    def __init__(self, enc_key: bytes, mac_key: bytes, max_frame_bytes: int):
        self._enc_key = enc_key  # 16B SM4 密钥
        self._mac_key = mac_key  # 32B HMAC-SM3 密钥
        self._max_frame_bytes = max_frame_bytes  # 单帧字节上限(gameconfig.WS_FRAME_MAX_BYTES),防超大帧
        self._out_seq = 0  # 出站已发最大序号;seal 先自增 ⇒ 首帧 seq=1
        self._in_seq = 0  # 入站已见最大序号;open 收 seq>此(严格递增,防重放)

    @classmethod
    def derive(
        cls, session_token: bytes, server_nonce: bytes, max_frame_bytes: int
    ) -> "SecureChannel":
        # 握手侧:由会话票据 + server_nonce 起本连接信道(密钥派生见 derive_keys)。
        enc_key, mac_key = derive_keys(session_token, server_nonce)
        return cls(enc_key, mac_key, max_frame_bytes)

    def seal(self, plaintext: bytes) -> bytes:
        # 出站封帧:seq‖iv‖ct‖mac。IV 每帧新鲜随机(非计数器);encrypt-then-MAC,mac 盖 seq‖iv‖ct。
        self._out_seq += 1
        seq = self._out_seq.to_bytes(_SEQ_BYTES, "big")
        iv = secrets.token_bytes(_IV_BYTES)
        ct = sm4_cbc_enc(self._enc_key, iv, plaintext)
        mac = hmac_sm3(self._mac_key, seq + iv + ct)
        return seq + iv + ct + mac

    def open(self, frame: bytes) -> bytes:
        # 入站拆帧,铁序(绝不先解密后验;库去填充是裸的,解未验密文有 padding-oracle 风险):
        # ① 结构长度 → ② seq>已见(防重放)→ ③ 重算 MAC 常量时间比(防篡改)→ ④ 才解密。
        # 任一步失败 raise FrameError;_in_seq 仅全通过后推进(失败不动计数)。
        if len(frame) > self._max_frame_bytes:
            raise FrameError("frame_too_large")
        if len(frame) < _FRAME_MIN_BYTES:
            raise FrameError("frame_too_short")
        ct_len = len(frame) - _SEQ_BYTES - _IV_BYTES - _MAC_BYTES
        if ct_len <= 0 or ct_len % _SM4_BLOCK_BYTES != 0:
            raise FrameError("bad_ct_length")
        seq_bytes = frame[:_SEQ_BYTES]
        iv = frame[_SEQ_BYTES : _SEQ_BYTES + _IV_BYTES]
        ct = frame[_SEQ_BYTES + _IV_BYTES : _SEQ_BYTES + _IV_BYTES + ct_len]
        mac = frame[_SEQ_BYTES + _IV_BYTES + ct_len :]
        seq = int.from_bytes(seq_bytes, "big")
        if seq <= self._in_seq:  # ② 防重放:严格大于已见序号
            raise FrameError("stale_seq")
        expected_mac = hmac_sm3(self._mac_key, seq_bytes + iv + ct)
        if not hmac.compare_digest(mac, expected_mac):  # ③ 防篡改:常量时间比对
            raise FrameError("bad_mac")
        try:  # ④ 验过才解密
            plaintext = sm4_cbc_dec(self._enc_key, iv, ct)
        except Exception as exc:  # 防御归一:MAC 已过 ⇒ 正常不可达,仅防库对边缘输入抛而崩 Receiver
            raise FrameError("decrypt_failed") from exc
        self._in_seq = seq  # 全部通过才推进入站序号
        return plaintext
