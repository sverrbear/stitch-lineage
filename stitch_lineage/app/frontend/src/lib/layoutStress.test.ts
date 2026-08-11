import { describe, expect, it } from 'vitest'
import { stressLayout, type StressNode } from './layoutStress'

const W = 300
const H = 200

function nodes(...ids: string[]): StressNode[] {
  return ids.map((id) => ({ id, w: W, h: H }))
}

function distance(
  layout: Map<string, { x: number; y: number }>,
  a: string,
  b: string,
): number {
  const one = layout.get(a) as { x: number; y: number }
  const other = layout.get(b) as { x: number; y: number }
  return Math.hypot(one.x - other.x, one.y - other.y)
}

describe('stressLayout', () => {
  it('returns a centre for every node', () => {
    const layout = stressLayout(nodes('a', 'b', 'c'), [{ from: 'a', to: 'b' }], [['a', 'b', 'c']])
    expect([...layout.keys()].sort()).toEqual(['a', 'b', 'c'])
    for (const at of layout.values()) {
      expect(Number.isFinite(at.x) && Number.isFinite(at.y)).toBe(true)
    }
  })

  it('places a lone node at the origin', () => {
    expect(stressLayout(nodes('only'), [], [['only']]).get('only')).toEqual({ x: 0, y: 0 })
  })

  it('returns nothing for an empty component', () => {
    expect(stressLayout([], [], []).size).toBe(0)
  })

  it('lays a joined pair out side by side, not corner to corner', () => {
    // stress cannot tell a rotation from another rotation, so the orientation has to
    // be chosen; a pair stacked diagonally was what that gauge freedom cost before
    const layout = stressLayout(nodes('a', 'b'), [{ from: 'a', to: 'b' }], [['a', 'b']])
    const one = layout.get('a') as { x: number; y: number }
    const other = layout.get('b') as { x: number; y: number }
    expect(Math.abs(one.x - other.x)).toBeGreaterThan(Math.abs(one.y - other.y) * 4)
  })

  it('lays the widest direction of a chain along x', () => {
    const ids = ['a', 'b', 'c', 'd', 'e']
    const layout = stressLayout(
      nodes(...ids),
      [
        { from: 'a', to: 'b' },
        { from: 'b', to: 'c' },
        { from: 'c', to: 'd' },
        { from: 'd', to: 'e' },
      ],
      [ids],
    )
    const xs = ids.map((id) => (layout.get(id) as { x: number }).x)
    const ys = ids.map((id) => (layout.get(id) as { y: number }).y)
    expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(Math.max(...ys) - Math.min(...ys))
  })

  it('keeps the busiest table nearest the middle of its constellation', () => {
    const dims = Array.from({ length: 9 }, (_, i) => `dim_${i}`)
    const ids = ['fct', ...dims]
    const layout = stressLayout(
      nodes(...ids),
      dims.map((dim) => ({ from: 'fct', to: dim })),
      [ids],
    )
    const points = ids.map((id) => layout.get(id) as { x: number; y: number })
    const mid = {
      x: points.reduce((s, p) => s + p.x, 0) / points.length,
      y: points.reduce((s, p) => s + p.y, 0) / points.length,
    }
    const radius = (id: string) => {
      const at = layout.get(id) as { x: number; y: number }
      return Math.hypot(at.x - mid.x, at.y - mid.y)
    }
    for (const dim of dims) expect(radius('fct')).toBeLessThan(radius(dim))
  })

  it('pulls two communities apart while keeping each one together', () => {
    const ids = ['fct_a', 'a1', 'a2', 'fct_b', 'b1', 'b2']
    const layout = stressLayout(
      nodes(...ids),
      [
        { from: 'fct_a', to: 'a1' },
        { from: 'fct_a', to: 'a2' },
        { from: 'fct_b', to: 'b1' },
        { from: 'fct_b', to: 'b2' },
        { from: 'fct_a', to: 'fct_b' },
      ],
      [
        ['fct_a', 'a1', 'a2'],
        ['fct_b', 'b1', 'b2'],
      ],
    )
    for (const satellite of ['a1', 'a2']) {
      expect(distance(layout, satellite, 'fct_a')).toBeLessThan(distance(layout, satellite, 'fct_b'))
    }
    for (const satellite of ['b1', 'b2']) {
      expect(distance(layout, satellite, 'fct_b')).toBeLessThan(distance(layout, satellite, 'fct_a'))
    }
  })

  it('is deterministic, and independent of the order it is given', () => {
    const ids = ['a', 'b', 'c', 'd', 'e', 'f']
    const edges = [
      { from: 'a', to: 'b' },
      { from: 'a', to: 'c' },
      { from: 'c', to: 'd' },
      { from: 'd', to: 'e' },
      { from: 'a', to: 'f' },
    ]
    const first = stressLayout(nodes(...ids), edges, [ids])
    const again = stressLayout(nodes(...ids), edges, [ids])
    const reversed = stressLayout(nodes(...[...ids].reverse()), [...edges].reverse(), [
      [...ids].reverse(),
    ])
    for (const id of ids) {
      expect(again.get(id), id).toEqual(first.get(id))
      const other = reversed.get(id) as { x: number; y: number }
      const one = first.get(id) as { x: number; y: number }
      expect(other.x, id).toBeCloseTo(one.x, 6)
      expect(other.y, id).toBeCloseTo(one.y, 6)
    }
  })

  it('copes with a node no community claimed', () => {
    const layout = stressLayout(nodes('a', 'b', 'c'), [{ from: 'a', to: 'b' }], [['a', 'b']])
    expect(layout.size).toBe(3)
    expect(Number.isFinite((layout.get('c') as { x: number }).x)).toBe(true)
  })
})
