// 全局私聊抽屉。大厅和牌桌**挂同一个组件、同一个位置**——位置固定,用户才不会把它和牌桌页里的房间聊天混起来。
//
// 两者的区别就是这个组件存在的理由:房聊不落库、不在场就是错过,所以没有「未读」;
// 私聊落库、跨房间、有未读有已读。因此「未读徽标」在这套 UI 里是私聊的专属标识,房聊不该有。
//
// 纯展示层:只读 store 快照、只调 store 动作,不碰 WebSocket。

'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { conversationList, getConversation, isReadByPeer, markRead, sendDm, totalUnread, type DMConversation, type DMMessage } from '@/store/dm'
import { useDm } from '@/store/useDm'
import { useRoom } from '@/store/useRoom'
import { cn } from '@/lib/utils'

export interface DmDrawerProps {
  /**
   * 让外部把抽屉直接开到某个对端(比如牌桌上点某个玩家 →「私聊」)。
   * 值每变一次就开一次,所以调用方要么每次给新值,要么用完置回 null。
   */
  openPeer?: string | null
  className?: string
}

export default function DmDrawer({ openPeer, className }: DmDrawerProps) {
  const dm = useDm()
  const room = useRoom()

  const [open, setOpen] = useState(false)
  const [peer, setPeer] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [newPeer, setNewPeer] = useState('')
  const [composing, setComposing] = useState(false)
  /** 本地发送失败(ws 没连上,sendDm 会抛)。服务器侧的拒绝走 room.lastError,不在这里显示。 */
  const [sendError, setSendError] = useState<string | null>(null)

  const conversations = useMemo(() => conversationList(dm), [dm])
  const unreadAll = useMemo(() => totalUnread(dm), [dm])
  // 新建的会话在收到/发出第一条消息前不在 store 里,所以这里允许 conv 为 null 而 peer 已选中。
  const conv = peer ? getConversation(peer, dm) : null

  // 外部指定对端:开抽屉并切到该会话。
  useEffect(() => {
    if (!openPeer) return
    setOpen(true)
    setPeer(openPeer)
    setDraft('')
  }, [openPeer])

  /**
   * 打开会话即已读。依赖里带上 unread 与最后一条消息的 id:抽屉开着时对方又发新消息,
   * 也要顺手把游标推上去,否则关掉抽屉才发现红点还在。
   */
  const lastIncomingId = conv ? lastIncoming(conv)?.id : undefined
  useEffect(() => {
    if (!open || !peer || !conv || conv.unread === 0) return
    try {
      markRead(peer)
    } catch {
      // ws 断了标不了已读。不提示:这不是用户发起的动作,重连后再进会话会补标。
    }
  }, [open, peer, conv, lastIncomingId])

  // 新消息到达/切换会话时滚到底。
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [peer, conv?.messages.length, open])

  // Esc 关抽屉。只在开着时挂,免得白占一个全局键。
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const selfDm = peer !== null && room.me !== null && peer === room.me
  const canSend = peer !== null && draft.trim().length > 0 && !selfDm

  const handleSend = () => {
    if (!canSend || peer === null) return
    const text = draft.trim()
    try {
      sendDm(peer, text)
      setDraft('')
      setSendError(null)
    } catch {
      // sendDm 在 ws 未连时抛且**不**落本地消息,所以这里必须显式说出来,不能静默吞掉。
      setSendError('未连接到服务器,这条没有发出去。')
    }
  }

  const handleStartNew = () => {
    const nick = newPeer.trim()
    if (!nick) return
    setPeer(nick)
    setNewPeer('')
    setDraft('')
    setSendError(null)
  }

  // ── 收起态:一个带未读数的小浮标,尽量少占地方 ──
  if (!open) {
    return (
      <button
        type="button"
        aria-label={unreadAll > 0 ? `打开私聊,${unreadAll} 条未读` : '打开私聊'}
        onClick={() => setOpen(true)}
        className={cn(
          'fixed bottom-6 right-6 z-40 flex h-12 w-12 items-center justify-center rounded-full',
          'border-2 border-primary/50 bg-card/95 text-lg text-primary shadow-xl',
          'transition-colors hover:bg-primary/15',
          className,
        )}
      >
        <span aria-hidden>✉</span>
        {unreadAll > 0 && <UnreadBadge count={unreadAll} className="absolute -right-1 -top-1" />}
      </button>
    )
  }

  // ── 展开态 ──
  return (
    <aside
      aria-label="私聊"
      className={cn(
        // 宽度上限锁在屏幕 1/3(用户明确担心占地方);窄屏 1/3 没法用字,退回近似满宽。
        'fixed inset-y-0 right-0 z-40 flex w-[92vw] flex-col border-l-2 border-primary/30 bg-card/98 shadow-2xl backdrop-blur',
        'sm:w-[33vw] sm:min-w-[340px] sm:max-w-[440px]',
        className,
      )}
    >
      <header className="flex items-center justify-between border-b border-primary/20 px-4 py-3">
        <div>
          <p className="text-[10px] uppercase tracking-[0.25em] text-muted-foreground">Direct Messages</p>
          <p className="flex items-center gap-2 text-base font-semibold text-primary">
            私聊
            {unreadAll > 0 && <UnreadBadge count={unreadAll} />}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          aria-label="关闭私聊"
          className="border-primary/40 bg-card/60 text-xs hover:bg-primary/10"
          onClick={() => setOpen(false)}
        >
          收起
        </Button>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* 左:会话列表 */}
        <div className="flex w-32 shrink-0 flex-col border-r border-primary/20 sm:w-36">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {conversations.length === 0 && peer === null && (
              <p className="px-3 py-4 text-[11px] leading-relaxed text-muted-foreground">还没有私聊。输入昵称开一个。</p>
            )}
            {/* 新开的会话还没进 store,单独顶一行,否则选中了却在列表里看不见。 */}
            {peer !== null && !dm.conversations.has(peer) && (
              <ConversationRow peer={peer} preview="新会话" unread={0} active onClick={() => undefined} />
            )}
            {conversations.map((c) => (
              <ConversationRow
                key={c.peer}
                peer={c.peer}
                preview={c.messages[c.messages.length - 1]?.text ?? ''}
                unread={c.unread}
                active={c.peer === peer}
                onClick={() => {
                  setPeer(c.peer)
                  setDraft('')
                  setSendError(null)
                }}
              />
            ))}
          </div>

          <div className="border-t border-primary/20 p-2">
            <input
              aria-label="新私聊对象昵称"
              value={newPeer}
              onChange={(e) => setNewPeer(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleStartNew()
              }}
              placeholder="昵称…"
              className="h-7 w-full rounded border border-primary/40 bg-black/30 px-2 text-[11px] outline-none focus:border-primary"
            />
          </div>
        </div>

        {/* 右:消息 + 输入 */}
        <div className="flex min-w-0 flex-1 flex-col">
          {peer === null ? (
            <div className="flex flex-1 items-center justify-center px-4 text-center text-xs text-muted-foreground">
              选一个会话,或在左下角输入昵称开始私聊。
            </div>
          ) : (
            <>
              <div className="border-b border-primary/20 px-3 py-2 text-sm font-semibold text-primary">{peer}</div>

              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3">
                {(conv?.messages.length ?? 0) === 0 && (
                  <p className="text-[11px] text-muted-foreground">还没有消息。</p>
                )}
                {conv?.messages.map((m) => (
                  <Bubble key={m.id} msg={m} read={isReadByPeer(conv, m)} />
                ))}
                <div ref={bottomRef} />
              </div>

              {selfDm && <p className="px-3 pb-1 text-[11px] text-destructive">不能给自己发私信。</p>}
              {sendError && <p className="px-3 pb-1 text-[11px] text-destructive">{sendError}</p>}

              <div className="flex items-center gap-2 border-t border-primary/20 p-2">
                <input
                  aria-label="私聊输入"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onCompositionStart={() => setComposing(true)}
                  onCompositionEnd={() => setComposing(false)}
                  onKeyDown={(e) => {
                    // 输入法组字中的回车是「选词」,不是「发送」——不挡会把半截拼音发出去。
                    if (e.key === 'Enter' && !e.shiftKey && !composing) {
                      e.preventDefault()
                      handleSend()
                    }
                  }}
                  placeholder={`发给 ${peer}…`}
                  className="h-8 min-w-0 flex-1 rounded border border-primary/40 bg-black/30 px-2 text-xs outline-none focus:border-primary"
                />
                <Button
                  size="sm"
                  disabled={!canSend}
                  onClick={handleSend}
                  className="bg-primary text-primary-foreground hover:bg-primary/90"
                >
                  发送
                </Button>
              </div>
            </>
          )}
        </div>
      </div>
    </aside>
  )
}

