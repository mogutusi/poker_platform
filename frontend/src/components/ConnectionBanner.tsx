'use client'

// 连接状态横幅。
//
// 断线时如果什么都不显示,用户看到的是一张不动的桌子——分不清是自己网断了、别人在长考、
// 还是程序卡死了。这条信息 store 里一直有(room.connection),只是此前没接到界面上。
//
// 只在「不正常」的时候出现:连上了就完全不占地方。

import { useRoom } from '@/store/useRoom'

/** 每种状态说一句人话。'open' 不在表里——正常连接不该有横幅。 */
const NOTICE: Record<string, { text: string; tone: 'wait' | 'bad' }> = {
  connecting: { text: '正在连接服务器…', tone: 'wait' },
  reconnecting: { text: '连接断开,正在重连…你的座位和筹码会保留一段时间', tone: 'wait' },
  closed: { text: '连接已断开。可能是会话过期,或账号在别处登录', tone: 'bad' },
}

export default function ConnectionBanner() {
  const { connection } = useRoom()
  const notice = NOTICE[connection]
  // idle 表示还没开始连(比如大厅页),不是异常,不提示。
  if (!notice || connection === 'idle') return null

  return (
    <div
      role="status"
      aria-live="polite"
      className={[
        'fixed left-1/2 top-3 z-50 -translate-x-1/2 rounded-full border px-4 py-1.5 text-xs font-semibold shadow-lg backdrop-blur-sm',
        notice.tone === 'wait'
          ? 'border-amber-500/50 bg-amber-950/80 text-amber-200'
          : 'border-red-500/50 bg-red-950/85 text-red-200',
      ].join(' ')}
    >
      {notice.text}
    </div>
  )
}
