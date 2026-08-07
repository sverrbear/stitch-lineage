import { describe, expect, it } from 'vitest'
import { CLICK_SLOP_PX, isClickNotDrag } from './canvas'

describe('isClickNotDrag', () => {
  it('treats a stationary press-release as a click', () => {
    expect(isClickNotDrag({ x: 100, y: 100 }, { x: 100, y: 100 })).toBe(true)
  })

  it('tolerates hand jitter within the slop', () => {
    expect(isClickNotDrag({ x: 100, y: 100 }, { x: 102, y: 101 })).toBe(true)
    expect(isClickNotDrag({ x: 100, y: 100 }, { x: 100 + CLICK_SLOP_PX, y: 100 })).toBe(true)
  })

  it('treats a press that travelled beyond the slop as a drag', () => {
    expect(isClickNotDrag({ x: 100, y: 100 }, { x: 100 + CLICK_SLOP_PX + 1, y: 100 })).toBe(false)
    expect(isClickNotDrag({ x: 100, y: 100 }, { x: 140, y: 260 })).toBe(false)
  })

  it('honours a caller-supplied slop', () => {
    expect(isClickNotDrag({ x: 0, y: 0 }, { x: 10, y: 0 }, 12)).toBe(true)
    expect(isClickNotDrag({ x: 0, y: 0 }, { x: 10, y: 0 }, 8)).toBe(false)
  })

  it('treats a click with no recorded press origin (keyboard) as a click', () => {
    expect(isClickNotDrag(null, { x: 0, y: 0 })).toBe(true)
  })
})
