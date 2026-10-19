"use client"

// Room (table) chat.
//
// Everything was already wired below the UI: the server relays `room_chat` to everyone in the room
// (spectators included), guards length and rate in the shell, and keeps the last
// ROOM_CHAT_HISTORY_SIZE lines per room; the store keeps `state.chat`. This is the missing view.
//
// Placement follows the note left on the table page: room chat lives on the table's left edge,
// never a right-side drawer. History dies with the room (BACKEND_GUIDE §5), so there is no
// "unread" state to track: if you weren't in the room, you missed it.
//
// Emoji are plain `[code]` tokens in the text (wire.gen EMOJI_CATALOG); the server never parses
// them. Rejections (MESSAGE_TOO_LONG / RATE_LIMITED) come back as the page's normal error pill.

import { useEffect, useRef, useState } from "react"
import { useRoom } from "@/store/useRoom"
import { fetchChatHistory, sendChat } from "@/store/actions"
import { tokenizeChat } from "@/utils/emoji"
import { EMOJI_CATALOG, type EmojiCode } from "@/types/wire.gen"
import { cn } from "@/lib/utils"

/** Server default ROOM_CHAT_MAX_TEXT_LEN (service/app/poker.env.example). The server is the guard;
 *  this only stops the box from accepting text that would certainly be rejected. */
const MAX_LEN = 500

function ChatText({ text }: { text: string }) {
  return (
    <>
      {tokenizeChat(text).map((seg, i) =>
        seg.kind === "text" ? (
          <span key={i}>{seg.text}</span>
        ) : (
          <span key={i} title={seg.meta.label} className="mx-px text-base leading-none">
            {seg.meta.glyph}
          </span>
        ),
      )}
    </>
  )
}

export default function TableChat({ className }: { className?: string }) {
  const state = useRoom()
  const [draft, setDraft] = useState("")
  const [showEmoji, setShowEmoji] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)

  // Load the room's recent lines once we're in (and again if we land in a different room).
  useEffect(() => {
    if (state.room) fetchChatHistory()
  }, [state.room])

  // Keep the newest line in view.
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state.chat.length])

  const send = () => {
    const text = draft.trim()
    if (!text || !state.room) return
    sendChat(text)
    setDraft("")
    setShowEmoji(false)
  }

  const addEmoji = (code: EmojiCode) => setDraft((d) => (d + `[${code}]`).slice(0, MAX_LEN))

  return (
    <section aria-label="牌桌聊天" className={cn("panel flex min-h-0 flex-col overflow-hidden", className)}>
      <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h2 className="font-condensed text-lg font-semibold text-ink">牌桌聊天</h2>
        <span className="text-xs text-muted-ink">只有这一桌的人能看到</span>
      </header>

      <div ref={listRef} className="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-4 py-3 text-sm" aria-live="polite">
        {state.chat.length === 0 ? (
          <p className="pt-6 text-center text-xs text-muted-ink">还没有人说话。打个招呼?</p>
        ) : (
          state.chat.map((m, i) => (
            <p key={i} className="break-words leading-snug">
              <span className={cn("mr-1.5 font-semibold", m.from_nick === state.me ? "text-gold" : "text-[#8fb7ff]")}>
                {m.from_nick}
              </span>
              <span className="text-ink">
                <ChatText text={m.text} />
              </span>
            </p>
          ))
        )}
      </div>

      {showEmoji && (
        <div className="grid grid-cols-6 gap-1 border-t border-line px-3 py-2">
          {(Object.keys(EMOJI_CATALOG) as EmojiCode[]).map((code) => (
            <button
              key={code}
              type="button"
              title={EMOJI_CATALOG[code].label}
              onClick={() => addEmoji(code)}
              className="rounded-md py-1 text-lg hover:bg-white/10"
            >
              {EMOJI_CATALOG[code].glyph}
            </button>
          ))}
        </div>
      )}

      <form
        className="flex items-center gap-1.5 border-t border-line p-2"
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
      >
        <button
          type="button"
          aria-label="表情"
          aria-expanded={showEmoji}
          onClick={() => setShowEmoji((v) => !v)}
          className={cn("h-9 w-9 shrink-0 rounded-lg text-lg hover:bg-white/10", showEmoji && "bg-white/10")}
        >
          😊
        </button>
        <input
          aria-label="聊天输入"
          value={draft}
          maxLength={MAX_LEN}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={state.room ? "说点什么…" : "进房后才能聊天"}
          disabled={!state.room}
          className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-room-deep/80 px-3 text-sm text-ink placeholder:text-muted-ink/70 focus:border-gold focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!draft.trim() || !state.room}
          className="h-9 shrink-0 rounded-lg bg-gold px-3 text-sm font-semibold text-[#1a1405] hover:brightness-110 disabled:opacity-40"
        >
          发送
        </button>
      </form>
    </section>
  )
}
