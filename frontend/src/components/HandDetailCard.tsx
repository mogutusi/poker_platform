"use client"

// One finished hand in full: room, time, blinds, pot, the board, and every player with avatar,
// position, net result and their cards — your own, or anyone's that were shown at showdown.
// Other players' unshown cards never reach the browser (the server leaves them out), so they're
// drawn face-down. Used by the Stats page when a hand-history row is expanded.

import { Card } from "@/components/ui/card"
import Avatar from "@/components/Avatar"
import MiniCard from "@/components/MiniCard"
import { handRoom, type HandParticipant, type HandRecord } from "@/transport/rest"
import { positionNames } from "@/utils/positions"

export function HandDetailCard({ hand, me }: { hand: HandRecord; me: string | null }) {
  const mine = me ? hand.participants.find((p) => p.nickname === me) : undefined
  // 位置(BTN / SB / BB …)按这手发到牌的座位和庄位推,和牌桌上同一份规则(utils/positions.ts)
  const seats = hand.participants.map((p) => p.seat_position).filter((s): s is number => s !== null)
  const positions =
    hand.button_position !== null && seats.length === hand.participants.length
      ? positionNames(seats, hand.button_position, Math.max(9, ...seats.map((s) => s + 1)))
      : new Map<number, string>()
  // 赢家排前面,其余按盈亏
  const players = [...hand.participants].sort((a, b) => b.net - a.net)

  return (
    <Card data-testid="hand-row" className="border-2 border-primary/20 bg-card/95 p-3 shadow-lg">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate rounded-full bg-secondary/60 px-2.5 py-0.5 text-xs text-secondary-foreground">
              {handRoom(hand)}
            </span>
            <span className="text-[11px] text-muted-foreground">{formatTime(hand.end_time)}</span>
            <span className="text-[11px] text-muted-foreground">{formatDuration(hand.start_time, hand.end_time)}</span>
            {hand.big_blind !== null && (
              <span className="text-[11px] text-muted-foreground">
                {hand.big_blind / 2}/{hand.big_blind}
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            底池 <span className="text-sm font-semibold text-primary">{hand.final_pot.toLocaleString()}</span>
          </p>
        </div>

        {/* 公共牌:5 个小槽位,没发到的街留空 */}
        <div className="flex items-center gap-1" data-testid="hand-board" aria-label="公共牌">
          {[0, 1, 2, 3, 4].map((i) =>
            hand.board[i] ? (
              <MiniCard key={i} code={hand.board[i]} />
            ) : (
              <span key={i} className="h-[30px] w-[22px] rounded-[4px] border border-dashed border-white/10" />
            ),
          )}
        </div>

        <div className="w-20 shrink-0 text-right">
          <p className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">{mine ? "我的盈亏" : "未参与"}</p>
          <p className={"text-xl font-bold " + netClass(mine?.net)}>{mine ? formatNet(mine.net) : "—"}</p>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5 border-t border-border/60 pt-2">
        {players.map((p) => (
          <PlayerChip
            key={p.nickname}
            p={p}
            isMe={p.nickname === me}
            position={p.seat_position !== null ? positions.get(p.seat_position) : undefined}
          />
        ))}
      </div>
    </Card>
  )
}

function PlayerChip({ p, isMe, position }: { p: HandParticipant; isMe: boolean; position?: string }) {
  return (
    <span
      data-testid="hand-player"
      className={
        "flex items-center gap-1.5 rounded-lg py-1 pl-1 pr-2.5 text-[11px] " +
        (isMe ? "bg-primary/15 ring-1 ring-primary/50" : p.net > 0 ? "bg-emerald-500/10 ring-1 ring-emerald-500/30" : "bg-secondary/40")
      }
    >
      <Avatar name={p.nickname} size={24} className="h-6 w-6 shrink-0 rounded-full" />
      <span className="flex flex-col leading-tight">
        <span className={isMe ? "font-semibold text-primary" : "text-card-foreground"}>{p.nickname}</span>
        {position && <span className="text-[9px] font-bold tracking-wide text-muted-foreground">{position}</span>}
      </span>
      {/* 底牌:自己的,或摊牌亮过的;其余扣着(服务器根本不给) */}
      <span className="flex gap-0.5">
        {p.hole_cards ? (
          p.hole_cards.map((c) => <MiniCard key={c} code={c} />)
        ) : p.seat_position !== null ? (
          <>
            <MiniCard hidden />
            <MiniCard hidden />
          </>
        ) : null}
      </span>
      <span className={"num font-semibold " + netClass(p.net)}>{formatNet(p.net)}</span>
      {p.showed && <span className="rounded bg-white/10 px-1 text-[9px] text-muted-foreground">摊牌</span>}
    </span>
  )
}

export function netClass(net: number | undefined): string {
  if (net === undefined || net === 0) return "text-muted-foreground"
  return net > 0 ? "text-emerald-400" : "text-destructive"
}

export function formatNet(net: number): string {
  return (net > 0 ? "+" : "") + net.toLocaleString()
}

const pad = (n: number) => String(n).padStart(2, "0")

/** 后端盖的是 ISO 墙钟串;这里手动格式化,不用 toLocaleString,免得服务端/客户端 locale 不一致。 */
export function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatDuration(start: string, end: string): string {
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (!Number.isFinite(ms) || ms < 0) return ""
  const sec = Math.round(ms / 1000)
  return sec < 60 ? `${sec}s` : `${Math.floor(sec / 60)}m${pad(sec % 60)}s`
}
