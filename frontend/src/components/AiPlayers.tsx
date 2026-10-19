"use client"

// "AI 对手" control on the table page (dev only): add a few bot players to this room so you can
// play a real hand on your own. The bots are scripts/bots.mjs, launched by /api/bots; they log in
// as dev accounts and play over the real protocol, so everything they do goes through the server.

import { useCallback, useEffect, useState } from "react"
import { cn } from "@/lib/utils"

const STYLES = [
  { id: "tight", label: "稳健", hint: "只玩好牌" },
  { id: "balanced", label: "均衡", hint: "正常打法" },
  { id: "loose", label: "松", hint: "爱看牌" },
  { id: "aggressive", label: "激进", hint: "常加注,会诈唬" },
] as const

interface BotStatus {
  running: boolean
  count: number
  style: string | null
}

export default function AiPlayers({ room, me, freeSeats }: { room: string; me: string | null; freeSeats: number }) {
  const [open, setOpen] = useState(false)
  const [count, setCount] = useState(3)
  const [style, setStyle] = useState<string>("balanced")
  const [status, setStatus] = useState<BotStatus>({ running: false, count: 0, style: null })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`/api/bots?room=${encodeURIComponent(room)}`)
      if (res.ok) setStatus(await res.json())
    } catch {
      /* dev server restarting: try again on the next tick */
    }
  }, [room])

  useEffect(() => {
    void refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [refresh])

  const add = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch("/api/bots", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ room, count, style, exclude: me ? [me] : [] }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) setError(data.error ?? `失败(${res.status})`)
      else {
        setStatus(data)
        setOpen(false)
      }
    } catch {
      setError("连不上前端开发服务器")
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      const res = await fetch(`/api/bots?room=${encodeURIComponent(room)}`, { method: "DELETE" })
      if (res.ok) setStatus(await res.json())
    } finally {
      setBusy(false)
    }
  }

  const maxCount = Math.max(1, Math.min(8, freeSeats))

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(
          "rounded-lg border px-3 py-1.5 text-xs",
          status.running ? "border-call/60 bg-call/15 text-call" : "border-line bg-panel text-ink hover:border-gold/60",
        )}
      >
        {status.running ? `AI 对手 ×${status.count}` : "AI 对手"}
      </button>

      {open && (
        <div className="panel absolute right-0 top-11 z-50 w-72 p-4 text-sm">
          <div className="mb-3 flex items-center justify-between">
            <p className="font-semibold text-ink">AI 对手</p>
            <button type="button" aria-label="关闭" onClick={() => setOpen(false)} className="px-1 text-lg leading-none text-muted-ink hover:text-ink">
              ×
            </button>
          </div>

          {status.running ? (
            <>
              <p className="text-muted-ink">
                这桌有 {status.count} 个 AI 在打。它们准备好后会等你准备;一手结束后会自动开下一手。
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={remove}
                className="mt-3 w-full rounded-lg border border-fold/60 bg-fold/15 py-2 font-semibold text-[#ff9aa2] disabled:opacity-50"
              >
                让 AI 离桌
              </button>
            </>
          ) : (
            <>
              <p className="mb-1.5 text-xs text-muted-ink">人数(空座 {freeSeats})</p>
              <div className="flex gap-1">
                {Array.from({ length: maxCount }, (_, i) => i + 1).map((n) => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setCount(n)}
                    className={cn(
                      "h-8 flex-1 rounded-md border text-sm",
                      count === n ? "border-gold bg-gold/15 text-gold" : "border-line text-ink hover:border-gold/50",
                    )}
                  >
                    {n}
                  </button>
                ))}
              </div>

              <p className="mb-1.5 mt-3 text-xs text-muted-ink">风格</p>
              <div className="grid grid-cols-2 gap-1">
                {STYLES.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => setStyle(s.id)}
                    title={s.hint}
                    className={cn(
                      "rounded-md border px-2 py-1.5 text-left",
                      style === s.id ? "border-gold bg-gold/15 text-gold" : "border-line text-ink hover:border-gold/50",
                    )}
                  >
                    <span className="font-semibold">{s.label}</span>
                    <span className="ml-1.5 text-[11px] text-muted-ink">{s.hint}</span>
                  </button>
                ))}
              </div>

              <button
                type="button"
                disabled={busy || freeSeats === 0}
                onClick={add}
                className="mt-4 w-full rounded-lg bg-gold py-2 font-semibold text-primary-foreground hover:brightness-110 disabled:opacity-50"
              >
                {busy ? "加入中…" : `加入 ${Math.min(count, maxCount)} 个 AI`}
              </button>
              <p className="mt-2 text-[11px] leading-relaxed text-muted-ink">
                AI 用 dev 账号登录(不会用你自己的账号),入座、买入、准备都自动完成。你准备好后点 Start Game。
              </p>
            </>
          )}
          {error && <p className="mt-2 text-xs text-[#ff9aa2]">{error}</p>}
        </div>
      )}
    </div>
  )
}
