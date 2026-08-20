// 文案表必须覆盖协议里的每一个 ErrorCode:漏一个,用户就会看到 NOT_ENOUGH_PLAYERS 这种机器码。
// 这条测试的价值在于后端**新增**错误码时会立刻红,而不是等用户在界面上撞见。

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { errorText } from './errorText'

/** 从 codegen 产物里抽出 ErrorCode 的全部取值,而不是在测试里再抄一份。 */
function allErrorCodes(): string[] {
  const src = readFileSync(new URL('../types/wire.gen.ts', import.meta.url), 'utf8')
  const line = src.split('\n').find((l) => l.startsWith('export type ErrorCode'))
  if (!line) throw new Error('wire.gen.ts 里找不到 ErrorCode')
  return [...line.matchAll(/"([A-Z_]+)"/g)].map((m) => m[1])
}

describe('错误码文案', () => {
  const codes = allErrorCodes()

  it('协议里的每个码都有中文文案', () => {
    expect(codes.length).toBeGreaterThan(0)
    const missing = codes.filter((c) => errorText(c as never) === c)
    expect(missing, `这些码没有文案,会把机器码露给用户:${missing.join(', ')}`).toEqual([])
  })

  it('文案不是机器码的复制', () => {
    for (const c of codes) {
      expect(errorText(c as never)).not.toMatch(/^[A-Z_]+$/)
    }
  })

  it('遇到未收录的码退回显示原码,而不是空白', () => {
    expect(errorText('SOMETHING_NEW' as never)).toBe('SOMETHING_NEW')
  })
})
