# 逐会话安全信道原语(P5 鉴权,见 docs/auth.md §加密信道 / changes/0057 设计 / 0058 落地)。
# 登录后 ws 与 REST 一切流量走此信封:出站 iv‖ct‖mac(seq 藏 ct 内首 8B);入站铁序「验 MAC → 解密 → 验 seq」。
# selector(session_id)由传输层剥离、据此查会话取本信道,不进本原语(错 selector → 错密钥 → MAC 必败,
# selector 完整性隐式受保,故 mac 只盖 iv‖ct、本原语传输无关)。密钥由会话密钥直接派生(不再逐连接 server_nonce);
# seq 按会话计。纯计算 + per-session 状态,无 IO,绝不进 world。脱敏红线(log.md):明文/密钥/密文/session_token 不进日志。

import hmac
import secrets

from ttxsgm import KDF_sm3, sm3_hash_bytes, sm4_cbc_dec, sm4_cbc_enc

_ENC_KEY_BYTES = 16  # SM4 密钥长度(128-bit)
_MAC_KEY_BYTES = 32  # HMAC-SM3 密钥长度
_IV_BYTES = 16  # SM4-CBC IV 长度(每帧新鲜随机)
_SEQ_BYTES = 8  # 序号宽度(大端无符号 64-bit);藏 ct 内首 8B,保密 + 被 MAC 罩住
_MAC_BYTES = 32  # HMAC-SM3 输出长度
_SM4_BLOCK_BYTES = 16  # SM4 分组长度;ct 段必为其整数倍
_HMAC_BLOCK_BYTES = 64  # SM3 压缩分组长度(HMAC ipad/opad 按此补齐)
# 最小帧 = iv + 至少一个密文分组(装 seq(8B)+空明文,PKCS#7 补到一整块) + mac
_FRAME_MIN_BYTES = _IV_BYTES + _SM4_BLOCK_BYTES + _MAC_BYTES
_KDF_INFO_ENC = b"\x01"  # 派生 enc_key 的域分隔字节
_KDF_INFO_MAC = b"\x02"  # 派生 mac_key 的域分隔字节


def hmac_sm3(key: bytes, msg: bytes) -> bytes:
    # 标准 HMAC,底层 SM3(避开裸 sm3 的长度扩展)。块 64B:超长 key 先 SM3 收缩,再补 0 到块长。
    # 两输入 = key(证明持钥 = 认证)+ msg(防篡改);裸 sm3 无 key 谁都能算,故用 HMAC。
    if len(key) > _HMAC_BLOCK_BYTES:
        key = sm3_hash_bytes(key)
    key = key.ljust(_HMAC_BLOCK_BYTES, b"\x00")
    ipad = bytes(b ^ 0x36 for b in key)
    opad = bytes(b ^ 0x5C for b in key)
    return sm3_hash_bytes(opad + sm3_hash_bytes(ipad + msg))


def derive_keys(session_token: bytes) -> tuple[bytes, bytes]:
    # 由会话密钥直接派生本会话 (enc_key, mac_key)。info 0x01/0x02 域分隔使两钥输入不同、互不可导。
    # 不再逐连接派 server_nonce —— REST 无连接上下文须「查会话即解」;跨重连重放由按会话计的 seq 挡(changes/0057)。
    enc_key = KDF_sm3(session_token + _KDF_INFO_ENC, _ENC_KEY_BYTES)
    mac_key = KDF_sm3(session_token + _KDF_INFO_MAC, _MAC_KEY_BYTES)
    return enc_key, mac_key


class FrameError(Exception):
    # 逐帧校验失败(结构/MAC/解密/序号)。传输层捕获 → 丢帧 +(ws)关连接 /(REST)拒(auth.md)。
    # reason 是分类标签(供日志定位),绝不含明文/密钥/密文内容(脱敏红线)。
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SecureChannel:
    # per-session 安全信道:本会话派生密钥 + 双向序号计数器(shell 状态,绝不进 world)。
    def __init__(self, enc_key: bytes, mac_key: bytes, max_frame_bytes: int):
        self._enc_key = enc_key  # 16B SM4 密钥
        self._mac_key = mac_key  # 32B HMAC-SM3 密钥
        self._max_frame_bytes = max_frame_bytes  # 单帧字节上限(gameconfig.WS_FRAME_MAX_BYTES),防超大帧
        self._out_seq = 0  # 出站已发最大序号;seal 先自增 ⇒ 首帧 seq=1
        self._in_seq = 0  # 入站已见最大序号;open 收 seq>此(严格递增,防重放)

    @classmethod
    def derive(cls, session_token: bytes, max_frame_bytes: int) -> "SecureChannel":
        # 由会话密钥起本会话信道(密钥派生见 derive_keys;selector 查会话由传输层做,不在此)。
        enc_key, mac_key = derive_keys(session_token)
        return cls(enc_key, mac_key, max_frame_bytes)

    def seal(self, plaintext: bytes) -> bytes:
        # 出站封帧:iv(新鲜随机) ‖ ct ‖ mac。seq 藏 ct 内首 8B;encrypt-then-MAC,mac 盖 iv‖ct。
        self._out_seq += 1
        inner = self._out_seq.to_bytes(_SEQ_BYTES, "big") + plaintext
        iv = secrets.token_bytes(_IV_BYTES)
        ct = sm4_cbc_enc(self._enc_key, iv, inner)
        mac = hmac_sm3(self._mac_key, iv + ct)
        return iv + ct + mac

    def open(self, frame: bytes) -> bytes:
        # 入站拆帧,铁序(changes/0057):① 结构长度 → ② 验 MAC(常量时间)→ ③ 才解密 → ④ 验 seq(藏密文内)。
        # 绝不先解密后验(库去填充是裸的,解未验数据有 padding-oracle 风险),故 MAC 必在解密前。
        # 任一步失败 raise FrameError;_in_seq 仅全通过后推进(失败不动计数)。
        if len(frame) > self._max_frame_bytes:
            raise FrameError("frame_too_large")
        if len(frame) < _FRAME_MIN_BYTES:
            raise FrameError("frame_too_short")
        ct_len = len(frame) - _IV_BYTES - _MAC_BYTES
        if ct_len <= 0 or ct_len % _SM4_BLOCK_BYTES != 0:
            raise FrameError("bad_ct_length")
        iv = frame[:_IV_BYTES]
        ct = frame[_IV_BYTES : _IV_BYTES + ct_len]
        mac = frame[_IV_BYTES + ct_len :]
        expected_mac = hmac_sm3(self._mac_key, iv + ct)
        if not hmac.compare_digest(mac, expected_mac):  # ② 防篡改:常量时间比对
            raise FrameError("bad_mac")
        try:  # ③ 验过才解密
            inner = sm4_cbc_dec(self._enc_key, iv, ct)
        except Exception as exc:  # 防御归一:MAC 过 ⇒ ct 真实 ⇒ 正常不可达,仅防库对边缘输入抛而崩
            raise FrameError("decrypt_failed") from exc
        if len(inner) < _SEQ_BYTES:  # 认证过的帧本应含 8B seq;防御(正常不触发)
            raise FrameError("bad_plaintext")
        seq = int.from_bytes(inner[:_SEQ_BYTES], "big")
        if seq <= self._in_seq:  # ④ 防重放:严格大于已见序号
            raise FrameError("stale_seq")
        self._in_seq = seq  # 全部通过才推进入站序号
        return inner[_SEQ_BYTES:]
