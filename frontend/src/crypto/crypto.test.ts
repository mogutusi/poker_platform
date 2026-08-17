// 国密原语的已知答案测试。向量由后端 service/scripts/gen_crypto_vectors.py 生成,是本层的验收标准:
// 差一个字节,表现就是服务器直接关连接且没有任何有用报错,所以必须在这里钉死(见 docs/crypto.md)。

import { describe, expect, it } from 'vitest'
import vectors from '../../crypto-test-vectors.json'
import {
  bytesToHex,
  bytesToUtf8,
  deriveRestKeys,
  deriveWsKeys,
  hexToBytes,
  hmacSm3,
  kdfSm3,
  openFrame,
  sealFrame,
  sm3,
  sm4CbcDecrypt,
  sm4CbcEncrypt,
  utf8ToBytes,
} from './index'

describe('sm3', () => {
  // 用例分两种输入形式:文本用 input_utf8,分组边界(55/64 字节)那两条用 input_hex。
  for (const [i, c] of vectors.sm3.cases.entries()) {
    const raw = c as { input_utf8?: string; input_hex?: string; hash_hex: string }
    const input = raw.input_hex !== undefined ? hexToBytes(raw.input_hex) : utf8ToBytes(raw.input_utf8 ?? '')
    it(`case ${i}(${input.length} 字节)`, () => {
      expect(bytesToHex(sm3(input))).toBe(raw.hash_hex)
    })
  }
})

describe('sm4-cbc', () => {
  const key = hexToBytes(vectors.sm4_cbc.key_hex)
  const iv = hexToBytes(vectors.sm4_cbc.iv_hex)

  for (const c of vectors.sm4_cbc.cases) {
    it(`encrypts ${c.plaintext_hex.length / 2} bytes`, () => {
      expect(bytesToHex(sm4CbcEncrypt(key, iv, hexToBytes(c.plaintext_hex)))).toBe(c.ciphertext_hex)
    })

    it(`decrypts back ${c.plaintext_hex.length / 2} bytes`, () => {
      expect(bytesToHex(sm4CbcDecrypt(key, iv, hexToBytes(c.ciphertext_hex)))).toBe(c.plaintext_hex)
    })
  }
})

describe('hmac-sm3', () => {
  for (const [i, c] of vectors.hmac_sm3.cases.entries()) {
    it(`case ${i}`, () => {
      expect(bytesToHex(hmacSm3(hexToBytes(c.key_hex), hexToBytes(c.msg_hex)))).toBe(c.mac_hex)
    })
  }
})

describe('kdf 与密钥分域', () => {
  const token = hexToBytes(vectors.kdf.session_token_hex)

  it('KDF_sm3 就是 SM3 的前 n 字节', () => {
    expect(bytesToHex(kdfSm3(token, 16))).toBe(bytesToHex(sm3(token).subarray(0, 16)))
  })

  it('ws 密钥对', () => {
    const { encKey, macKey } = deriveWsKeys(token)
    expect(bytesToHex(encKey)).toBe(vectors.kdf.ws_enc_key_hex)
    expect(bytesToHex(macKey)).toBe(vectors.kdf.ws_mac_key_hex)
  })

  it('REST 密钥对与 ws 分域', () => {
    const { encKey, macKey } = deriveRestKeys(token)
    expect(bytesToHex(encKey)).toBe(vectors.kdf.rest_enc_key_hex)
    expect(bytesToHex(macKey)).toBe(vectors.kdf.rest_mac_key_hex)
    expect(bytesToHex(encKey)).not.toBe(vectors.kdf.ws_enc_key_hex)
  })
})

describe('ws 信封', () => {
  const keys = deriveWsKeys(hexToBytes(vectors.ws_frame.session_token_hex))
  const c = vectors.ws_frame.case

  it('封出的帧与后端逐字节一致', () => {
    const frame = sealFrame(keys, BigInt(c.seq), utf8ToBytes(c.plaintext_utf8), hexToBytes(c.iv_hex))
    expect(bytesToHex(frame)).toBe(c.frame_hex)
  })

  it('拆帧还原 seq 与明文', () => {
    const opened = openFrame(keys, hexToBytes(c.frame_hex))
    expect(opened.seq).toBe(BigInt(c.seq))
    expect(bytesToUtf8(opened.plaintext)).toBe(c.plaintext_utf8)
  })

  it('篡改任意一字节都会 MAC 失败', () => {
    const frame = hexToBytes(c.frame_hex)
    frame[20] ^= 0x01
    expect(() => openFrame(keys, frame)).toThrow(/mac/i)
  })

  it('用 REST 密钥拆 ws 帧必败(跨信道重放)', () => {
    const restKeys = deriveRestKeys(hexToBytes(vectors.ws_frame.session_token_hex))
    expect(() => openFrame(restKeys, hexToBytes(c.frame_hex))).toThrow(/mac/i)
  })
})

describe('REST 信封', () => {
  const keys = deriveRestKeys(hexToBytes(vectors.rest_envelope.session_token_hex))
  const c = vectors.rest_envelope.case

  it('封出的帧与后端逐字节一致', () => {
    const frame = sealFrame(keys, BigInt(c.seq), utf8ToBytes(c.plaintext_utf8), hexToBytes(c.iv_hex))
    expect(bytesToHex(frame)).toBe(c.frame_hex)
  })
})

describe('登录 blob', () => {
  const kUser = hexToBytes(vectors.login_blob.k_user_hex)
  const iv = hexToBytes(vectors.login_blob.iv_hex)

  it('请求方向:加密出的 blob 与后端一致', () => {
    const blob = sm4CbcEncrypt(kUser, iv, utf8ToBytes(vectors.login_blob.request.plaintext_utf8))
    expect(bytesToHex(blob)).toBe(vectors.login_blob.request.blob_hex)
  })

  it('响应方向:能解出后端下发的 blob', () => {
    const plain = sm4CbcDecrypt(kUser, iv, hexToBytes(vectors.login_blob.response.blob_hex))
    expect(bytesToUtf8(plain)).toBe(vectors.login_blob.response.plaintext_utf8)
  })
})
