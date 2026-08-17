// 验证「上一次会话的残留」能自愈(0078·A)。
//
// 制造残留:alice 进 A 房、入座、然后直接断线(在座断线 → 后端保留座位)。
// 再验自愈:alice 重新登录连上,目标是 B 房。服务器会先发**A 房**的快照,
// 直接 join_room B 会被 ALREADY_IN_ROOM 拒;前端的 decideJoinMessage 应判出
// 「先退再进」,最终落在 B 房。
//
// 跑法:后端在跑 + npm run smoke 已打包过 dist-smoke/crypto.js,然后
//   node scripts/smoke-stale-room.mjs

import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.SMOKE_API_URL || 'http://127.0.0.1:8000'
const K_USER = '00112233445566778899aabbccddeeff'
const PASSWORD = 'devpass123'

const { sm4CbcEncrypt, sm4CbcDecrypt, hexToBytes, bytesToHex, utf8ToBytes, bytesToUtf8,
        deriveWsKeys, sealFrame, openFrame } = await import(`${FRONTEND}/dist-smoke/crypto.js`)

async function login(name) {
  const kUser = hexToBytes(K_USER)
  const iv = crypto.getRandomValues(new Uint8Array(16))
  const payload = { password: PASSWORD, client_nonce: bytesToHex(crypto.getRandomValues(new Uint8Array(16))), ts: Math.floor(Date.now() / 1000) }
  const blob = sm4CbcEncrypt(kUser, iv, utf8ToBytes(JSON.stringify(payload)))
  const res = await fetch(`${BASE}/user/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, iv: bytesToHex(iv), blob: bytesToHex(blob) }),
  })
  if (!res.ok) throw new Error(`login failed: ${res.status}`)
  const body = await res.json()
  return JSON.parse(bytesToUtf8(sm4CbcDecrypt(kUser, hexToBytes(body.iv), hexToBytes(body.blob))))
}

function makeClient(session) {
  const keys = deriveWsKeys(hexToBytes(session.session_token))
  let sendSeq = 0n, seenSeq = 0n
  const events = []
  let ws
  return {
    events,
    connect: () => new Promise((res, rej) => {
      ws = new WebSocket(`${BASE.replace('http', 'ws')}/ws?sid=${encodeURIComponent(session.session_id)}`)
      ws.binaryType = 'arraybuffer'
      ws.onopen = res
      ws.onerror = () => rej(new Error('ws error'))
      ws.onmessage = (ev) => {
        const o = openFrame(keys, new Uint8Array(ev.data))
        if (o.seq <= seenSeq) return
        seenSeq = o.seq
        events.push(JSON.parse(bytesToUtf8(o.plaintext)))
      }
    }),
    send: (m) => { sendSeq += 1n; ws.send(sealFrame(keys, sendSeq, utf8ToBytes(JSON.stringify(m)))) },
    close: () => ws.close(),
  }
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms))
let failures = 0
const check = (c, l) => { console.log(`${c ? '  ✓' : '  ✗'} ${l}`); if (!c) failures++ }

const ROOM_A = `stale-a-${Date.now().toString(36)}`
const ROOM_B = `stale-b-${Date.now().toString(36)}`

console.log('① 制造残留:alice 进 A 房、入座、直接断线')
{
  const s = await login('alice')
  const c = makeClient(s)
  await c.connect()
  c.send({ type: 'join_room', room: ROOM_A })
  await wait(300)
  const snap = c.events.find((m) => m.type === 'state_snapshot')
  if (snap && snap.room !== ROOM_A) { c.send({ type: 'leave_room' }); await wait(200); c.send({ type: 'join_room', room: ROOM_A }); await wait(300) }
  c.send({ type: 'sit_down', seat: 0, wait_for_big_blind: false })
  await wait(300)
  const seated = c.events.filter((m) => m.type === 'state_snapshot').pop()
  check(!!seated, `alice 已在 ${ROOM_A} 入座`)
  c.close() // 在座断线:后端按不变量 9 保留座位
  await wait(400)
}

console.log('② 重新登录,目标是 B 房 —— 用前端的判定逻辑处理服务器发来的消息')
{
  const s = await login('alice')
  const c = makeClient(s)
  await c.connect()

  // 这段就是 store/actions.ts 里 enterRoom 的逻辑(decideJoinMessage + 先退再进)
  let recovered = false
  let landed = null
  const handled = new Set()
  const pump = () => {
    for (const m of c.events) {
      if (handled.has(m)) continue
      handled.add(m)
      const stale = m.type === 'state_snapshot' && m.room !== ROOM_B
      const already = m.type === 'error' && m.code === 'ALREADY_IN_ROOM'
      if (stale || already) {
        if (recovered) continue
        recovered = true
        console.log(`     检测到残留(${stale ? '旧房快照 ' + m.room : 'ALREADY_IN_ROOM'})→ 先退再进`)
        c.send({ type: 'leave_room' })
        c.send({ type: 'join_room', room: ROOM_B })
        continue
      }
      if (m.type === 'state_snapshot' && m.room === ROOM_B) landed = m
    }
  }

  c.send({ type: 'join_room', room: ROOM_B })
  for (let i = 0; i < 30 && !landed; i++) { await wait(150); pump() }

  check(recovered, '识别出上次会话的残留并触发恢复')
  check(!!landed, landed ? `最终落在目标房 ${landed.room}` : '未能进入目标房')
  if (landed) c.send({ type: 'leave_room' })
  await wait(300)
  c.close()
}

console.log('')
console.log(failures === 0 ? '残留自愈验证通过' : `失败:${failures} 项`)
process.exit(failures === 0 ? 0 : 1)