function UnreadBadge({ count, className }: { count: number; className?: string }) {
  return (
    <span
      className={cn(
        'flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-bold leading-none text-destructive-foreground',
        className,
      )}
    >
      {count > 99 ? '99+' : count}
    </span>
  )
}

function ConversationRow({
  peer,
  preview,
  unread,
  active,
  onClick,
}: {
  peer: string
  preview: string
  unread: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex w-full flex-col gap-0.5 border-b border-primary/10 px-2 py-2 text-left transition-colors hover:bg-primary/10',
        active && 'bg-primary/15',
      )}
    >
      <span className="flex items-center justify-between gap-1">
        <span className="truncate text-xs font-semibold text-card-foreground">{peer}</span>
        {unread > 0 && <UnreadBadge count={unread} />}
      </span>
      <span className="truncate text-[10px] text-muted-foreground">{preview}</span>
    </button>
  )
}

function Bubble({ msg, read }: { msg: DMMessage; read: boolean }) {
  return (
    <div className={cn('flex flex-col', msg.mine ? 'items-end' : 'items-start')}>
      <div
        className={cn(
          'max-w-[85%] whitespace-pre-wrap break-words rounded-lg px-2.5 py-1.5 text-xs',
          msg.mine ? 'bg-primary/25 text-card-foreground' : 'bg-secondary/60 text-secondary-foreground',
          // 投递失败要一眼看出来,不能只靠下面那行小字。
          msg.undelivered && 'border border-destructive/70 bg-destructive/15',
        )}
      >
        {msg.text}
      </div>
      <span className="mt-0.5 text-[10px] text-muted-foreground">
        {msg.undelivered ? (
          <span className="text-destructive">未送达 · 查无此人</span>
        ) : (
          <>
            {formatTime(msg.createdAt)}
            {read && ' · 已读'}
          </>
        )}
      </span>
    </div>
  )
}

/** 只显示时分:私聊面板窄,日期挤不下,也基本用不上。 */
function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function lastIncoming(conv: DMConversation): DMMessage | undefined {
  for (let i = conv.messages.length - 1; i >= 0; i -= 1) {
    if (!conv.messages[i].mine) return conv.messages[i]
  }
  return undefined
}
