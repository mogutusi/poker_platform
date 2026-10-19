// AI 对手:让几个 dev 账号作为「机器人玩家」进到你的房间,入座、买入、准备,然后像真人一样打牌。
//
// 它们走的是和浏览器**完全同一条路**:真登录、加密 ws、同样的客户端报文(复用 smoke-client.mjs,
// 用的是前端自己的 src/crypto 打包产物)。服务器分不出它们是机器人,所以规则、超时、结算、边池
// 全是真的——这正是拿来测试的意义。只用 dev 种子账号与 dev 共享密钥,仅限本地。
//
// 用法(frontend/ 下,后端在跑):
//   npm run bots -- --room dev --count 3
//   npm run bots -- --room dev --count 5 --style aggressive --exclude alice
// 参数:
//   --room     要进的房间名(默认 dev;房间不存在会被创建)
//   --count    几个 AI(默认 3;不会超过空座数)
//   --style    tight | balanced | loose | aggressive(默认 balanced)
//   --exclude  逗号分隔,不许拿来当 AI 的账号(你自己正在用的账号一定要排除,否则会把你顶下线)
//   --no-autostart  不自动开下一手(默认:一手结束后若全员已准备,AI 会在几秒后开局)
// Ctrl+C 退出:每个 AI 发 leave_room 再断开(局中离桌会自动弃牌,手牌结束后离座)。
//
// 牌力判断是**粗略的启发式**,只为了让行动看起来合理(好牌加注、烂牌弃牌、会诈唬一点),
// 不追求打得好;规则和比牌一律由服务器裁定,这里算错了服务器会拒,AI 再退一步(过牌→跟注→弃牌)。

import { connectAs, ensureInRoom, login, restCall, sleep } from './smoke-client.mjs'

// ── 参数 ──
const argv = process.argv.slice(2)
function arg(name, fallback) {
  const i = argv.indexOf(`--${name}`)
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : fallback
}
const ROOM = arg('room', 'dev')
const COUNT = Math.max(1, Math.min(8, Number(arg('count', '3')) || 3))
const STYLE = arg('style', 'balanced')
const EXCLUDE = new Set(String(arg('exclude', '')).split(',').map((s) => s.trim()).filter(Boolean))
const AUTOSTART = !argv.includes('--no-autostart')

// dev 种子账号(service/app/poker.env.example 的 DEV_USERS)。smoke*/gina 留给冒烟与浏览器用例,排在最后才用。
const POOL = ['bob', 'carol', 'dave', 'eve', 'frank', 'alice', 'gina', 'smoke1', 'smoke2', 'smoke3']

const STYLES = {
  //            入池门槛  加注门槛  诈唬率  加注尺度(底池倍数)
  tight:      { play: 0.42, raise: 0.68, bluff: 0.03, size: 0.6 },
  balanced:   { play: 0.32, raise: 0.6, bluff: 0.07, size: 0.7 },
  loose:      { play: 0.2, raise: 0.62, bluff: 0.1, size: 0.6 },
  aggressive: { play: 0.26, raise: 0.48, bluff: 0.16, size: 1.0 },
}
const S = STYLES[STYLE] ?? STYLES.balanced

const log = (who, ...rest) => console.log(`[bot ${who}]`, ...rest)

// ── 粗略牌力:0..1 ──
const RANKS = '23456789TJQKA'
const rv = (c) => RANKS.indexOf(c.rank) + 2 // 2..14

function preflopStrength([a, b]) {
  const hi = Math.max(rv(a), rv(b))
  const lo = Math.min(rv(a), rv(b))
  let s = (hi + lo) / 32 // 72o ≈ 0.28, AK ≈ 0.84
  if (hi === lo) s = 0.55 + hi / 35 // 22 ≈ 0.61, AA ≈ 0.95
  if (a.suit === b.suit) s += 0.05
  if (hi - lo === 1) s += 0.03
  return Math.min(1, s)
}

