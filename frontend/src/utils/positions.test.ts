import { describe, expect, it } from 'vitest'
import { positionNames } from './positions'

describe('positionNames', () => {
  it('heads-up: button is also the small blind', () => {
    const m = positionNames([2, 7], 7, 9)
    expect(m.get(7)).toBe('BTN/SB')
    expect(m.get(2)).toBe('BB')
  })

  it('full 9-handed table, wrapping past seat 0', () => {
    const m = positionNames([0, 1, 2, 3, 4, 5, 6, 7, 8], 6, 9)
    expect([6, 7, 8, 0, 1, 2, 3, 4, 5].map((s) => m.get(s))).toEqual([
      'BTN', 'SB', 'BB', 'UTG', 'UTG+1', 'UTG+2', 'LJ', 'HJ', 'CO',
    ])
  })

  it('6-handed with gaps uses only the dealt-in seats', () => {
    const m = positionNames([0, 2, 3, 5, 6, 8], 3, 9)
    expect([3, 5, 6, 8, 0, 2].map((s) => m.get(s))).toEqual(['BTN', 'SB', 'BB', 'UTG', 'HJ', 'CO'])
    expect(m.has(1)).toBe(false)
  })

  it('3-handed has no middle positions', () => {
    const m = positionNames([1, 4, 8], 4, 9)
    expect([4, 8, 1].map((s) => m.get(s))).toEqual(['BTN', 'SB', 'BB'])
  })
})
