// 国密信道原语的对外出口:HMAC-SM3、KDF、字节工具。
// 精确定义与验收方式见 docs/crypto.md;所有函数纯计算,不碰网络、不打日志。

import { sm3 } from './sm3'
import { sm4CbcDecrypt, sm4CbcEncrypt } from './sm4'

export { sm3 } from './sm3'
export { sm4CbcEncrypt, sm4CbcDecrypt } from './sm4'

const HMAC_BLOCK = 64

/**
 * 标准 HMAC 构造,底层用 SM3(裸 SM3 无密钥,谁都能算,且有长度扩展问题)。
 * 超长 key 先 SM3 收缩,短 key 右侧补零到块长。
 */
export function hmacSm3(key: Uint8Array, msg: Uint8Array): Uint8Array {
  let k = key
  if (k.length > HMAC_BLOCK) k = sm3(k)

  const padded = new Uint8Array(HMAC_BLOCK)
  padded.set(k, 0)

  const ipad = new Uint8Array(HMAC_BLOCK + msg.length)
  const opad = new Uint8Array(HMAC_BLOCK + 32)
  for (let i = 0; i < HMAC_BLOCK; i++) {
    ipad[i] = padded[i] ^ 0x36
    opad[i] = padded[i] ^ 0x5c
  }
  ipad.set(msg, HMAC_BLOCK)
  opad.set(sm3(ipad), HMAC_BLOCK)
  return sm3(opad)
}

/** KDF_sm3(input, n) = SM3(input) 的前 n 字节。上限 32,不是 HKDF。 */
export function kdfSm3(input: Uint8Array, length: number): Uint8Array {
  if (length > 32) throw new Error('kdfSm3 length must be <= 32')
  return sm3(input).subarray(0, length)
}

/** 常量时间比较:逐字节异或累加后判零,不提前短路。 */
export function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false
  let diff = 0
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i]
  return diff === 0
}

export function concatBytes(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0)
  const out = new Uint8Array(total)
  let off = 0
  for (const p of parts) {
    out.set(p, off)
    off += p.length
  }
  return out
}

export function hexToBytes(hex: string): Uint8Array {
  if (hex.length % 2 !== 0) throw new Error('hex string must have even length')
  const out = new Uint8Array(hex.length / 2)
  for (let i = 0; i < out.length; i++) {
    const byte = Number.parseInt(hex.slice(i * 2, i * 2 + 2), 16)
    if (Number.isNaN(byte)) throw new Error('invalid hex string')
    out[i] = byte
  }
  return out
}

export function bytesToHex(bytes: Uint8Array): string {
  let out = ''
  for (const b of bytes) out += b.toString(16).padStart(2, '0')
  return out
}

export function utf8ToBytes(text: string): Uint8Array {
  return new TextEncoder().encode(text)
}

export function bytesToUtf8(bytes: Uint8Array): string {
  return new TextDecoder().decode(bytes)
}

/** seq 是 8 字节大端无符号整数。必须用 bigint:超过 2^53 用 number 会失精度。 */
export function seqToBytes(seq: bigint): Uint8Array {
  const out = new Uint8Array(8)
  new DataView(out.buffer).setBigUint64(0, seq, false)
  return out
}

export function bytesToSeq(bytes: Uint8Array): bigint {
  return new DataView(bytes.buffer, bytes.byteOffset, 8).getBigUint64(0, false)
}

/** 每帧新鲜随机的 16 字节 IV,绝不复用。 */
export function randomIv(): Uint8Array {
  const iv = new Uint8Array(16)
  crypto.getRandomValues(iv)
  return iv
}

/** ws/REST 信封的密钥对。ws 用 info 0x01/0x02,REST 用 0x03/0x04,分域杀跨信道重放。 */
export interface ChannelKeys {
  encKey: Uint8Array
  macKey: Uint8Array
}

function deriveWith(sessionToken: Uint8Array, encInfo: number, macInfo: number): ChannelKeys {
  return {
    encKey: kdfSm3(concatBytes(sessionToken, Uint8Array.of(encInfo)), 16),
    macKey: kdfSm3(concatBytes(sessionToken, Uint8Array.of(macInfo)), 32),
  }
}

export function deriveWsKeys(sessionToken: Uint8Array): ChannelKeys {
  return deriveWith(sessionToken, 0x01, 0x02)
}

export function deriveRestKeys(sessionToken: Uint8Array): ChannelKeys {
  return deriveWith(sessionToken, 0x03, 0x04)
}

/**
 * 封一个信封:iv ‖ ct ‖ mac,其中 ct = SM4(encKey, iv, seq(8B 大端) ‖ 明文)。
 * seq 藏在密文里,既保密又被 MAC 罩住;mac 只盖 iv‖ct,不盖 selector。
 */
export function sealFrame(keys: ChannelKeys, seq: bigint, plaintext: Uint8Array, iv: Uint8Array = randomIv()): Uint8Array {
  const ct = sm4CbcEncrypt(keys.encKey, iv, concatBytes(seqToBytes(seq), plaintext))
  const mac = hmacSm3(keys.macKey, concatBytes(iv, ct))
  return concatBytes(iv, ct, mac)
}

export interface OpenedFrame {
  seq: bigint
  plaintext: Uint8Array
}

/**
 * 拆一个信封,按「入站铁序」:验 MAC → 解密 → 取 seq。
 * MAC 必须在解密之前验——去填充没有防护,对未验证的密文解密有 padding-oracle 风险。
 * seq 的新鲜性由调用方按信道规则判(ws 严格单调,REST 滑动窗)。
 */
export function openFrame(keys: ChannelKeys, frame: Uint8Array): OpenedFrame {
  if (frame.length < 16 + 16 + 32) throw new Error('frame too short')
  const iv = frame.subarray(0, 16)
  const ct = frame.subarray(16, frame.length - 32)
  const mac = frame.subarray(frame.length - 32)
  if (ct.length === 0 || ct.length % 16 !== 0) throw new Error('bad ciphertext length')

  const expected = hmacSm3(keys.macKey, concatBytes(iv, ct))
  if (!bytesEqual(expected, mac)) throw new Error('mac mismatch')

  const decoded = sm4CbcDecrypt(keys.encKey, iv, ct)
  if (decoded.length < 8) throw new Error('frame missing seq')
  return { seq: bytesToSeq(decoded.subarray(0, 8)), plaintext: decoded.subarray(8) }
}