/** 7 张(或更少)里最好的牌型类别,只看类别,不比踢脚。 */
function madeCategory(cards) {
  const counts = new Map()
  const suits = new Map()
  for (const c of cards) {
    counts.set(rv(c), (counts.get(rv(c)) ?? 0) + 1)
    suits.set(c.suit, (suits.get(c.suit) ?? 0) + 1)
  }
  const groups = [...counts.values()].sort((x, y) => y - x)
  const flush = [...suits.values()].some((n) => n >= 5)
  const uniq = new Set(cards.map(rv))
  if (uniq.has(14)) uniq.add(1)
  let run = 0
  let straight = false
  for (let r = 1; r <= 14; r++) {
    run = uniq.has(r) ? run + 1 : 0
    if (run >= 5) straight = true
  }
  if (straight && flush) return 8
  if (groups[0] === 4) return 7
  if (groups[0] === 3 && groups[1] >= 2) return 6
  if (flush) return 5
  if (straight) return 4
  if (groups[0] === 3) return 3
  if (groups[0] === 2 && groups[1] === 2) return 2
  if (groups[0] === 2) return 1
  return 0
}

function postflopStrength(hole, board) {
  const mine = madeCategory([...hole, ...board])
  const tableOnly = madeCategory(board)
  // 牌型全在公共牌上(人人都有)不算自己的力量
  const own = mine > tableOnly ? mine : 0
  const byCat = [0.18, 0.5, 0.7, 0.78, 0.84, 0.88, 0.94, 0.98, 1]
  let s = byCat[own]
  if (own === 1) {
    // 对子:顶对比底对强一点
    const topBoard = Math.max(...board.map(rv))
    if (hole.some((c) => rv(c) >= topBoard)) s += 0.1
  }
  // 听牌:同花差一张
  const suitCount = new Map()
  for (const c of [...hole, ...board]) suitCount.set(c.suit, (suitCount.get(c.suit) ?? 0) + 1)
  if (own < 4 && board.length < 5 && [...suitCount.values()].some((n) => n === 4)) s = Math.max(s, 0.42)
  return Math.min(1, s)
}

// ── 一个 AI ──
class Bot {
  constructor(client) {
    this.c = client
    this.name = client.name
    this.seat = null
    this.room = null
    this.bigBlind = 2
    this.buyIn = 100
    this.players = []
    this.acting = null
    this.lastBet = 0
    this.minRaiseTo = 0
    this.pot = 0
    this.board = []
    this.hole = null
    this.handOn = false
    this.seats = []
    this.thinking = false
    this.pending = null // 最近一次发出去的动作,被服务器拒了就退一步
    this.leaving = false
    client.onEvent = (m) => this.onEvent(m)
  }

  me() {
    return this.players.find((p) => p.nickname === this.name) ?? null
  }

