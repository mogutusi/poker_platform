// SM4 分组密码(GB/T 32907)+ CBC 模式 + PKCS#7 填充。与后端 lib/ttxsgm 的 sm4_cbc_enc/dec
// 逐字节一致,验收标准是 crypto-test-vectors.json 的 sm4_cbc 组(见 docs/crypto.md)。纯计算,无 IO。

/** S 盒,取自后端参考实现并校验为 0-255 的置换。 */
const SBOX = Uint8Array.from([
  0xd6, 0x90, 0xe9, 0xfe, 0xcc, 0xe1, 0x3d, 0xb7, 0x16, 0xb6, 0x14, 0xc2, 0x28, 0xfb, 0x2c, 0x05,
  0x2b, 0x67, 0x9a, 0x76, 0x2a, 0xbe, 0x04, 0xc3, 0xaa, 0x44, 0x13, 0x26, 0x49, 0x86, 0x06, 0x99,
  0x9c, 0x42, 0x50, 0xf4, 0x91, 0xef, 0x98, 0x7a, 0x33, 0x54, 0x0b, 0x43, 0xed, 0xcf, 0xac, 0x62,
  0xe4, 0xb3, 0x1c, 0xa9, 0xc9, 0x08, 0xe8, 0x95, 0x80, 0xdf, 0x94, 0xfa, 0x75, 0x8f, 0x3f, 0xa6,
  0x47, 0x07, 0xa7, 0xfc, 0xf3, 0x73, 0x17, 0xba, 0x83, 0x59, 0x3c, 0x19, 0xe6, 0x85, 0x4f, 0xa8,
  0x68, 0x6b, 0x81, 0xb2, 0x71, 0x64, 0xda, 0x8b, 0xf8, 0xeb, 0x0f, 0x4b, 0x70, 0x56, 0x9d, 0x35,
  0x1e, 0x24, 0x0e, 0x5e, 0x63, 0x58, 0xd1, 0xa2, 0x25, 0x22, 0x7c, 0x3b, 0x01, 0x21, 0x78, 0x87,
  0xd4, 0x00, 0x46, 0x57, 0x9f, 0xd3, 0x27, 0x52, 0x4c, 0x36, 0x02, 0xe7, 0xa0, 0xc4, 0xc8, 0x9e,
  0xea, 0xbf, 0x8a, 0xd2, 0x40, 0xc7, 0x38, 0xb5, 0xa3, 0xf7, 0xf2, 0xce, 0xf9, 0x61, 0x15, 0xa1,
  0xe0, 0xae, 0x5d, 0xa4, 0x9b, 0x34, 0x1a, 0x55, 0xad, 0x93, 0x32, 0x30, 0xf5, 0x8c, 0xb1, 0xe3,
  0x1d, 0xf6, 0xe2, 0x2e, 0x82, 0x66, 0xca, 0x60, 0xc0, 0x29, 0x23, 0xab, 0x0d, 0x53, 0x4e, 0x6f,
  0xd5, 0xdb, 0x37, 0x45, 0xde, 0xfd, 0x8e, 0x2f, 0x03, 0xff, 0x6a, 0x72, 0x6d, 0x6c, 0x5b, 0x51,
  0x8d, 0x1b, 0xaf, 0x92, 0xbb, 0xdd, 0xbc, 0x7f, 0x11, 0xd9, 0x5c, 0x41, 0x1f, 0x10, 0x5a, 0xd8,
  0x0a, 0xc1, 0x31, 0x88, 0xa5, 0xcd, 0x7b, 0xbd, 0x2d, 0x74, 0xd0, 0x12, 0xb8, 0xe5, 0xb4, 0xb0,
  0x89, 0x69, 0x97, 0x4a, 0x0c, 0x96, 0x77, 0x7e, 0x65, 0xb9, 0xf1, 0x09, 0xc5, 0x6e, 0xc6, 0x84,
  0x18, 0xf0, 0x7d, 0xec, 0x3a, 0xdc, 0x4d, 0x20, 0x79, 0xee, 0x5f, 0x3e, 0xd7, 0xcb, 0x39, 0x48
])

/** 系统参数 FK,用于密钥扩展的初始异或。 */
const FK = Uint32Array.from([0xa3b1bac6, 0x56aa3350, 0x677d9197, 0xb27022dc])

/** 固定参数 CK,32 轮各一个。 */
const CK = Uint32Array.from([
  0x00070e15, 0x1c232a31, 0x383f464d, 0x545b6269, 0x70777e85, 0x8c939aa1, 0xa8afb6bd, 0xc4cbd2d9,
  0xe0e7eef5, 0xfc030a11, 0x181f262d, 0x343b4249, 0x50575e65, 0x6c737a81, 0x888f969d, 0xa4abb2b9,
  0xc0c7ced5, 0xdce3eaf1, 0xf8ff060d, 0x141b2229, 0x30373e45, 0x4c535a61, 0x686f767d, 0x848b9299,
  0xa0a7aeb5, 0xbcc3cad1, 0xd8dfe6ed, 0xf4fb0209, 0x10171e25, 0x2c333a41, 0x484f565d, 0x646b7279,
])

const BLOCK = 16

function rotl(x: number, n: number): number {
  return ((x << n) | (x >>> (32 - n))) >>> 0
}

