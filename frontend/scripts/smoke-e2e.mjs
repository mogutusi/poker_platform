// 端到端冒烟:用前端自己的 crypto/transport 代码,对真后端跑通
// 登录 → ws 握手 → 进房 → 入座 → 买入 → 准备 → 开局 → 一手牌。
// 这是检验传输层的唯一可靠办法(0077 自 review ⑥ 记的缺口)。

// 跑法(见 docs/dev.md):
//   1) 后端:cd service && .venv/bin/uvicorn app.shell.lifespan:app --host 127.0.0.1
//   2) 打包本前端的 crypto:npx esbuild src/crypto/index.ts --bundle --format=esm \
//        --platform=neutral --outfile=dist-smoke/crypto.js
//   3) node scripts/smoke-e2e.mjs
//
// 用的是 dev 种子用户与 dev 共享密钥(见 service/app/poker.env.example),仅限本地。

import { Client, ensureInRoom, login, restCall } from './smoke-client.mjs'

// **冒烟用专属账号,不与浏览器用例共用**(0086 实测教训):共用时,浏览器那边留在桌上的筹码会在
// 冒烟跑到一半时被占座清理退回全局积分,凭空改变「两人合计」,把守恒断言打红——查半天才发现
// 是别的测试在动同一批账号。dev 种子里 smoke1/2/3 就是为此预留的。
const SMOKE_A = 'smoke1'
const SMOKE_B = 'smoke2'

function log(...a) { console.log(...a) }

// 每轮用独立房名:断线后座位会保留一个占座窗口(~90s),复用房名会撞上一轮的座位。
// 房间是动态创建的,末人离开即销毁,所以不会堆积。
const ROOM = `smoke-${Date.now().toString(36)}`
let failures = 0
function check(cond, label) {
  log(`${cond ? '  ✓' : '  ✗'} ${label}`)
  if (!cond) failures++
}

// 两人积分的基线:守恒断言必须对着**本次跑之前的实际总额**比,不能对着写死的 2000。
// dev 库是长期复用的:服务器带着「有人在座」被杀过一次,桌上的筹码就再也回不到全局积分
// (进程崩溃带走内存状态,见 service/docs/architecture.md「崩溃语义」),写死的初始总额从此永远对不上;
// 而真正的不变量——「这一趟跑下来两人合计不变」——照样成立。
// 排行榜 0094 起走加密信封(没有明文读了),所以基线要**登录之后**才读得到。
async function pairPoints(session) {
  const { entries } = await restCall(session, '/leaderboard', {})
  return [SMOKE_A, SMOKE_B].map((nick) => [nick, entries.find((e) => e.nickname === nick)?.points ?? 0])
}

log('① 登录 alice / bob')
const aliceSession = await login(SMOKE_A)
const bobSession = await login(SMOKE_B)
const alice = new Client(SMOKE_A, aliceSession)
const bob = new Client(SMOKE_B, bobSession)

const baseline = await pairPoints(aliceSession)
const baselineSum = baseline.reduce((n, [, p]) => n + p, 0)
check(!!alice.session.session_id && !!alice.session.session_token, '登录换回 session_id + session_token')

log('② ws 握手')
await alice.connect(); await bob.connect()
check(true, '两条加密连接建立')

log('③ 进房')

/**
 * 进房要先处理「上一次会话的残留」:如果这个账号还挂在别的房间里(上次是在座断线,
 * 后端按不变量 9 保留了座位),新连接触发的是**重连**路径,服务器会先发来旧房间的快照,
 * 此时直接 join_room 会被 ALREADY_IN_ROOM 拒。所以先 leave_room 再进。
 * 前端真实场景同理,不是测试特有的。
 */
const snapA = await ensureInRoom(alice, ROOM)
check(snapA.room === ROOM, `收到 StateSnapshot(房间=${snapA.room}, 座位数=${snapA.max_seats})`)
await ensureInRoom(bob, ROOM)
check(true, 'bob 也进房')

log('④ 入座 + 买入')
alice.send({ type: 'sit_down', seat: 0, wait_for_big_blind: false })
bob.send({ type: 'sit_down', seat: 1, wait_for_big_blind: false })
await alice.wait((m) => m.type === 'user_status_changed' && m.nickname === SMOKE_B, 'bob 入座广播')
alice.send({ type: 'buy_in', seat: 0, amount: 500 })
bob.send({ type: 'buy_in', seat: 1, amount: 500 })
const bought = await alice.wait((m) => m.type === 'player_bought_in' && m.nickname === SMOKE_B, 'bob 买入')
check(bought.seat_points === 500, `买入到账(bob 桌上 ${bought.seat_points})`)

log('⑤ 准备 + 开局')
alice.send({ type: 'set_user_status', status: 'ready_to_play', seat: 0 })
bob.send({ type: 'set_user_status', status: 'ready_to_play', seat: 1 })
await alice.wait((m) => m.type === 'user_status_changed' && m.nickname === SMOKE_B && m.status === 'ready_to_play', 'bob 准备')
alice.send({ type: 'start_hand', seat: 0 })
const started = await alice.wait((m) => m.type === 'hand_started', '开局')
check(started.players.length === 2, `开局(${started.players.length} 人,button=${started.button_position})`)

log('⑥ 底牌隐私')
const holeA = await alice.wait((m) => m.type === 'hole_cards', 'alice 底牌')
check(Array.isArray(holeA.cards) && holeA.cards.length === 2, `alice 收到自己的两张底牌`)
const aliceSawBobHole = alice.events.some((m) => m.type === 'hole_cards' && m !== holeA)
check(!aliceSawBobHole, 'alice 收不到别人的底牌')

