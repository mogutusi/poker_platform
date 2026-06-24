// 聊天表情渲染(见 service/docs/messaging.md「表情」/ changes/0034-0035)。
// 后端纯透传:聊天文本里 [code] 是普通文本,前端按生成的 EMOJI_CATALOG 渲染已知 code,未知原样保留。
// 目录是单一事实源(后端 app/wire/emoji.py → codegen),本文件只消费,绝不手写表情集。

import { EMOJI_CATALOG, type EmojiCode, type EmojiMeta } from '@/types/wire.gen'

// 令牌正则:[code],code 限 [a-z0-9_]+(与后端 EmojiCode 形制一致,见 test_emoji)。
const EMOJI_TOKEN = /\[([a-z0-9_]+)\]/g

function isKnown(code: string): code is EmojiCode {
  return Object.prototype.hasOwnProperty.call(EMOJI_CATALOG, code)
}

// 聊天文本切成「文本 / 表情」段,供 React 渲染:文本段原样,表情段带 meta(渲 glyph 或按 code 换自定义贴纸图)。
// 未知 [foo] 不切、留在文本段(绝不吞用户内容)。
export type ChatSegment =
  | { kind: 'text'; text: string }
  | { kind: 'emoji'; code: EmojiCode; meta: EmojiMeta }

export function tokenizeChat(text: string): ChatSegment[] {
  const segments: ChatSegment[] = []
  let last = 0
  for (const match of text.matchAll(EMOJI_TOKEN)) {
    const code = match[1]
    if (!isKnown(code)) continue // 未知 code:留作文本,下一段 text.slice 会带上
    const start = match.index ?? 0
    if (start > last) segments.push({ kind: 'text', text: text.slice(last, start) })
    segments.push({ kind: 'emoji', code, meta: EMOJI_CATALOG[code] })
    last = start + match[0].length
  }
  if (last < text.length) segments.push({ kind: 'text', text: text.slice(last) })
  return segments
}

// 便捷:纯文本渲染(已知 [code] 换成 Unicode 字形,未知原样)。需自定义贴纸图时改用 tokenizeChat。
export function chatToPlainText(text: string): string {
  return text.replace(EMOJI_TOKEN, (whole, code: string) =>
    isKnown(code) ? EMOJI_CATALOG[code].glyph : whole,
  )
}
