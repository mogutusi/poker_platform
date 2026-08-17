// SM3 杂凑(GB/T 32905)。与后端 lib/ttxsgm 的 sm3_hash_bytes 逐字节一致,验收标准是
// crypto-test-vectors.json 的 sm3 组(见 docs/crypto.md)。纯计算,无 IO。

const IV = Uint32Array.from([
  0x7380166f, 0x4914b2b9, 0x172442d7, 0xda8a0600, 0xa96f30bc, 0x163138aa, 0xe38dee4d, 0xb0fb0e4e,
])

/** 32 位循环左移。JS 位运算是有符号 32 位,必须 >>> 0 收回无符号。 */
function rotl(x: number, n: number): number {
  const s = n & 31
  return ((x << s) | (x >>> (32 - s))) >>> 0
}

/** 置换 P0,压缩函数末尾用。 */
function p0(x: number): number {
  return (x ^ rotl(x, 9) ^ rotl(x, 17)) >>> 0
}

/** 置换 P1,消息扩展用。 */
function p1(x: number): number {
  return (x ^ rotl(x, 15) ^ rotl(x, 23)) >>> 0
}

/** 压缩函数 CF:用一个 64 字节分组更新链接变量 v(原地)。 */
function compress(v: Uint32Array, block: DataView, offset: number): void {
  const w = new Uint32Array(68)
  const w1 = new Uint32Array(64)

  for (let i = 0; i < 16; i++) w[i] = block.getUint32(offset + i * 4, false)
  for (let j = 16; j < 68; j++) {
    const t = (w[j - 16] ^ w[j - 9] ^ rotl(w[j - 3], 15)) >>> 0
    w[j] = (p1(t) ^ rotl(w[j - 13], 7) ^ w[j - 6]) >>> 0
  }
  for (let j = 0; j < 64; j++) w1[j] = (w[j] ^ w[j + 4]) >>> 0

  let a = v[0], b = v[1], c = v[2], d = v[3]
  let e = v[4], f = v[5], g = v[6], h = v[7]

  for (let j = 0; j < 64; j++) {
    // T_j 前 16 轮为 0x79cc4519,其后为 0x7a879d8a;轮内取 ROTL(T_j, j mod 32)。
    const tj = j < 16 ? 0x79cc4519 : 0x7a879d8a
    const a12 = rotl(a, 12)
    const ss1 = rotl((a12 + e + rotl(tj, j)) >>> 0, 7)
    const ss2 = (ss1 ^ a12) >>> 0
    // 布尔函数 FF/GG 前 16 轮是三重异或,其后换成择多 / 择一。
    const ff = j < 16 ? (a ^ b ^ c) >>> 0 : ((a & b) | (a & c) | (b & c)) >>> 0
    const gg = j < 16 ? (e ^ f ^ g) >>> 0 : ((e & f) | (~e & g)) >>> 0
    const tt1 = (ff + d + ss2 + w1[j]) >>> 0
    const tt2 = (gg + h + ss1 + w[j]) >>> 0
    d = c
    c = rotl(b, 9)
    b = a
    a = tt1
    h = g
    g = rotl(f, 19)
    f = e
    e = p0(tt2)
  }

  v[0] = (v[0] ^ a) >>> 0
  v[1] = (v[1] ^ b) >>> 0
  v[2] = (v[2] ^ c) >>> 0
  v[3] = (v[3] ^ d) >>> 0
  v[4] = (v[4] ^ e) >>> 0
  v[5] = (v[5] ^ f) >>> 0
  v[6] = (v[6] ^ g) >>> 0
  v[7] = (v[7] ^ h) >>> 0
}

/** SM3 杂凑,返回 32 字节摘要。 */
export function sm3(msg: Uint8Array): Uint8Array {
  // 填充:补 0x80,补零到长度 ≡ 56 (mod 64),末尾放 64 位大端比特长度。
  const bitLen = BigInt(msg.length) * 8n
  const padLen = msg.length % 64 < 56 ? 56 - (msg.length % 64) : 120 - (msg.length % 64)
  const total = msg.length + padLen + 8
  const buf = new Uint8Array(total)
  buf.set(msg, 0)
  buf[msg.length] = 0x80
  new DataView(buf.buffer).setBigUint64(total - 8, bitLen, false)

  const v = Uint32Array.from(IV)
  const view = new DataView(buf.buffer)
  for (let off = 0; off < total; off += 64) compress(v, view, off)

  const out = new Uint8Array(32)
  const outView = new DataView(out.buffer)
  for (let i = 0; i < 8; i++) outView.setUint32(i * 4, v[i], false)
  return out
}