  onEvent(m) {
    switch (m.type) {
      case 'state_snapshot':
        this.room = m.room
        this.bigBlind = m.big_blind
        this.buyIn = m.buy_in
        this.seats = m.seats
        this.players = m.players
        this.acting = m.acting_position
        this.lastBet = m.last_bet
        this.minRaiseTo = m.min_raise_to
        this.pot = m.pot
        this.board = m.board
        this.hole = m.your_hole_cards
        this.handOn = m.hand_status !== null
        break
      case 'hand_started':
        this.handOn = true
        this.players = m.players
        this.acting = m.acting_position
        this.pot = m.pot
        this.lastBet = m.last_bet
        this.minRaiseTo = m.min_raise_to
        this.bigBlind = m.big_blind
        this.board = []
        this.hole = null
        this.dealtAt = Date.now()
        break
      case 'hole_cards':
        this.hole = m.cards
        break
      case 'hand_status_changed':
        // 新发了牌:像真人一样先看一眼(浏览器那边公共牌也是一街一街亮的,别抢在画面前面行动)
        if (m.board.length > this.board.length) this.dealtAt = Date.now() + (m.board.length - Math.max(3, this.board.length)) * 1000
        this.board = m.board
        this.lastBet = m.last_bet
        this.minRaiseTo = m.min_raise_to
        this.players = m.players
        break
      case 'player_acted':
        this.players = this.players.map((p) =>
          p.nickname === m.nickname ? { ...p, points: m.points, bet_amount: m.bet_amount, status: m.status } : p,
        )
        this.lastBet = m.last_bet
        this.minRaiseTo = m.min_raise_to
        this.pot = m.pot
        this.acting = m.acting_position
        if (m.nickname === this.name) this.pending = null
        break
      case 'hand_ended':
        this.handOn = false
        this.acting = null
        this.pending = null
        for (const st of m.stacks ?? []) {
          const s = this.seats.find((x) => x.nickname === st.nickname)
          if (s) s.points = st.points
        }
        this.c.events.length = 0 // 长跑不攒事件
        this.endedAt = Date.now()
        this.afterHand() // 立刻补筹码:手与手之间才允许买入,晚了下一手可能已经开了
        break
      case 'user_status_changed': {
        const s = this.seats.find((x) => x.nickname === m.nickname)
        if (s) s.status = m.status
        else if (m.seat_position !== null) this.seats.push({ nickname: m.nickname, seat_position: m.seat_position, status: m.status, points: 0 })
        if (m.seat_position === null) this.seats = this.seats.filter((x) => x.nickname !== m.nickname)
        if (m.nickname === this.name && m.status === 'sitting_in' && !this.leaving) this.ready()
        break
      }
      case 'player_bought_in': {
        const s = this.seats.find((x) => x.nickname === m.nickname)
        if (s) s.points = m.seat_points
        break
      }
      case 'user_left':
        this.seats = this.seats.filter((x) => x.nickname !== m.nickname)
        break
      case 'room_config_changed':
        this.bigBlind = m.big_blind
        this.buyIn = m.buy_in
        break
      case 'error':
        this.onError(m)
        break
      default:
        break
    }
    this.maybeAct()
  }

  onError(m) {
    // 自己的动作被拒:退一步再试(过牌不行就跟注,跟注/加注不行就过牌,再不行就弃牌)
    const p = this.pending
    if (!p || !this.isMyTurn()) {
      if (m.code !== 'NOT_YOUR_TURN') log(this.name, `服务器拒绝: ${m.code}${m.detail ? ` (${m.detail})` : ''}`)
      return
    }
    this.pending = null
    if (p === 'check') this.act('bet', this.lastBet)
    else if (p === 'bet') this.act('check')
    else if (p !== 'fold') this.act('fold')
  }

  isMyTurn() {
    return this.handOn && this.acting !== null && this.players[this.acting]?.nickname === this.name
  }

  maybeAct() {
    if (!this.isMyTurn() || this.thinking || this.pending) return
    this.thinking = true
    // 思考一下再出手:像人,也让你看得清桌上发生了什么。刚发完牌的话再多等一会儿。
    const sinceDeal = Date.now() - (this.dealtAt ?? 0)
    const dealPause = Math.max(0, 1800 - sinceDeal)
    setTimeout(() => {
      this.thinking = false
      if (this.isMyTurn() && !this.pending) this.decide()
    }, dealPause + 700 + Math.random() * 1300)
  }

  decide() {
    const me = this.me()
    if (!me || !this.hole) return
    const toCall = Math.max(0, this.lastBet - me.bet_amount)
    const maxTo = me.points + me.bet_amount
    if (me.points <= 0) return this.act('check') // 已经全下,没有可做的决定
    const strength = this.board.length === 0 ? preflopStrength(this.hole) : postflopStrength(this.hole, this.board)
    const noise = (Math.random() - 0.5) * 0.12
    const s = strength + noise
    const potOdds = toCall / Math.max(1, this.pot + toCall)
    const bluff = Math.random() < S.bluff

    const raiseTo = () => {
      const target = Math.round(this.lastBet + Math.max(this.bigBlind, (this.pot + toCall) * S.size * (0.7 + Math.random() * 0.6)))
      const to = Math.max(this.minRaiseTo, target)
      return to >= maxTo * 0.85 ? maxTo : to // 快推光了就直接全下
    }

    if (toCall === 0) {
      if (s >= S.raise || bluff) return this.act('bet', raiseTo())
      return this.act('check')
    }
    // 要跟注
    if (s >= S.raise + 0.15 || (s >= S.raise && Math.random() < 0.5) || (bluff && toCall <= this.bigBlind * 3)) {
      return this.act('bet', raiseTo())
    }
    if (s >= S.play && s >= potOdds * 0.9) return this.act('bet', Math.min(this.lastBet, maxTo))
    if (toCall <= this.bigBlind && s >= S.play - 0.12) return this.act('bet', Math.min(this.lastBet, maxTo)) // 便宜就看一眼
    return this.act('fold')
  }