/** 非线性变换 τ:把 32 位字拆 4 字节分别过 S 盒。 */
function tau(x: number): number {
  return (
    ((SBOX[(x >>> 24) & 0xff] << 24) |
      (SBOX[(x >>> 16) & 0xff] << 16) |
      (SBOX[(x >>> 8) & 0xff] << 8) |
      SBOX[x & 0xff]) >>>
    0
  )
}

/** 轮函数的线性变换 L。 */
function lRound(b: number): number {
  return (b ^ rotl(b, 2) ^ rotl(b, 10) ^ rotl(b, 18) ^ rotl(b, 24)) >>> 0
}

/** 密钥扩展的线性变换 L'(与轮函数的 L 不同)。 */
function lKey(b: number): number {
  return (b ^ rotl(b, 13) ^ rotl(b, 23)) >>> 0
}

/** 由 16 字节密钥扩展出 32 个轮密钥。 */
function expandKey(key: Uint8Array): Uint32Array {
  if (key.length !== 16) throw new Error('SM4 key must be 16 bytes')
  const view = new DataView(key.buffer, key.byteOffset, key.byteLength)
  const k = new Uint32Array(4)
  for (let i = 0; i < 4; i++) k[i] = (view.getUint32(i * 4, false) ^ FK[i]) >>> 0

  const rk = new Uint32Array(32)
  for (let i = 0; i < 32; i++) {
    const t = lKey(tau((k[1] ^ k[2] ^ k[3] ^ CK[i]) >>> 0))
    const next = (k[0] ^ t) >>> 0
    k[0] = k[1]
    k[1] = k[2]
    k[2] = k[3]
    k[3] = next
    rk[i] = next
  }
  return rk
}

/** 单分组加解密:解密就是轮密钥倒序用,故共用一套轮结构。 */
function cryptBlock(rk: Uint32Array, input: Uint8Array, inOff: number, out: Uint8Array, outOff: number, reverse: boolean): void {
  const iv = new DataView(input.buffer, input.byteOffset, input.byteLength)
  let x0 = iv.getUint32(inOff, false)
  let x1 = iv.getUint32(inOff + 4, false)
  let x2 = iv.getUint32(inOff + 8, false)
  let x3 = iv.getUint32(inOff + 12, false)

  for (let i = 0; i < 32; i++) {
    const k = rk[reverse ? 31 - i : i]
    const t = (x0 ^ lRound(tau((x1 ^ x2 ^ x3 ^ k) >>> 0))) >>> 0
    x0 = x1
    x1 = x2
    x2 = x3
    x3 = t
  }

  // 反序变换 R:输出是最后四个状态字的倒序。
  const ov = new DataView(out.buffer, out.byteOffset, out.byteLength)
  ov.setUint32(outOff, x3, false)
  ov.setUint32(outOff + 4, x2, false)
  ov.setUint32(outOff + 8, x1, false)
  ov.setUint32(outOff + 12, x0, false)
}

/**
 * SM4-CBC 加密,PKCS#7 填充。
 * 注意:明文长度恰为 16 的整数倍时(含空串)仍要补满一整块 0x10,密文因此总比明文长。
 */
export function sm4CbcEncrypt(key: Uint8Array, iv: Uint8Array, data: Uint8Array): Uint8Array {
  if (iv.length !== BLOCK) throw new Error('SM4 iv must be 16 bytes')
  const rk = expandKey(key)
  const padValue = BLOCK - (data.length % BLOCK)
  const padded = new Uint8Array(data.length + padValue)
  padded.set(data, 0)
  padded.fill(padValue, data.length)

  const out = new Uint8Array(padded.length)
  const prev = new Uint8Array(iv)
  const block = new Uint8Array(BLOCK)
  for (let off = 0; off < padded.length; off += BLOCK) {
    for (let i = 0; i < BLOCK; i++) block[i] = padded[off + i] ^ prev[i]
    cryptBlock(rk, block, 0, out, off, false)
    prev.set(out.subarray(off, off + BLOCK))
  }
  return out
}

/** SM4-CBC 解密并去掉 PKCS#7 填充。填充值非法会抛错,不静默返回半截明文。 */
export function sm4CbcDecrypt(key: Uint8Array, iv: Uint8Array, data: Uint8Array): Uint8Array {
  if (iv.length !== BLOCK) throw new Error('SM4 iv must be 16 bytes')
  if (data.length === 0 || data.length % BLOCK !== 0) throw new Error('SM4 ciphertext must be a non-empty multiple of 16 bytes')
  const rk = expandKey(key)

  const plain = new Uint8Array(data.length)
  const prev = new Uint8Array(iv)
  const decoded = new Uint8Array(BLOCK)
  for (let off = 0; off < data.length; off += BLOCK) {
    cryptBlock(rk, data, off, decoded, 0, true)
    for (let i = 0; i < BLOCK; i++) plain[off + i] = decoded[i] ^ prev[i]
    prev.set(data.subarray(off, off + BLOCK))
  }

  const pad = plain[plain.length - 1]
  if (pad < 1 || pad > BLOCK || pad > plain.length) throw new Error('SM4 bad padding')
  return plain.subarray(0, plain.length - pad)
}
