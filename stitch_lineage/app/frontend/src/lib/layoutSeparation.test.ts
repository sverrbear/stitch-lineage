import { describe, expect, it } from 'vitest'
import { clearance, type MetricRect } from './layoutMetrics'
import { relaxOverlaps, separateBoxes, type SeparationBox } from './layoutSeparation'

const GUTTER = 28

function boxes(
  spec: Array<[string, number, number, number?, number?]>,
): SeparationBox[] {
  return spec.map(([id, cx, cy, w, h]) => ({ id, cx, cy, w: w ?? 300, h: h ?? 200 }))
}

function rects(list: readonly SeparationBox[]): MetricRect[] {
  return list.map((b) => ({ id: b.id, x: b.cx - b.w / 2, y: b.cy - b.h / 2, w: b.w, h: b.h }))
}

/** The constraint, checked independently of how it was reached. */
function worstClearance(list: readonly SeparationBox[]): number {
  const all = rects(list)
  let worst = Infinity
  for (let i = 0; i < all.length; i++) {
    for (let j = i + 1; j < all.length; j++) worst = Math.min(worst, clearance(all[i], all[j]))
  }
  return worst === Infinity ? Infinity : worst
}

describe('separateBoxes — the hard constraint', () => {
  it('leaves a clean arrangement untouched', () => {
    const list = boxes([
      ['a', 0, 0],
      ['b', 400, 0],
    ])
    const result = separateBoxes(list, GUTTER)
    expect(list[0].cx).toBe(0)
    expect(list[1].cx).toBe(400)
    expect(result.cascaded).toEqual([])
  })

  it('separates two cards stacked exactly on top of each other', () => {
    const list = boxes([
      ['a', 0, 0],
      ['b', 0, 0],
    ])
    separateBoxes(list, GUTTER)
    expect(worstClearance(list)).toBeGreaterThanOrEqual(GUTTER - 1e-6)
  })

  it('separates a pile of twenty coincident cards', () => {
    const list = boxes(
      Array.from({ length: 20 }, (_, i) => [`n${String(i).padStart(2, '0')}`, 0, 0] as [string, number, number]),
    )
    separateBoxes(list, GUTTER)
    expect(worstClearance(list)).toBeGreaterThanOrEqual(GUTTER - 1e-6)
  })

  it('separates cards of wildly different measured sizes', () => {
    const list = boxes([
      ['tall', 0, 0, 240, 900],
      ['wide', 10, 10, 900, 120],
      ['small', 20, -20, 120, 90],
      ['huge', -5, 5, 700, 700],
    ])
    separateBoxes(list, GUTTER)
    expect(worstClearance(list)).toBeGreaterThanOrEqual(GUTTER - 1e-6)
  })

  it('honours a gutter of zero without reporting a violation', () => {
    const list = boxes([
      ['a', 0, 0],
      ['b', 0, 0],
    ])
    separateBoxes(list, 0)
    expect(worstClearance(list)).toBeGreaterThanOrEqual(-1e-6)
  })

  it('is deterministic whatever order the cards arrive in', () => {
    const spec: Array<[string, number, number]> = [
      ['a', 0, 0],
      ['b', 12, 8],
      ['c', -6, 14],
      ['d', 3, -9],
      ['e', 0, 0],
    ]
    const forward = boxes(spec)
    const backward = boxes([...spec].reverse())
    separateBoxes(forward, GUTTER)
    separateBoxes(backward, GUTTER)
    const key = (list: SeparationBox[]) =>
      [...list]
        .sort((a, b) => a.id.localeCompare(b.id))
        .map((b) => `${b.id}:${b.cx.toFixed(6)},${b.cy.toFixed(6)}`)
        .join('|')
    expect(key(backward)).toBe(key(forward))
  })

  it('still guarantees the constraint when relaxation is given no sweeps at all', () => {
    // the cascade alone is the guarantee: with zero relaxation it has to do all of it
    const list = boxes(
      Array.from({ length: 12 }, (_, i) => [`n${i}`, i * 4, i * 3] as [string, number, number]),
    )
    const result = separateBoxes(list, GUTTER, 0)
    expect(worstClearance(list)).toBeGreaterThanOrEqual(GUTTER - 1e-6)
    expect(result.cascaded.length).toBeGreaterThan(0)
  })
})

describe('relaxOverlaps', () => {
  it('reports zero penetration for a clean pair and moves nothing', () => {
    const list = boxes([
      ['a', 0, 0],
      ['b', 400, 0],
    ])
    expect(relaxOverlaps(list, GUTTER)).toBe(0)
    expect(list[0].cx).toBe(0)
  })

  it('pushes apart along the cheaper axis', () => {
    // a small horizontal overlap and a large vertical one: they should part sideways
    const list = boxes([
      ['a', 0, 0],
      ['b', 320, 10],
    ])
    relaxOverlaps(list, GUTTER)
    expect(list[0].cy).toBe(0)
    expect(list[1].cy).toBe(10)
    expect(list[1].cx - list[0].cx).toBeGreaterThan(320)
  })

  it('makes the heavier card yield less', () => {
    const heavy: SeparationBox = { id: 'hub', cx: 0, cy: 0, w: 300, h: 200, mass: 20 }
    const light: SeparationBox = { id: 'leaf', cx: 40, cy: 0, w: 300, h: 200, mass: 1 }
    relaxOverlaps([heavy, light], GUTTER)
    const moved = Math.hypot(heavy.cx, heavy.cy)
    expect(moved).toBeGreaterThan(0)
    expect(moved).toBeLessThan(Math.hypot(light.cx - 40, light.cy) / 10)
  })

  it('splits evenly when neither card carries more structure', () => {
    const a: SeparationBox = { id: 'a', cx: 0, cy: 0, w: 300, h: 200 }
    const b: SeparationBox = { id: 'b', cx: 40, cy: 0, w: 300, h: 200 }
    relaxOverlaps([a, b], GUTTER)
    expect(Math.hypot(a.cx, a.cy)).toBeCloseTo(Math.hypot(b.cx - 40, b.cy), 6)
  })
})