  act(action, amount) {
    this.pending = action
    const msg = action === 'bet' ? { type: 'player_action', action, bet_amount: amount } : { type: 'player_action', action }
    const label =
      action === 'check' ? '过牌' : action === 'fold' ? '弃牌' : amount <= this.lastBet ? `跟注 ${amount}` : `加注到 ${amount}`
    log(this.name, label)
    this.c.send(msg)
  }

  ready() {
    if (this.seat === null) return
    const mine = this.seats.find((s) => s.nickname === this.name)
    if (mine && (mine.status === 'ready_to_play' || mine.status === 'playing')) return // 已准备,再发会被拒
    this.c.send({ type: 'set_user_status', status: 'ready_to_play', seat: this.seat })
  }

  async afterHand() {
    if (this.leaving) return
    const mine = this.seats.find((s) => s.nickname === this.name)
    if (mine && mine.points === 0) {
      log(this.name, '筹码打光了,重新买入')
      this.c.send({ type: 'buy_in', seat: this.seat, amount: this.buyIn })
      await sleep(400)
      this.ready()
    } else {
      this.ready()
    }
  }

  async sitDown(freeSeats) {
    for (const seat of freeSeats) {
      this.c.send({ type: 'sit_down', seat, wait_for_big_blind: false })
      try {
        await this.c.wait((m) => (m.type === 'user_status_changed' && m.nickname === this.name && m.seat_position === seat) || m.type === 'error', `${this.name} 入座`, 3000)
      } catch {
        continue
      }
      const ok = this.c.last('user_status_changed')
      if (ok && ok.nickname === this.name && ok.seat_position === seat) {
        this.seat = seat
        return true
      }
      this.c.events.length = 0
    }
    return false
  }

  async buyInAndReady() {
    this.c.send({ type: 'buy_in', seat: this.seat, amount: this.buyIn })
    try {
      await this.c.wait((m) => m.type === 'player_bought_in' && m.nickname === this.name, `${this.name} 买入`, 3000)
    } catch {
      const err = this.c.last('error')
      throw new Error(`${this.name} 买入失败${err ? `: ${err.code}` : ''}(dev 账号积分可能已低于买入额,见 BUGS.md BUG-21)`)
    }
    this.ready()
  }

  async leave() {
    this.leaving = true
    try {
      this.c.send({ type: 'leave_room' })
      await sleep(300)
    } catch {
      /* 连接已断 */
    }
    this.c.ws?.close()
  }
}

