// 冒烟脚本共用的客户端脚手架:登录握手 + 加密 ws 连接 + 收发帧。
//
// 抽出来的理由很实际:第三个冒烟脚本(0085 的加注/边池)一写,这套 60 行就要有三份副本,
// 而它直接压在协议面上——协议一改(比如 0084 给 user_status_changed 加字段),三份都得跟着改,
// 漏一份就是一个静默失效的冒烟。**用的是前端自己的 `src/crypto` 打包产物**,这正是冒烟的价值:
// 类型检查和构建都发现不了「密钥派生错一个字节」,而它的表现只是服务器默默关连接。
//
// 用的是 dev 种子用户与 dev 共享密钥(见 service/app/poker.env.example),仅限本地。
// 前置:`npm run smoke` 会先把 dist-smoke/crypto.js 打出来;单独跑别的脚本前需先跑过一次。

import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '..')

export const BASE = process.env.SMOKE_API_URL || 'http://127.0.0.1:8000'
const K_USER = '00112233445566778899aabbccddeeff'
const PASSWORD = 'devpass123'

const { sm4CbcEncrypt, sm4CbcDecrypt, hexToBytes, bytesToHex, utf8ToBytes, bytesToUtf8,
        deriveWsKeys, sealFrame, openFrame } = await import(`${FRONTEND}/dist-smoke/crypto.js`)

export { hexToBytes, bytesToHex, utf8ToBytes, bytesToUtf8 }

export async function login(name) {
  const kUser = hexToBytes(K_USER)
  const iv = crypto.getRandomValues(new Uint8Array(16))
  const payload = {
    password: PASSWORD,
    client_nonce: bytesToHex(crypto.getRandomValues(new Uint8Array(16))),
    ts: Math.floor(Date.now() / 1000),
  }
  const blob = sm4CbcEncrypt(kUser, iv, utf8ToBytes(JSON.stringify(payload)))
  const res = await fetch(`${BASE}/user/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, iv: bytesToHex(iv), blob: bytesToHex(blob) }),
  })
  if (!res.ok) throw new Error(`login ${name} failed: ${res.status}`)
  const body = await res.json()
  return JSON.parse(bytesToUtf8(sm4CbcDecrypt(kUser, hexToBytes(body.iv), hexToBytes(body.blob))))
}

export class Client {
  constructor(name, session) {
    this.name = name
    this.session = session
    this.keys = deriveWsKeys(hexToBytes(session.session_token))
    this.sendSeq = 0n // seq 按**会话**计,跨重连继续累加(从 0 重来会被判 stale_seq,见 docs/transport.md)
    this.seenSeq = 0n
    this.events = []
    this.waiters = []
    this.staleFrames = 0 // 收到不新鲜 seq 的次数;冒烟据此断言「全程无重放/乱序」
  }

  connect() {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${BASE.replace('http', 'ws')}/ws?sid=${encodeURIComponent(this.session.session_id)}`)
      ws.binaryType = 'arraybuffer'
      this.ws = ws
      ws.onopen = () => resolve()
      ws.onerror = () => reject(new Error(`ws error for ${this.name}`))
      ws.onclose = (e) => { this.closed = e.code }
      ws.onmessage = (ev) => {
        const opened = openFrame(this.keys, new Uint8Array(ev.data))
        if (opened.seq <= this.seenSeq) { this.staleFrames += 1; return } // 入站铁序:seq 必须严格递增
        this.seenSeq = opened.seq
        const msg = JSON.parse(bytesToUtf8(opened.plaintext))
        this.events.push(msg)
        for (const w of this.waiters.slice()) {
          if (w.pred(msg)) { this.waiters.splice(this.waiters.indexOf(w), 1); w.resolve(msg) }
        }
      }
    })
  }

  send(msg) {
    this.sendSeq += 1n
    this.ws.send(sealFrame(this.keys, this.sendSeq, utf8ToBytes(JSON.stringify(msg))))
  }

  wait(pred, label, ms = 4000) {
    const hit = this.events.find(pred)
    if (hit) return Promise.resolve(hit)
    return new Promise((resolve, reject) => {
      const w = { pred, resolve }
      this.waiters.push(w)
      setTimeout(() => {
        const i = this.waiters.indexOf(w)
        if (i >= 0) { this.waiters.splice(i, 1); reject(new Error(`超时等待 ${label} (${this.name})`)) }
      }, ms)
    })
  }

  /** 最后一条某类型事件;没有则 undefined。 */
  last(type) {
    for (let i = this.events.length - 1; i >= 0; i--) if (this.events[i].type === type) return this.events[i]
    return undefined
  }
}

/** 登录 + 连上,返回可用的 Client。 */
export async function connectAs(name) {
  const client = new Client(name, await login(name))
  await client.connect()
  return client
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 进房,并处理「上一次会话的残留」(0078·A)。
 *
 * 后端按不变量 9 记着「一个用户同时只在一个房间」:上一轮若是**在座断线**,座位会被保留一个占座窗口,
 * 于是这条新连接走的是**重连**路径,服务器先发来**旧房间**的快照,此时直接 join_room 会被 ALREADY_IN_ROOM
 * 拒掉。这不是后端的 bug 而是正确行为;真实前端在 store/joinFlow.ts 里做同样的判定(先退再进,只做一次)。
 */
export async function ensureInRoom(client, room) {
  await sleep(200) // 给可能的重连快照一点时间到达
  if (client.events.some((m) => m.type === 'state_snapshot' && m.room !== room)) {
    client.send({ type: 'leave_room' })
    await sleep(200)
  }
  client.send({ type: 'join_room', room })
  return client.wait((m) => m.type === 'state_snapshot' && m.room === room, `${client.name} 进 ${room}`)
}
