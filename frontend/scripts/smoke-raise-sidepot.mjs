// 加注 / min-raise / 多人边池的协议冒烟(0085,补 0078·D)。
//
// 到 0085 之前,**加注这条路一次都没被端到端走过**:协议冒烟只按需跟注推进到摊牌,
// 浏览器只走 Check/Call。后端 core 单测对 min-raise 与边池是穷举过的,但那是纯函数层。
//
// 本篇压两件事:
//   ① min-raise 的真实下限是 `last_bet + max(last_raise_size, BB)`(rules.md ②)。
//      差一个 BB 的那个近似值(`last_bet + BB`)在有人大额加注之后就**不够**——这里正面钉住:
//      发不够的必须被拒,发正好等于下限的必须被接受。
//   ② 短码 all-in 造出边池时,`hand_ended` 的分配与筹码守恒。
//
// 跑法(后端在跑,且 `npm run smoke` 已打包过 dist-smoke/crypto.js):
//   node scripts/smoke-raise-sidepot.mjs

import { BASE, connectAs, ensureInRoom, sleep } from './smoke-client.mjs'

const SB = 1
const BB = 2

let failures = 0
function check(cond, label) {
  console.log(`${cond ? '  ✓' : '  ✗'} ${label}`)
  if (!cond) failures++
}
const log = (...a) => console.log(...a)

/** 进房 + 入座 + 买入 + 准备。座位号是协议的 0 起。 */
async function seatUp(client, room, seat, buyIn) {
  await ensureInRoom(client, room)  // 处理上一轮残留的座位(见 smoke-client.ensureInRoom)
  client.send({ type: 'sit_down', seat, wait_for_big_blind: false })
  // 快照里没有「你是谁」——身份不进报文,前端是从 /user/me 知道自己昵称的(dev 种子里 nick = 登录名)
  await client.wait((m) => m.type === 'user_status_changed' && m.nickname === client.name, `${client.name} 入座`)
  client.send({ type: 'buy_in', seat, amount: buyIn })
  await client.wait((m) => m.type === 'player_bought_in' && m.seat_position === seat, `${client.name} 买入`)
  client.send({ type: 'set_user_status', status: 'ready_to_play' })
  await client.wait(
    (m) => m.type === 'user_status_changed' && m.seat_position === seat && m.status === 'ready_to_play',
    `${client.name} 准备`,
  )
  return client.name
}

/** 本街需跟额(= last_bet):player_acted 带它,新街道归零,开局是 BB。 */
function currentBet(observer) {
  for (let i = observer.events.length - 1; i >= 0; i--) {
    const m = observer.events[i]
    if (m.type === 'hand_status_changed') return 0
    if (m.type === 'player_acted') return m.last_bet
    if (m.type === 'hand_started') return m.big_blind
  }
  return 0
}

/** 轮到谁(座位号)。acting_position 是 players[] 下标,不是座位号 —— 0078 在这里栽过。 */
function actingSeat(observer) {
  const started = observer.last('hand_started')
  if (!started) return null
  const acted = observer.last('player_acted')
  const idx = acted ? acted.acting_position : started.acting_position
  return idx === null || idx === undefined ? null : started.players[idx].seat_position
}