// ── 主流程 ──
async function main() {
  // 先查房间里已经坐了谁(大厅接口带座位),避开他们的账号,也知道哪些座位空着。
  // 只登录走 REST,**不连 ws**:连 ws 会把正在用这个账号的人顶下线。
  const candidates = POOL.filter((n) => !EXCLUDE.has(n))
  const bots = []
  const probeSession = await login(candidates[0]).catch((e) => {
    throw new Error(`连不上后端:${e.message}(后端在跑吗?地址 ${process.env.SMOKE_API_URL || 'http://127.0.0.1:8000'})`)
  })
  const { rooms } = await restCall(probeSession, '/lobby/rooms')
  const roomMeta = rooms.find((r) => r.id === ROOM)
  const seatedNames = new Set((roomMeta?.seats ?? []).map((s) => s.nickname))
  const names = candidates.filter((n) => !seatedNames.has(n))
  const maxSeats = roomMeta?.max_seats ?? 9
  const free = Array.from({ length: maxSeats }, (_, i) => i).filter((i) => !(roomMeta?.seats ?? []).some((s) => s.seat_position === i))
  const want = Math.min(COUNT, free.length, names.length)
  if (want === 0) {
    console.log(`房间 ${ROOM} 没有空座(或没有可用的 dev 账号),不加 AI。`)
    process.exit(0)
  }
  console.log(`往房间 ${ROOM} 加 ${want} 个 AI(风格 ${STYLE}${AUTOSTART ? ',自动开下一手' : ''})…`)

  for (const name of names) {
    if (bots.length >= want) break
    let client
    try {
      client = await connectAs(name)
    } catch (e) {
      log(name, `登录失败,跳过:${e.message}`)
      continue
    }
    const bot = new Bot(client)
    try {
      await ensureInRoom(client, ROOM)
      // 重新看一眼空座(前一个 AI 刚坐下)
      const taken = new Set(bot.seats.map((s) => s.seat_position))
      const freeNow = Array.from({ length: maxSeats }, (_, i) => i).filter((i) => !taken.has(i))
      if (!(await bot.sitDown(freeNow))) {
        log(name, '没坐上,放弃')
        await bot.leave()
        continue
      }
      await bot.buyInAndReady()
      log(name, `坐在 ${bot.seat + 1} 号位,已准备`)
      bots.push(bot)
    } catch (e) {
      log(name, e.message)
      await bot.leave()
    }
  }

  if (bots.length === 0) {
    console.log('一个 AI 都没加进去。')
    process.exit(1)
  }
  console.log(`${bots.length} 个 AI 已就位。所有人准备好之后点 Start Game 开局;Ctrl+C 让它们离桌。`)

  // 自动开下一手:由第一个 AI 负责。全员已准备才发,发早了服务器回 NOT_READY,无害。
  // 自动开下一手,与牌桌页同一条规则:上一手结束 5 秒后,由**座位号最小的已准备玩家**发 start_hand。
  // 那个人是我们的 AI 就由它发;是真人的话交给他的页面(页面也会自动发),他 2.5 秒内没发(关了自动)
  // 才由 AI 代发。第一手:桌上全是 AI 时 AI 自己开;有真人在就等真人点 Start Game。
  if (AUTOSTART) {
    const host = bots[0]
    const mine = new Set(bots.map((b) => b.name))
    let lastTry = 0
    setInterval(() => {
      if (host.handOn || host.leaving || Date.now() - lastTry < 3000) return
      const ready = host.seats.filter((s) => s.status === 'ready_to_play').sort((x, y) => x.seat_position - y.seat_position)
      if (ready.length < 2) return
      const now = Date.now()
      let starter = null
      if (host.endedAt === undefined) {
        // 还没打过:只有全桌都是 AI 且都准备好了才自己开
        const allBots = host.seats.every((s) => mine.has(s.nickname))
        if (allBots && ready.length === host.seats.length) starter = bots.find((b) => b.name === ready[0].nickname)
      } else if (now - host.endedAt >= 5000) {
        starter = bots.find((b) => b.name === ready[0].nickname) // 最小座位号是我们的 AI
        if (!starter && now - host.endedAt >= 7500) starter = bots.find((b) => ready.some((r) => r.nickname === b.name)) // 真人没开:代开
      }
      if (!starter) return
      lastTry = now
      starter.c.send({ type: 'start_hand', seat: starter.seat })
    }, 500)
  }

  const shutdown = async () => {
    console.log('AI 离桌中…')
    await Promise.all(bots.map((b) => b.leave()))
    process.exit(0)
  }
  process.on('SIGINT', shutdown)
  process.on('SIGTERM', shutdown)
  // 由 Next 的 /api/bots 拉起时,父进程用关闭 stdin 来通知退出(Windows 上没有可靠的 SIGTERM)。
  if (process.env.BOTS_STDIN_CONTROL === '1') {
    process.stdin.on('end', shutdown)
    process.stdin.resume()
  }
}

main().catch((e) => {
  console.error(e.message)
  process.exit(1)
})