log('⑦ 打一手:跟注推进到摊牌(覆盖多街 + 比牌,不走 fold 捷径)')

/**
 * 谁该行动。**acting_position 是 players[] 的下标,不是座位号**
 * (见 service/docs/wire-protocol-guide.md),要经 players 换算成座位。
 * 这一点最初写错过,前端 store 里也错了同一处 —— 靠本冒烟才发现。
 */
let playersOrder = []
function actingSeat() {
  for (const m of [...alice.events].reverse()) {
    if (m.type === 'hand_started') playersOrder = m.players
    break
  }
  const hs = [...alice.events].reverse().find((m) => m.type === 'hand_started')
  if (hs) playersOrder = hs.players
  const last = [...alice.events].reverse().find(
    (m) => (m.type === 'player_acted' || m.type === 'hand_started') && 'acting_position' in m,
  )
  const idx = last ? last.acting_position : null
  if (idx === null || idx === undefined) return null
  return playersOrder[idx]?.seat_position ?? null
}
/** 本街需跟额:PlayerActed 带 last_bet;新街道归零。 */
function currentBet() {
  for (const m of [...alice.events].reverse()) {
    if (m.type === 'hand_status_changed') return 0
    if (m.type === 'player_acted') return m.last_bet
    if (m.type === 'hand_started') return m.big_blind
  }
  return 0
}
/** 某座位本街已投入。 */
function betOf(seat) {
  for (const m of [...alice.events].reverse()) {
    if (m.type === 'hand_status_changed') break
    if (m.type === 'player_acted' && m.seat_position === seat) return m.bet_amount
  }
  const hs = [...alice.events].reverse().find((m) => m.type === 'hand_started')
  return hs?.players.find((p) => p.seat_position === seat)?.bet_amount ?? 0
}

const streets = new Set()
let guard = 0
while (guard++ < 60) {
  if (alice.events.some((m) => m.type === 'hand_ended')) break
  for (const m of alice.events) if (m.type === 'hand_status_changed') streets.add(m.status)
  const seat = actingSeat()
  if (seat === null || seat === undefined) { await new Promise((r) => setTimeout(r, 120)); continue }
  const who = seat === 0 ? alice : bob
  const need = currentBet()
  const mine = betOf(seat)
  // 跟平就 check,否则跟注(bet_amount 是本街目标总额,不是增量)
  who.send(need > mine
    ? { type: 'player_action', action: 'bet', bet_amount: need }
    : { type: 'player_action', action: 'check' })
  await new Promise((r) => setTimeout(r, 180))
}
if (process.env.SMOKE_DEBUG) {
  console.log('  --- 事件流 ---')
  for (const m of alice.events) console.log('   ', JSON.stringify(m).slice(0, 150))
}
const ended = alice.events.find((m) => m.type === 'hand_ended')
const showdown = alice.events.find((m) => m.type === 'hand_show_down')
check(!!ended, ended ? `手牌结束(${ended.winnings.map((w) => `${w.nickname}+${w.amount}`).join(', ')})` : '手牌未能结束')
check(streets.has('flop') && streets.has('turn') && streets.has('river'), `走完三条街道(${[...streets].join(' → ')})`)
check(!!showdown && showdown.reveals.length === 2, showdown ? `摊牌亮 ${showdown.reveals.length} 家底牌、公共牌 ${showdown.board.length} 张` : '未到摊牌')

log('⑧ 房间聊天')
alice.send({ type: 'room_chat', text: 'gg' })
const chat = await bob.wait((m) => m.type === 'chat_message' && m.text === 'gg', '聊天送达')
check(chat.from_nick === SMOKE_A, `bob 收到 alice 的聊天`)

log('⑨ seq 单调性(全程无不新鲜帧)')
check(alice.seenSeq > 0n && bob.seenSeq > 0n, `alice 收 ${alice.seenSeq} 帧, bob 收 ${bob.seenSeq} 帧,均严格递增`)

log('⑩ 离桌退分(让脚本可重复跑)')
alice.send({ type: 'leave_room' })
bob.send({ type: 'leave_room' })
// 断言守恒,而不是某人回到某个定值:赢家会多、输家会少,但两人合计必须不变
// (筹码守恒是后端的核心不变量,见 service/docs/review.md)。
// **必须轮询等**:/leaderboard 读的是 DB,而积分走 delayDB —— 落库最多滞后一个 DB_FLUSH_INTERVAL_MS
// (缺省 500ms),固定睡 400ms 必然偶尔读到退分之前的旧值,那读出来就是「凭空少了一笔买入」的假阳性。
let after = []
let sum = 0
for (let i = 0; i < 40; i++) {
  await new Promise((r) => setTimeout(r, 100))
  after = await pairPoints(aliceSession)
  sum = after.reduce((n, [, p]) => n + p, 0)
  if (sum === baselineSum) break
}
const detail = after.map(([nick, p]) => `${nick}=${p}`).join(' ')
check(sum === baselineSum, `离桌后筹码守恒:${detail},合计 ${sum}(跑之前 ${baselineSum})`)

log('')
log(failures === 0 ? '冒烟通过' : `冒烟失败:${failures} 项`)
alice.ws.close(); bob.ws.close()
process.exit(failures === 0 ? 0 : 1)