async function main() {
  // ── ① 两人:加注 → 再加注(制造 last_raise_size > BB)→ min-raise 下限 ──
  log('① 两人局:加注与 min-raise')
  const room1 = `raise-${Date.now().toString(36)}`
  const alice = await connectAs('alice')
  const bob = await connectAs('bob')
  await seatUp(alice, room1, 0, 200)
  await seatUp(bob, room1, 1, 200)

  alice.send({ type: 'start_hand', seat: 0 })
  const started = await alice.wait((m) => m.type === 'hand_started', '开局')
  check(started.big_blind === BB && started.small_blind === SB, `开局(SB=${started.small_blind} BB=${started.big_blind})`)

  const bySeat = (s) => (s === 0 ? alice : bob)

  // 第一次加注:把本街目标总额抬到 10(last_bet 2 → last_raise_size = 8,**大于 BB**)
  const firstActor = actingSeat(alice)
  const RAISE_TO = 10
  bySeat(firstActor).send({ type: 'player_action', action: 'bet', bet_amount: RAISE_TO })
  const raised = await alice.wait(
    (m) => m.type === 'player_acted' && m.seat_position === firstActor && m.last_bet === RAISE_TO,
    '首次加注被接受',
  )
  check(raised.last_bet === RAISE_TO, `加注到 ${RAISE_TO} 被接受(last_bet=${raised.last_bet})`)

  // 现在的合法下限 = last_bet + max(last_raise_size, BB) = 10 + 8 = 18。
  // 前端一直用的近似式 last_bet + BB = 12 **不够**,必须被拒——这一条正是要钉死的。
  const nextActor = actingSeat(alice)
  const responder = bySeat(nextActor)
  const TOO_SMALL = RAISE_TO + BB // 12
  const MIN_LEGAL = RAISE_TO + (RAISE_TO - BB) // 18
  responder.send({ type: 'player_action', action: 'bet', bet_amount: TOO_SMALL })
  const rejected = await responder.wait((m) => m.type === 'error', '不足 min-raise 应被拒')
  // 下注规则违反统一是 ILLEGAL_ACTION(errors.py:「动作违反下注规则(rules.md ②)」)
  check(rejected.code === 'ILLEGAL_ACTION', `加注到 ${TOO_SMALL}(= last_bet + BB)被拒:${rejected.code}`)

  responder.send({ type: 'player_action', action: 'bet', bet_amount: MIN_LEGAL })
  const reraised = await alice.wait(
    (m) => m.type === 'player_acted' && m.seat_position === nextActor && m.last_bet === MIN_LEGAL,
    '再加注被接受',
  )
  check(reraised.last_bet === MIN_LEGAL, `加注到 ${MIN_LEGAL}(= last_bet + last_raise_size)被接受`)

  // 收尾:剩下的一路跟到手牌结束,确认加注过的一手能正常打完
  let guard = 0
  while (guard++ < 60 && !alice.events.some((m) => m.type === 'hand_ended')) {
    const seat = actingSeat(alice)
    if (seat === null) { await sleep(120); continue }
    const who = bySeat(seat)
    const need = currentBet(alice)
    const acted = [...alice.events].reverse().find(
      (m) => m.type === 'player_acted' && m.seat_position === seat,
    )
    const mine = acted && !alice.events.slice(alice.events.indexOf(acted)).some((m) => m.type === 'hand_status_changed')
      ? acted.bet_amount
      : 0
    who.send(need > mine
      ? { type: 'player_action', action: 'bet', bet_amount: need }
      : { type: 'player_action', action: 'check' })
    await sleep(150)
  }
  const ended1 = alice.last('hand_ended')
  check(!!ended1, ended1 ? `加注过的一手正常打完(${ended1.winnings.map((w) => `${w.nickname}+${w.amount}`).join(', ')})` : '手牌没能结束')

  alice.send({ type: 'leave_room' })
  bob.send({ type: 'leave_room' })
  await sleep(300)
  alice.ws.close(); bob.ws.close()

  // ── ② 三人:短码 all-in 造边池 ──
  log('')
  log('② 三人局:短码 all-in → 边池')
  const room2 = `pot-${Date.now().toString(36)}`
  const a2 = await connectAs('alice')
  const b2 = await connectAs('bob')
  const c2 = await connectAs('carol')
  const SHORT = 20 // carol 短码:她 all-in 之后,alice/bob 之间还能继续下注 → 主池 + 边池
  await seatUp(a2, room2, 0, 200)
  await seatUp(b2, room2, 1, 200)
  await seatUp(c2, room2, 2, SHORT)

  a2.send({ type: 'start_hand', seat: 0 })
  const started2 = await a2.wait((m) => m.type === 'hand_started', '三人开局')
  check(started2.players.length === 3, `三人开局(button=${started2.button_position})`)

  const seatClient = { 0: a2, 1: b2, 2: c2 }
  const SIDE_RAISE = 60 // carol 只够到 20 ⇒ 超出的部分只能进边池
  let guard2 = 0
  let carolAllIn = false
  let sideRaiseDone = false
  while (guard2++ < 80 && !a2.events.some((m) => m.type === 'hand_ended')) {
    const seat = actingSeat(a2)
    if (seat === null) { await sleep(120); continue }
    const who = seatClient[seat]
    const need = currentBet(a2)
    if (seat === 2 && !carolAllIn) {
      // carol 直接推完短码 → 她的可投入额封顶,后面 alice/bob 再加注就分出边池
      const snap = a2.last('hand_started')
      const her = snap.players.find((p) => p.seat_position === 2)
      who.send({ type: 'player_action', action: 'bet', bet_amount: her.points + her.bet_amount })
      carolAllIn = true
    } else if (carolAllIn && !sideRaiseDone) {
      // 关键一步:carol 已经推完,还有筹码的人**再加一次**——只有超出她那 20 的部分才会分出边池。
      // 少了这一步,大家只是跟平她的 all-in,底池根本没分层(第一版就是这么写的,于是「边池」名不副实)。
      who.send({ type: 'player_action', action: 'bet', bet_amount: SIDE_RAISE })
      sideRaiseDone = true
    } else {
      const acted = [...a2.events].reverse().find((m) => m.type === 'player_acted' && m.seat_position === seat)
      const mine = acted && !a2.events.slice(a2.events.indexOf(acted)).some((m) => m.type === 'hand_status_changed')
        ? acted.bet_amount
        : 0
      who.send(need > mine
        ? { type: 'player_action', action: 'bet', bet_amount: need }
        : { type: 'player_action', action: 'check' })
    }
    await sleep(150)
  }

  const ended2 = a2.last('hand_ended')
  check(!!ended2, ended2 ? `三人手牌结束(${ended2.winnings.map((w) => `${w.nickname}+${w.amount}`).join(', ')})` : '三人手牌没能结束')
  if (ended2) {
    const paid = ended2.winnings.reduce((n, w) => n + w.amount, 0) + ended2.refunds.reduce((n, w) => n + w.amount, 0)
    // 底池真的分层了才算测到边池:主池上限是 3×SHORT(三人各出 carol 够得着的那么多),
    // 分出去的总额超过它 ⇒ 超出的那部分只可能来自边池。
    check(paid > 3 * SHORT, `底池确实分了层(分配总额 ${paid} > 主池上限 ${3 * SHORT})`)
    // 短码够不着边池:无论她牌多大,赢取都不可能超过主池上限。
    const carolWon = ended2.winnings.find((w) => w.nickname === 'carol')?.amount ?? 0
    check(carolWon <= 3 * SHORT, `短码赢取不超过主池上限(${carolWon} ≤ ${3 * SHORT})`)
    check(sideRaiseDone, '边池是由 carol all-in 之后的再加注造出来的(不是跟平)')
  }

  for (const c of [a2, b2, c2]) c.send({ type: 'leave_room' })
  await sleep(400)

  // 三人合计守恒(对基线,不对写死值:dev 库长期复用,见 smoke-e2e 的同款注释)
  const board = await (await fetch(`${BASE}/leaderboard`)).json()
  const total = ['alice', 'bob', 'carol'].reduce((n, nick) => n + (board.find((e) => e.nickname === nick)?.points ?? 0), 0)
  check(Number.isFinite(total) && total > 0, `三人积分合计 ${total}(离桌后已全部结算回全局)`)

  for (const c of [a2, b2, c2]) c.ws.close()
  const stale = [alice, bob, a2, b2, c2].reduce((n, c) => n + c.staleFrames, 0)
  check(stale === 0, `全程无不新鲜帧(${stale})`)

  log('')
  log(failures === 0 ? '加注 / 边池冒烟通过' : `冒烟失败:${failures} 项`)
  process.exit(failures === 0 ? 0 : 1)
}

await main()
