import { describe, expect, it } from 'vitest'
import {
  clearance,
  measureLayout,
  overlapArea,
  segmentsCross,
  type MetricRect,
} from './layoutMetrics'

const box = (id: string, x: number, y: number, w = 100, h = 100): MetricRect => ({ id, x, y, w, h })

describe('overlapArea', () => {
  it('is the shared area', () => {
    expect(overlapArea(box('a', 0, 0), box('b', 50, 50))).toBe(2500)
  })

  it('is zero for cards that only touch', () => {
    expect(overlapArea(box('a', 0, 0), box('b', 100, 0))).toBe(0)
  })

  it('is zero for cards that are apart', () => {
    expect(overlapArea(box('a', 0, 0), box('b', 200, 200))).toBe(0)
  })
})

describe('clearance', () => {
  it('is zero when two cards overlap', () => {
    expect(clearance(box('a', 0, 0), box('b', 50, 50))).toBe(0)
  })

  it('is the axis gap when cards face each other', () => {
    expect(clearance(box('a', 0, 0), box('b', 140, 0))).toBe(40)
    expect(clearance(box('a', 0, 0), box('b', 0, 130))).toBe(30)
  })

  it('is the corner distance when cards sit diagonally', () => {
    expect(clearance(box('a', 0, 0), box('b', 130, 140))).toBeCloseTo(50, 6)
  })
})

describe('segmentsCross', () => {
  const p = (x: number, y: number) => ({ x, y })

  it('finds a proper crossing', () => {
    expect(segmentsCross(p(0, 0), p(10, 10), p(0, 10), p(10, 0))).toBe(true)
  })

  it('does not count segments that miss', () => {
    expect(segmentsCross(p(0, 0), p(1, 1), p(5, 5), p(6, 6))).toBe(false)
  })

  it('does not count a shared endpoint as a crossing', () => {
    expect(segmentsCross(p(0, 0), p(10, 10), p(0, 0), p(10, -10))).toBe(false)
  })

  it('does not count collinear overlap', () => {
    expect(segmentsCross(p(0, 0), p(10, 0), p(5, 0), p(15, 0))).toBe(false)
  })
})

describe('measureLayout', () => {
  it('counts overlapping pairs and the pairs under the gutter', () => {
    const rects = [box('a', 0, 0), box('b', 50, 50), box('c', 400, 400)]
    const m = measureLayout(rects, [], 20)
    expect(m.overlaps).toBe(1)
    expect(m.worstOverlapArea).toBe(2500)
    expect(m.minClearance).toBe(0)
    expect(m.pairsUnderGutter).toBe(1)
  })

  it('reports a clean layout as clean', () => {
    const rects = [box('a', 0, 0), box('b', 130, 0), box('c', 260, 0)]
    const m = measureLayout(rects, [], 30)
    expect(m.overlaps).toBe(0)
    expect(m.minClearance).toBe(30)
    expect(m.pairsUnderGutter).toBe(0)
  })

  it('measures edges centre to centre and ignores ones it cannot place', () => {
    const rects = [box('a', 0, 0), box('b', 300, 0)]
    const m = measureLayout(rects, [{ from: 'a', to: 'b' }, { from: 'a', to: 'gone' }], 10)
    expect(m.edges).toBe(1)
    expect(m.totalEdgeLength).toBe(300)
    expect(m.meanEdgeLength).toBe(300)
    expect(m.longestEdge).toBe(300)
  })

  it('counts a crossing between two relationships that share no table', () => {
    const rects = [box('a', 0, 0), box('b', 300, 300), box('c', 0, 300), box('d', 300, 0)]
    const m = measureLayout(
      rects,
      [
        { from: 'a', to: 'b' },
        { from: 'c', to: 'd' },
      ],
      10,
    )
    expect(m.crossings).toBe(1)
  })

  it('does not count relationships that meet at a table', () => {
    const rects = [box('hub', 0, 0), box('a', 300, 0), box('b', -300, 0)]
    const m = measureLayout(
      rects,
      [
        { from: 'hub', to: 'a' },
        { from: 'hub', to: 'b' },
      ],
      10,
    )
    expect(m.crossings).toBe(0)
  })

  it('reports the bounding box', () => {
    const m = measureLayout([box('a', 0, 0), box('b', 300, 200)], [], 10)
    expect(m.width).toBe(400)
    expect(m.height).toBe(300)
  })

  it('survives an empty layout', () => {
    const m = measureLayout([], [], 10)
    expect(m.cards).toBe(0)
    expect(m.overlaps).toBe(0)
    expect(m.minClearance).toBe(0)
  })
})
