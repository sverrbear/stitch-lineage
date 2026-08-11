import { describe, expect, it } from 'vitest'
import { erdNodeHeight, layoutErd, type ErdLayoutEdge, type ErdLayoutNode } from './erdLayout'
import { measureLayout, type MetricRect } from './layoutMetrics'

const CARD_W = 300

function nodes(...ids: string[]): ErdLayoutNode[] {
  return ids.map((id) => ({ id, width: CARD_W, height: erdNodeHeight(6, true) }))
}

function edge(from: string, to: string): ErdLayoutEdge {
  return { from, to }
}

function centre(layout: Map<string, { x: number; y: number }>, id: string, all: ErdLayoutNode[]) {
  const node = all.find((n) => n.id === id) as ErdLayoutNode
  const position = layout.get(id) as { x: number; y: number }
  return { x: position.x + (node.width ?? CARD_W) / 2, y: position.y + node.height / 2 }
}

function distance(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

/** Every pair of cards must be disjoint on at least one axis. */
function overlaps(layout: Map<string, { x: number; y: number }>, all: ErdLayoutNode[]): string[] {
  const bad: string[] = []
  for (let i = 0; i < all.length; i++) {
    for (let j = i + 1; j < all.length; j++) {
      const a = all[i]
      const b = all[j]
      const pa = layout.get(a.id) as { x: number; y: number }
      const pb = layout.get(b.id) as { x: number; y: number }
      const overlapX = pa.x < pb.x + (b.width ?? CARD_W) && pb.x < pa.x + (a.width ?? CARD_W)
      const overlapY = pa.y < pb.y + b.height && pb.y < pa.y + a.height
      if (overlapX && overlapY) bad.push(`${a.id} / ${b.id}`)
    }
  }
  return bad
}

describe('layoutErd — star-schema arrangement', () => {
  it('places every node exactly once', () => {
    const layout = layoutErd(nodes('a', 'b', 'c'), [edge('a', 'b')])
    expect([...layout.keys()].sort()).toEqual(['a', 'b', 'c'])
  })

  it('returns an empty layout for an empty scope', () => {
    expect(layoutErd([], []).size).toBe(0)
  })

  it('puts the fact in the middle and its dimensions around it', () => {
    const dims = ['dim_a', 'dim_b', 'dim_c', 'dim_d', 'dim_e', 'dim_f']
    const all = nodes('fct_orders', ...dims)
    const layout = layoutErd(all, dims.map((dim) => edge('fct_orders', dim)))
    const hub = centre(layout, 'fct_orders', all)
    const ring = dims.map((dim) => centre(layout, dim, all))

    // the hub is nearer the centroid of the constellation than any dimension is
    const centroid = ring.concat(hub).reduce(
      (acc, p, _i, list) => ({ x: acc.x + p.x / list.length, y: acc.y + p.y / list.length }),
      { x: 0, y: 0 },
    )
    const hubGap = distance(hub, centroid)
    for (const dim of ring) expect(hubGap).toBeLessThan(distance(dim, centroid))

    // ...and they surround it rather than queueing up on one side
    const angles = ring.map((p) => Math.atan2(p.y - hub.y, p.x - hub.x))
    expect(angles.some((a) => a > 0)).toBe(true)
    expect(angles.some((a) => a < 0)).toBe(true)
    expect(Math.max(...ring.map((p) => p.x)) - hub.x).toBeGreaterThan(0)
    expect(hub.x - Math.min(...ring.map((p) => p.x))).toBeGreaterThan(0)
  })

  it('keeps every spoke short — no dimension is flung across the canvas', () => {
    const dims = Array.from({ length: 8 }, (_, i) => `dim_${i}`)
    const all = nodes('fct', ...dims)
    const layout = layoutErd(all, dims.map((dim) => edge('fct', dim)))
    const hub = centre(layout, 'fct', all)
    const spokes = dims.map((dim) => distance(centre(layout, dim, all), hub))
    // eight cards do not fit on one orbit around a 300px card, so the tail sits
    // on a second ring — but every spoke stays inside a couple of card-widths,
    // which is the whole point: you can follow the line without panning
    expect(Math.max(...spokes)).toBeLessThan(CARD_W * 3)
  })

  it('spreads a big fact over more than one orbit rather than one crowded ring', () => {
    const dims = Array.from({ length: 14 }, (_, i) => `dim_${String(i).padStart(2, '0')}`)
    const all = nodes('fct', ...dims)
    const layout = layoutErd(all, dims.map((dim) => edge('fct', dim)))
    const hub = centre(layout, 'fct', all)
    const spokes = dims.map((dim) => Math.round(distance(centre(layout, dim, all), hub)))
    expect(new Set(spokes.map((s) => Math.round(s / 100))).size).toBeGreaterThan(1)
    expect(overlaps(layout, all)).toEqual([])
  })

  it('sits a conformed dimension between the facts that share it', () => {
    const all = nodes('fct_a', 'fct_b', 'dim_shared', 'dim_a1', 'dim_a2', 'dim_b1', 'dim_b2')
    const layout = layoutErd(all, [
      edge('fct_a', 'dim_shared'),
      edge('fct_b', 'dim_shared'),
      edge('fct_a', 'dim_a1'),
      edge('fct_a', 'dim_a2'),
      edge('fct_b', 'dim_b1'),
      edge('fct_b', 'dim_b2'),
    ])
    const a = centre(layout, 'fct_a', all)
    const b = centre(layout, 'fct_b', all)
    const shared = centre(layout, 'dim_shared', all)
    // closer to the line between the two facts than either fact is to the other
    expect(distance(shared, a) + distance(shared, b)).toBeLessThan(distance(a, b) * 1.6)
  })

  it('tiles several facts as separate constellations', () => {
    const all = nodes('fct_a', 'fct_b', 'a1', 'a2', 'a3', 'b1', 'b2', 'b3')
    const layout = layoutErd(all, [
      edge('fct_a', 'a1'),
      edge('fct_a', 'a2'),
      edge('fct_a', 'a3'),
      edge('fct_b', 'b1'),
      edge('fct_b', 'b2'),
      edge('fct_b', 'b3'),
      edge('fct_a', 'fct_b'),
    ])
    const a = centre(layout, 'fct_a', all)
    const b = centre(layout, 'fct_b', all)
    for (const satellite of ['a1', 'a2', 'a3']) {
      expect(distance(centre(layout, satellite, all), a)).toBeLessThan(
        distance(centre(layout, satellite, all), b),
      )
    }
    for (const satellite of ['b1', 'b2', 'b3']) {
      expect(distance(centre(layout, satellite, all), b)).toBeLessThan(
        distance(centre(layout, satellite, all), a),
      )
    }
  })

  it('separates connected components', () => {
    const all = nodes('a', 'b', 'c', 'd')
    const layout = layoutErd(all, [edge('a', 'b'), edge('c', 'd')])
    const first = Math.max(layout.get('a')!.y, layout.get('b')!.y)
    const second = Math.min(layout.get('c')!.y, layout.get('d')!.y)
    expect(second).toBeGreaterThan(first)
  })

  it('drops unrelated tables into a grid below everything connected', () => {
    const all = nodes('fct', 'dim', 'lonely_a', 'lonely_b')
    const layout = layoutErd(all, [edge('fct', 'dim')])
    const connectedBottom = Math.max(layout.get('fct')!.y, layout.get('dim')!.y)
    expect(layout.get('lonely_a')!.y).toBeGreaterThan(connectedBottom)
    expect(layout.get('lonely_b')!.y).toBeGreaterThan(connectedBottom)
    expect(layout.get('lonely_b')!.x).toBeGreaterThan(layout.get('lonely_a')!.x)
  })

  it('never overlaps two cards, whatever their measured size', () => {
    const all = nodes('fct_orders', 'dim_users', 'dim_dates', 'bridge', 'x', 'y', 'z')
    all[0].height = erdNodeHeight(40, false) // an expanded fact
    all[1].height = erdNodeHeight(28, false)
    const layout = layoutErd(all, [
      edge('fct_orders', 'dim_users'),
      edge('fct_orders', 'dim_dates'),
      edge('fct_orders', 'bridge'),
      edge('bridge', 'dim_users'),
    ])
    expect(overlaps(layout, all)).toEqual([])
  })

  it('never overlaps in a dense multi-fact scope either', () => {
    const ids = ['fct_a', 'fct_b', 'fct_c', ...Array.from({ length: 18 }, (_, i) => `dim_${i}`)]
    const all = nodes(...ids)
    const edges: ErdLayoutEdge[] = []
    ids.slice(3).forEach((dim, i) => {
      edges.push(edge(['fct_a', 'fct_b', 'fct_c'][i % 3], dim))
      if (i % 5 === 0) edges.push(edge('fct_a', dim))
    })
    const layout = layoutErd(all, edges)
    expect(overlaps(layout, all)).toEqual([])
  })

  it('is deterministic under input reordering', () => {
    const ids = ['a', 'b', 'c', 'd', 'e']
    const rels = [edge('a', 'b'), edge('b', 'c'), edge('d', 'c')]
    const first = layoutErd(nodes(...ids), rels)
    const second = layoutErd(nodes(...[...ids].reverse()), [...rels].reverse())
    expect([...second.entries()].sort()).toEqual([...first.entries()].sort())
  })

  it('survives a cycle in the declared relationships', () => {
    const layout = layoutErd(nodes('a', 'b', 'c'), [edge('a', 'b'), edge('b', 'c'), edge('c', 'a')])
    expect(layout.size).toBe(3)
    for (const position of layout.values()) {
      expect(Number.isFinite(position.x) && Number.isFinite(position.y)).toBe(true)
    }
  })

  it('ignores self-joins, duplicates and edges pointing outside the scope', () => {
    const layout = layoutErd(nodes('a', 'b'), [
      edge('a', 'a'),
      edge('a', 'b'),
      edge('a', 'b'),
      edge('a', 'gone'),
    ])
    expect(layout.size).toBe(2)
    expect(layout.get('a')).not.toEqual(layout.get('b'))
  })

  it('never overlaps the grid of unrelated tables, whatever they measure', () => {
    // a card wider than the fallback (a long model name, #80) used to land under its
    // neighbour: the grid stepped by a fixed 300px pitch
    const all: ErdLayoutNode[] = [
      { id: 'fct', width: CARD_W, height: 200 },
      { id: 'dim', width: CARD_W, height: 200 },
      { id: 'lonely_a', width: 396, height: 200 },
      { id: 'lonely_b', width: 240, height: 300 },
      { id: 'lonely_c', width: 396, height: 200 },
      { id: 'lonely_d', width: 230, height: 160 },
    ]
    expect(overlaps(layoutErd(all, [edge('fct', 'dim')]), all)).toEqual([])
  })

  it('uses measured widths, not just the fallback', () => {
    const all: ErdLayoutNode[] = [
      { id: 'fct', width: 900, height: 200 },
      { id: 'dim', width: 900, height: 200 },
    ]
    const layout = layoutErd(all, [edge('fct', 'dim')])
    expect(overlaps(layout, all)).toEqual([])
  })
})

describe('erdNodeHeight', () => {
  it('grows with the number of visible columns', () => {
    expect(erdNodeHeight(12, false)).toBeGreaterThan(erdNodeHeight(3, false))
  })

  it('reserves room for the expand control only when there is one', () => {
    expect(erdNodeHeight(3, true)).toBeGreaterThan(erdNodeHeight(3, false))
  })
})

// The acceptance criteria for #101, asserted rather than eyeballed. `layoutMetrics`
// computes them from the returned coordinates alone, so these check the constraint
// and not the machinery that happens to satisfy it.
describe('layoutErd — measured acceptance (#101)', () => {
  const GUTTER = 28

  function measure(all: ErdLayoutNode[], rels: ErdLayoutEdge[], options = {}) {
    const layout = layoutErd(all, rels, options)
    const rects: MetricRect[] = all.map((node) => {
      const at = layout.get(node.id) as { x: number; y: number }
      return { id: node.id, x: at.x, y: at.y, w: node.width ?? CARD_W, h: node.height }
    })
    return { layout, rects, ...measureLayout(rects, rels, GUTTER) }
  }

  /** A hub with `k` satellites, plus `extra` unrelated tables. */
  function star(k: number, extra = 0) {
    const dims = Array.from({ length: k }, (_, i) => `dim_${String(i).padStart(2, '0')}`)
    const lonely = Array.from({ length: extra }, (_, i) => `lonely_${String(i).padStart(2, '0')}`)
    return {
      all: nodes('fct', ...dims, ...lonely),
      rels: dims.map((dim) => edge('fct', dim)),
      dims,
      lonely,
    }
  }

  it('never overlaps, and every pair clears the gutter', () => {
    for (const k of [1, 2, 5, 8, 14, 20, 30]) {
      const { all, rels } = star(k)
      const m = measure(all, rels)
      expect(m.overlaps, `k=${k}`).toBe(0)
      expect(m.minClearance, `k=${k}`).toBeGreaterThanOrEqual(GUTTER - 1e-6)
      expect(m.pairsUnderGutter, `k=${k}`).toBe(0)
    }
  })

  it('holds the gutter with expanded cards in the mix', () => {
    // an expanded table is much taller; the constraint is on real rendered sizes
    const { all, rels } = star(12)
    all[0].height = erdNodeHeight(48, false)
    all[3].height = erdNodeHeight(30, false)
    all[7].width = 520
    const m = measure(all, rels)
    expect(m.overlaps).toBe(0)
    expect(m.minClearance).toBeGreaterThanOrEqual(GUTTER - 1e-6)
  })

  it('holds the gutter across the band of unrelated tables too', () => {
    const { all, rels } = star(6, 40)
    const m = measure(all, rels)
    expect(m.overlaps).toBe(0)
    expect(m.minClearance).toBeGreaterThanOrEqual(GUTTER - 1e-6)
  })

  it('puts every unrelated table below everything connected, clear of it', () => {
    const { all, rels, dims, lonely } = star(6, 12)
    const { layout, rects } = measure(all, rels)
    const connected = ['fct', ...dims]
    const lowest = Math.max(
      ...connected.map((id) => (layout.get(id) as { y: number }).y + heightOf(all, id)),
    )
    const highestLonely = Math.min(...lonely.map((id) => (layout.get(id) as { y: number }).y))
    expect(highestLonely).toBeGreaterThan(lowest)
    // and clearly separated, not merely non-overlapping
    expect(highestLonely - lowest).toBeGreaterThan(200)
    expect(rects.length).toBe(all.length)
  })

  it('keeps a pure star free of crossings', () => {
    for (const k of [6, 14, 20]) {
      const { all, rels } = star(k)
      expect(measure(all, rels).crossings, `k=${k}`).toBe(0)
    }
  })

  it('does not cross the relationships of two facts that share a dimension', () => {
    const all = nodes('fct_a', 'fct_b', 'dim_shared', 'a1', 'a2', 'a3', 'b1', 'b2', 'b3')
    const rels = [
      edge('fct_a', 'dim_shared'),
      edge('fct_b', 'dim_shared'),
      edge('fct_a', 'a1'),
      edge('fct_a', 'a2'),
      edge('fct_a', 'a3'),
      edge('fct_b', 'b1'),
      edge('fct_b', 'b2'),
      edge('fct_b', 'b3'),
    ]
    expect(measure(all, rels).crossings).toBe(0)
  })

  it('keeps a hub nearer the middle of its constellation than its leaves', () => {
    const { all, rels, dims } = star(12)
    const { layout } = measure(all, rels)
    const centre = (id: string) => ({
      x: (layout.get(id) as { x: number }).x + CARD_W / 2,
      y: (layout.get(id) as { y: number }).y + heightOf(all, id) / 2,
    })
    const points = ['fct', ...dims].map(centre)
    const mid = {
      x: points.reduce((s, p) => s + p.x, 0) / points.length,
      y: points.reduce((s, p) => s + p.y, 0) / points.length,
    }
    const radius = (id: string) => distance(centre(id), mid)
    for (const dim of dims) expect(radius('fct')).toBeLessThan(radius(dim))
  })

  it('gives identical coordinates on a second call — the canvas must not reshuffle', () => {
    const { all, rels } = star(14, 9)
    const first = layoutErd(all, rels)
    const second = layoutErd(all, rels)
    for (const [id, at] of first) {
      expect(second.get(id), id).toEqual(at)
    }
  })

  it('is unaffected by the order the scope arrives in', () => {
    const { all, rels } = star(11, 7)
    const forward = layoutErd(all, rels)
    const backward = layoutErd([...all].reverse(), [...rels].reverse())
    for (const [id, at] of forward) {
      const other = backward.get(id) as { x: number; y: number }
      expect(other.x, id).toBeCloseTo(at.x, 6)
      expect(other.y, id).toBeCloseTo(at.y, 6)
    }
  })

  it('shortens the relationships it draws as the constellation grows', () => {
    // the objective, sanity-checked: a leaf stays within a few card widths of its hub
    // however many siblings it has, rather than being flung onto an ever-bigger orbit
    for (const k of [8, 14, 20, 30]) {
      const { all, rels } = star(k)
      expect(measure(all, rels).meanEdgeLength, `k=${k}`).toBeLessThan(CARD_W * 3)
    }
  })
})

function heightOf(all: ErdLayoutNode[], id: string): number {
  return (all.find((node) => node.id === id) as ErdLayoutNode).height
}
