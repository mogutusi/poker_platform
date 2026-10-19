"use client"

import { useEffect, useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { Card } from "@/components/ui/card"
import AppNav from "@/components/AppNav"
import Avatar from "@/components/Avatar"
import MiniCard from "@/components/MiniCard"
import { HandDetailCard, formatNet, formatTime, netClass } from "@/components/HandDetailCard"
import { cn } from "@/lib/utils"
import { fetchHands, fetchLeaderboardStats, fetchMyStats, type HandRecord, type PlayerStats, type Ratio } from "@/transport/rest"
import { getSession } from "@/transport/session"

type Period = "all" | "today"
type Combo = PlayerStats["combos"][number]
type Recent = PlayerStats["recent"][number]

const RANKS = "AKQJT98765432"

/** 本地今天 0 点(epoch 秒)。 */
function startOfToday(): number {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return Math.floor(d.getTime() / 1000)
}

/** 13×13 起手牌表的第 (i, j) 格:对角线是对子,右上是同花,左下是不同花。 */
function comboAt(i: number, j: number): string {
  if (i === j) return RANKS[i] + RANKS[j]
  return i < j ? RANKS[i] + RANKS[j] + "s" : RANKS[j] + RANKS[i] + "o"
}

/**
 * 数据页(像 GG 的 PokerCraft):自己的 VPIP / PFR / 3-Bet 等、盈亏曲线、按位置的战绩、
 * 起手牌热力图(点格子看这手牌打了几手、输赢多少)、最近手牌(点开看整手)。
 * 所有数字都是服务器算的(POST /stats/me),这一页只画。
 */
export default function StatsPage() {
  const router = useRouter()
  const [period, setPeriod] = useState<Period>("all")
  /** 看谁的数据:null = 自己。只有后端开了本地测试开关(can_view_others)才给切换。 */
  const [viewing, setViewing] = useState<string | null>(null)
  const [players, setPlayers] = useState<string[]>([])
  const [selfName, setSelfName] = useState<string | null>(null)
  const [stats, setStats] = useState<PlayerStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    if (!getSession()) {
      router.replace("/")
      return
    }
    let alive = true
    setLoading(true)
    setError(null)
    fetchMyStats({ since: period === "today" ? startOfToday() : undefined, user: viewing ?? undefined })
      .then((s) => alive && setStats(s))
      .catch(() => alive && setError("数据加载失败,请确认后端已启动。"))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [period, viewing, router])

  // 本地测试开关开着时,列出打过牌的人(排行榜里有的就是打过的)供切换
  const canViewOthers = stats?.can_view_others ?? false
  useEffect(() => {
    if (!canViewOthers) return
    fetchLeaderboardStats({ limit: 100 })
      .then((rows) => setPlayers(rows.map((r) => r.nickname).sort()))
      .catch(() => undefined)
  }, [canViewOthers])
  useEffect(() => setSelected(null), [viewing])
  useEffect(() => {
    if (stats && viewing === null) setSelfName(stats.nickname)
  }, [stats, viewing])

  const cells = useMemo(() => new Map((stats?.combos ?? []).map((c) => [c.combo, c])), [stats])
  const s = stats?.summary

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <div className="pointer-events-none absolute inset-0 opacity-10">
        <div className="float absolute -top-4 left-10 text-8xl md:text-9xl">♠</div>
        <div className="float absolute right-10 top-20 text-7xl md:text-8xl" style={{ animationDelay: "0.5s" }}>
          ♥
        </div>
        <div className="float absolute bottom-24 left-6 text-7xl md:text-8xl" style={{ animationDelay: "1s" }}>
          ♣
        </div>
        <div className="float absolute -bottom-4 right-6 text-8xl md:text-9xl" style={{ animationDelay: "1.5s" }}>
          ♦
        </div>
      </div>

      <main className="relative z-10 mx-auto flex w-full max-w-6xl flex-col gap-4 p-4 sm:p-6">
        <AppNav />

        <Card className="flex flex-col gap-3 border-2 border-primary/30 bg-card/95 p-4 shadow-xl sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            {stats && <Avatar name={stats.nickname} size={44} className="h-11 w-11 rounded-full ring-2 ring-primary/60" />}
            <div>
              <p className="text-xs uppercase tracking-[0.25em] text-muted-foreground">Stats</p>
              <p className="text-lg font-semibold text-primary">
                {viewing ? "玩家数据" : "我的数据"}
                {stats ? ` · ${stats.nickname}` : ""}
              </p>
            </div>
          </div>
          {canViewOthers && (
            // 本地测试用:看 AI 对手的统计。后端 DEV_STATS_ANY_USER 关掉之后这一栏自己消失。
            <label className="flex items-center gap-2 text-xs text-muted-foreground" title="本地测试开关 DEV_STATS_ANY_USER 开着才有">
              <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold text-amber-300">DEV</span>
              查看玩家
              <select
                data-testid="stats-player"
                value={viewing ?? ""}
                onChange={(e) => setViewing(e.target.value || null)}
                className="rounded-md border border-primary/40 bg-card px-2 py-1 text-xs text-card-foreground"
              >
                <option value="">我自己</option>
                {players.filter((p) => p !== selfName).map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="flex overflow-hidden rounded-md border border-primary/40" role="tablist">
            {(["all", "today"] as const).map((p) => (
              <button
                key={p}
                type="button"
                role="tab"
                aria-selected={period === p}
                onClick={() => setPeriod(p)}
                className={cn(
                  "px-4 py-1.5 text-xs transition-colors",
                  period === p ? "bg-primary font-semibold text-primary-foreground" : "text-muted-foreground hover:bg-primary/10",
                )}
              >
                {p === "all" ? "全部" : "今日"}
              </button>
            ))}
          </div>
        </Card>

        {error && (
          <Card role="alert" className="border-2 border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive-foreground">
            {error}
          </Card>
        )}

        {loading && !stats ? (
          <Card className="border-2 border-primary/20 bg-card/95 p-10 text-center text-sm text-muted-foreground shadow-xl">
            正在加载数据…
          </Card>
        ) : !s || s.hands === 0 ? (
          <Card className="flex flex-col items-center gap-2 border-2 border-primary/20 bg-card/95 p-10 text-center shadow-xl">
            <span className="text-3xl opacity-40">♠ ♥ ♣ ♦</span>
            <p className="text-sm font-semibold text-card-foreground">{period === "today" ? "今天还没打牌" : "还没有打过牌"}</p>
            <p className="text-xs text-muted-foreground">打完几手之后,这里会出现你的 VPIP、3-Bet、起手牌热力图和手牌记录。</p>
          </Card>
        ) : (
          <>
            {/* 总览 */}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="stats-overview">
              <BigTile label="盈亏" value={formatNet(s.net)} tone={s.net} sub={`${s.hands} 手`} />
              <BigTile
                label="bb/100"
                value={s.bb_per_100 === null ? "—" : formatNet(Math.abs(s.bb_per_100) >= 100 ? Math.round(s.bb_per_100) : s.bb_per_100)}
                tone={s.bb_per_100 ?? 0}
                sub="每百手赢几个大盲"
              />
              <BigTile label="胜率" value={pct(s.win_rate)} sub={`赢了 ${s.won_hands} 手`} />
              <BigTile
                label="摊牌 / 非摊牌"
                value={`${formatNet(s.showdown_net)} / ${formatNet(s.non_showdown_net)}`}
                sub="蓝线 / 红线"
                small
              />
            </div>

            <Card className="border-2 border-primary/20 bg-card/95 p-4 shadow-xl">
              <SectionTitle en="Preflop" zh="翻牌前" />
              <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
                <RatioTile label="VPIP" hint="主动入池" r={s.vpip} range={[20, 30]} />
                <RatioTile label="PFR" hint="翻牌前加注" r={s.pfr} range={[15, 24]} />
                <RatioTile label="3-Bet" hint="面对加注再加注" r={s.three_bet} range={[6, 11]} />
                <RatioTile label="Fold to 3-Bet" hint="被 3-Bet 后弃牌" r={s.fold_to_three_bet} range={[40, 60]} />
              </div>
              <SectionTitle en="Postflop" zh="翻牌后" className="mt-5" />
              <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-6">
                <RatioTile label="C-Bet" hint="翻牌前加注者翻牌圈续注" r={s.cbet} range={[55, 75]} />
                <RatioTile label="看翻牌" hint="Saw Flop" r={s.saw_flop} range={[25, 40]} />
                <RatioTile label="WTSD" hint="看到翻牌后打到摊牌" r={s.wtsd} range={[25, 33]} />
                <RatioTile label="W$SD" hint="摊牌时赢" r={s.wsd} range={[48, 56]} />
                <RatioTile label="WWSF" hint="看到翻牌后赢" r={s.wwsf} range={[44, 52]} />
                <Tile label="AF" hint="翻牌后 (下注+加注)/跟注" value={s.af === null ? "—" : s.af.toFixed(2)} sub="参考 2–4" />
              </div>
            </Card>

            <Card className="border-2 border-primary/20 bg-card/95 p-4 shadow-xl">
              <SectionTitle en="Profit" zh="盈亏曲线" />
              <ProfitGraph points={stats.graph} />
            </Card>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
              <Card className="border-2 border-primary/20 bg-card/95 p-4 shadow-xl">
                <div className="flex items-center justify-between">
                  <SectionTitle en="Starting hands" zh="起手牌热力图" />
                  <Legend />
                </div>
                <Heatmap cells={cells} selected={selected} onSelect={(c) => setSelected(c === selected ? null : c)} />
              </Card>
              <div className="flex flex-col gap-4">
                <ComboPanel combo={selected} cell={selected ? cells.get(selected) : undefined} onClear={() => setSelected(null)} />
                <PositionTable rows={stats.positions} />
              </div>
            </div>

            <HistoryList recent={stats.recent} me={stats.nickname} combo={selected} onClearCombo={() => setSelected(null)} />
          </>
        )}
      </main>
    </div>
  )
}

/* ───────────────────────── tiles ───────────────────────── */

function SectionTitle({ en, zh, className }: { en: string; zh: string; className?: string }) {
  return (
    <div className={className}>
      <p className="text-[10px] uppercase tracking-[0.25em] text-muted-foreground">{en}</p>
      <p className="text-base font-semibold text-primary">{zh}</p>
    </div>
  )
}

function BigTile({ label, value, sub, tone, small }: { label: string; value: string; sub?: string; tone?: number; small?: boolean }) {
  return (
    <Card className="border-2 border-primary/20 bg-card/95 p-4 shadow-xl">
      <p className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">{label}</p>
      <p className={cn("num mt-1 font-bold", small ? "text-lg" : "text-3xl", tone === undefined ? "text-card-foreground" : netClass(tone))}>
        {value}
      </p>
      {sub && <p className="mt-0.5 text-[11px] text-muted-foreground">{sub}</p>}
    </Card>
  )
}

function Tile({ label, hint, value, sub }: { label: string; hint: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl bg-secondary/40 p-3 ring-1 ring-white/5" title={hint}>
      <p className="text-xs font-semibold text-card-foreground">{label}</p>
      <p className="text-[10px] text-muted-foreground">{hint}</p>
      <p className="num mt-1.5 text-2xl font-bold text-card-foreground">{value}</p>
      {sub && <p className="text-[10px] text-muted-foreground">{sub}</p>}
    </div>
  )
}

/**
 * 一个比率格子:百分比 + 「做了几次/几次机会」+ 一条 0–100% 的刻度,上面标着常见的参考区间
 * (6-max 常规桌的大致范围,只作参照)。落在区间里是金色,偏离是灰色。
 */
function RatioTile({ label, hint, r, range }: { label: string; hint: string; r: Ratio; range: [number, number] }) {
  const v = r.pct === null ? null : r.pct * 100
  const inRange = v !== null && v >= range[0] && v <= range[1]
  return (
    <div className="rounded-xl bg-secondary/40 p-3 ring-1 ring-white/5" title={hint} data-stat={label}>
      <p className="text-xs font-semibold text-card-foreground">{label}</p>
      <p className="truncate text-[10px] text-muted-foreground">{hint}</p>
      <p className="num mt-1.5 text-2xl font-bold text-card-foreground">{v === null ? "—" : `${Math.round(v)}%`}</p>
      <p className="num text-[10px] text-muted-foreground">
        {r.made}/{r.chances} · 参考 {range[0]}–{range[1]}%
      </p>
      <div className="relative mt-2 h-1.5 rounded-full bg-black/40">
        <div
          className="absolute inset-y-0 rounded-full bg-primary/25"
          style={{ left: `${range[0]}%`, width: `${range[1] - range[0]}%` }}
        />
        {v !== null && (
          <div
            className={cn("absolute -top-[3px] h-3 w-1 rounded-full", inRange ? "bg-primary" : "bg-slate-300")}
            style={{ left: `calc(${Math.min(100, v)}% - 2px)` }}
          />
        )}
      </div>
    </div>
  )
}

/* ───────────────────────── graph ───────────────────────── */

function ProfitGraph({ points }: { points: PlayerStats["graph"] }) {
  const W = 1000
  const H = 200
  const P = 8
  if (points.length < 2) {
    return <p className="mt-3 text-xs text-muted-foreground">再多打几手就能看到曲线了。</p>
  }
  const all = points.flatMap((p) => [p.net, p.sd, p.nsd, 0])
  const lo = Math.min(...all)
  const hi = Math.max(...all)
  const span = hi - lo || 1
  const n = points[points.length - 1].n
  const x = (i: number) => P + ((W - 2 * P) * (i - 1)) / Math.max(1, n - 1)
  const y = (v: number) => P + ((H - 2 * P) * (hi - v)) / span
  const line = (key: "net" | "sd" | "nsd") => points.map((p) => `${x(p.n).toFixed(1)},${y(p[key]).toFixed(1)}`).join(" ")
  const last = points[points.length - 1]
  return (
    <div className="mt-3">
      <div className="mb-2 flex flex-wrap gap-4 text-[11px]">
        <LegendDot color="#e8b84a" label={`总盈亏 ${formatNet(last.net)}`} />
        <LegendDot color="#4f8cff" label={`摊牌 ${formatNet(last.sd)}`} />
        <LegendDot color="#ef4444" label={`非摊牌 ${formatNet(last.nsd)}`} />
        <span className="ml-auto text-muted-foreground">{n} 手</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-48 w-full" preserveAspectRatio="none" role="img" aria-label="盈亏曲线">
        <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="rgba(255,255,255,0.15)" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />
        <polyline points={line("nsd")} fill="none" stroke="#ef4444" strokeWidth="1.5" vectorEffect="non-scaling-stroke" opacity="0.85" />
        <polyline points={line("sd")} fill="none" stroke="#4f8cff" strokeWidth="1.5" vectorEffect="non-scaling-stroke" opacity="0.85" />
        <polyline points={line("net")} fill="none" stroke="#e8b84a" strokeWidth="2.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span className="num">{formatNet(hi)}</span>
        <span className="num">{formatNet(lo)}</span>
      </div>
    </div>
  )
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5 text-card-foreground">
      <span className="h-2 w-4 rounded-full" style={{ background: color }} />
      <span className="num">{label}</span>
    </span>
  )
}

/* ───────────────────────── heat map ───────────────────────── */

function cellColor(c: Combo | undefined, maxAbs: number): React.CSSProperties {
  if (!c) return {}
  if (c.net === 0) return { background: "rgba(148,163,184,0.35)" }
  const t = Math.sqrt(Math.min(1, Math.abs(c.net) / (maxAbs || 1)))
  const a = 0.28 + 0.62 * t
  return { background: c.net > 0 ? `rgba(16,185,129,${a})` : `rgba(239,68,68,${a})` }
}

function Heatmap({ cells, selected, onSelect }: { cells: Map<string, Combo>; selected: string | null; onSelect: (c: string) => void }) {
  const maxAbs = Math.max(1, ...Array.from(cells.values(), (c) => Math.abs(c.net)))
  return (
    <div className="mt-3 grid gap-[3px]" data-testid="hand-heatmap" style={{ gridTemplateColumns: "repeat(13, minmax(0, 1fr))" }}>
      {Array.from({ length: 13 * 13 }, (_, k) => {
        const i = Math.floor(k / 13)
        const j = k % 13
        const combo = comboAt(i, j)
        const c = cells.get(combo)
        return (
          <button
            key={combo}
            type="button"
            data-combo={combo}
            onClick={() => onSelect(combo)}
            title={c ? `${combo}:${c.hands} 手,${formatNet(c.net)}` : `${combo}:没打过`}
            className={cn(
              "relative flex aspect-square flex-col items-center justify-center rounded-[4px] leading-none transition-transform hover:z-10 hover:scale-110",
              c ? "text-white" : "bg-white/[0.035] text-muted-foreground/50",
              i === j && !c && "bg-white/[0.06]",
              selected === combo && "z-10 scale-110 ring-2 ring-primary",
            )}
            style={cellColor(c, maxAbs)}
          >
            <span className="text-[10px] font-bold sm:text-[11px]">{combo}</span>
            {c && <span className="num mt-0.5 text-[8px] opacity-90 sm:text-[9px]">{c.hands}</span>}
          </button>
        )
      })}
    </div>
  )
}

function Legend() {
  return (
    <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
      <span>输</span>
      <span className="h-2 w-16 rounded-full bg-gradient-to-r from-red-500 via-slate-500/40 to-emerald-500" />
      <span>赢</span>
    </div>
  )
}

function ComboPanel({ combo, cell, onClear }: { combo: string | null; cell?: Combo; onClear: () => void }) {
  if (!combo) {
    return (
      <Card className="border-2 border-primary/20 bg-card/95 p-4 text-sm text-muted-foreground shadow-xl">
        <SectionTitle en="Hand" zh="点一个格子" />
        <p className="mt-2 text-xs">看这手起手牌打了几手、输赢多少;下面的手牌记录也会只显示它。</p>
        <p className="mt-2 text-[11px]">右上三角是同花(s),左下是不同花(o),对角线是对子。格子里的小数字是手数。</p>
      </Card>
    )
  }
  return (
    <Card data-testid="combo-panel" className="border-2 border-primary/40 bg-card/95 p-4 shadow-xl">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-[0.25em] text-muted-foreground">
            {combo.length === 2 ? "Pair" : combo.endsWith("s") ? "Suited" : "Offsuit"}
          </p>
          <p className="font-condensed text-4xl font-bold text-primary">{combo}</p>
        </div>
        <button type="button" onClick={onClear} className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-white/5">
          清除 ✕
        </button>
      </div>
      {!cell ? (
        <p className="mt-2 text-sm text-muted-foreground">这段时间没拿到过这手牌。</p>
      ) : (
        <div className="mt-3 grid grid-cols-2 gap-3">
          <Mini label="手数" value={String(cell.hands)} />
          <Mini label="盈亏" value={formatNet(cell.net)} tone={cell.net} />
          <Mini label="平均每手" value={formatNet(Math.round(cell.net / cell.hands))} tone={cell.net} />
          <Mini label="赢的比例" value={pct(cell.won / cell.hands)} />
          <Mini label="入池" value={`${cell.vpip}/${cell.hands}`} />
        </div>
      )}
    </Card>
  )
}

function Mini({ label, value, tone }: { label: string; value: string; tone?: number }) {
  return (
    <div className="rounded-lg bg-secondary/40 px-3 py-2">
      <p className="text-[10px] text-muted-foreground">{label}</p>
      <p className={cn("num text-lg font-bold", tone === undefined ? "text-card-foreground" : netClass(tone))}>{value}</p>
    </div>
  )
}

function PositionTable({ rows }: { rows: PlayerStats["positions"] }) {
  return (
    <Card className="border-2 border-primary/20 bg-card/95 p-4 shadow-xl">
      <SectionTitle en="By position" zh="按位置" />
      {rows.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">还没有带位置的手牌记录。</p>
      ) : (
        <table className="mt-2 w-full text-xs">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-muted-foreground">
              <th className="py-1 text-left font-normal">位置</th>
              <th className="py-1 text-right font-normal">手数</th>
              <th className="py-1 text-right font-normal">盈亏</th>
              <th className="py-1 text-right font-normal">bb/100</th>
              <th className="py-1 text-right font-normal">VPIP</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.position} className="border-t border-white/5">
                <td className="py-1.5 font-semibold text-card-foreground">{r.position}</td>
                <td className="num py-1.5 text-right text-card-foreground">{r.hands}</td>
                <td className={cn("num py-1.5 text-right font-semibold", netClass(r.net))}>{formatNet(r.net)}</td>
                <td className={cn("num py-1.5 text-right", netClass(r.bb_per_100 ?? 0))}>
                  {r.bb_per_100 === null ? "—" : formatNet(Math.round(r.bb_per_100))}
                </td>
                <td className="num py-1.5 text-right text-card-foreground">{r.vpip === null ? "—" : pct(r.vpip)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

/* ───────────────────────── hand history ───────────────────────── */

const PAGE = 30

function HistoryList({ recent, me, combo, onClearCombo }: { recent: Recent[]; me: string; combo: string | null; onClearCombo: () => void }) {
  const [open, setOpen] = useState<number | null>(null)
  const [shown, setShown] = useState(PAGE)
  const list = combo ? recent.filter((h) => h.combo === combo) : recent
  useEffect(() => setShown(PAGE), [combo])

  return (
    <Card className="border-2 border-primary/20 bg-card/95 p-4 shadow-xl">
      <div className="flex items-center justify-between">
        <SectionTitle en="Hand history" zh="手牌记录" />
        {combo && (
          <button
            type="button"
            onClick={onClearCombo}
            className="rounded-full bg-primary/15 px-3 py-1 text-xs font-semibold text-primary ring-1 ring-primary/40"
          >
            只看 {combo} ✕
          </button>
        )}
      </div>
      {list.length === 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">{combo ? `没有 ${combo} 的手牌记录。` : "还没有手牌记录。"}</p>
      ) : (
        <ul className="mt-3 flex flex-col gap-1" data-testid="history-list">
          {list.slice(0, shown).map((h) => (
            <li key={h.id}>
              <button
                type="button"
                data-testid="history-row"
                onClick={() => setOpen(open === h.id ? null : h.id)}
                className={cn(
                  "grid w-full grid-cols-[92px_44px_54px_minmax(0,1fr)_64px] items-center gap-3 rounded-lg px-3 py-1.5 text-left transition-colors sm:grid-cols-[120px_52px_60px_minmax(0,1fr)_80px]",
                  open === h.id ? "bg-primary/10 ring-1 ring-primary/30" : "hover:bg-white/[0.04]",
                )}
              >
                <span className="num text-[11px] text-muted-foreground">{formatTime(h.end_time)}</span>
                <span className="text-[10px] font-bold text-muted-foreground">{h.position ?? "—"}</span>
                <span className="flex gap-0.5">
                  {h.hole_cards ? h.hole_cards.map((c) => <MiniCard key={c} code={c} />) : <span className="text-xs text-muted-foreground">—</span>}
                </span>
                <span className="flex min-w-0 gap-0.5" aria-label="公共牌">
                  {h.board.length === 0 ? (
                    <span className="text-[10px] text-muted-foreground">翻牌前结束</span>
                  ) : (
                    h.board.map((c) => <MiniCard key={c} code={c} size="xs" />)
                  )}
                </span>
                <span className={cn("num text-right text-sm font-bold", netClass(h.net))}>
                  {formatNet(h.net)}
                  {h.showed && <span className="ml-1 align-middle text-[9px] font-normal text-muted-foreground">摊牌</span>}
                </span>
              </button>
              {open === h.id && <HandDetail id={h.id} me={me} />}
            </li>
          ))}
        </ul>
      )}
      {list.length > shown && (
        <div className="mt-3 text-center">
          <button
            type="button"
            onClick={() => setShown(shown + PAGE)}
            className="rounded-md border border-primary/40 px-4 py-1.5 text-xs text-primary hover:bg-primary/10"
          >
            再看 {Math.min(PAGE, list.length - shown)} 手
          </button>
        </div>
      )}
    </Card>
  )
}

/** 展开一手:按 id 取这一手的完整记录(游标 before=id+1、limit=1 正好是它)。 */
function HandDetail({ id, me }: { id: number; me: string }) {
  const [hand, setHand] = useState<HandRecord | null | "error">(null)
  useEffect(() => {
    let alive = true
    fetchHands({ user: me, before: id + 1, limit: 1 })
      .then((list) => alive && setHand(list.find((h) => h.id === id) ?? "error"))
      .catch(() => alive && setHand("error"))
    return () => {
      alive = false
    }
  }, [id, me])
  if (hand === null) return <p className="px-3 py-2 text-xs text-muted-foreground">加载中…</p>
  if (hand === "error") return <p className="px-3 py-2 text-xs text-destructive">这手牌加载失败。</p>
  return (
    <div className="px-1 pb-2 pt-1">
      <HandDetailCard hand={hand} me={me} />
    </div>
  )
}

function pct(x: number | null): string {
  return x === null ? "—" : `${Math.round(x * 100)}%`
}
