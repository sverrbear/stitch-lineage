import { describe, expect, it } from 'vitest'
import { buildIndex } from './graph'
import { fixtureGraph } from './fixture'
import { FAN_PX, edgeFans, layoutLineage, lineageFor } from './lineage'

const index = buildIndex(fixtureGraph())

describe('buildIndex', () => {
  it('synthesizes nodes for dangling edge endpoints (star pseudo-columns)', () => {
    const star = index.nodesById.get('model.demo.stg_payments::*')
    expect(star).toBeDefined()
    expect(star!.node_type).toBe('column')
    expect(star!.name).toBe('*')
    expect(star!.properties.synthetic).toBe(true)
    expect(index.synthesizedIds.has('model.demo.stg_payments::*')).toBe(true)
  })
})

describe('lineageFor', () => {
  it('extracts the full reachable chain from a column, both directions', () => {
    const lineage = lineageFor(index, 'model.demo.fct_revenue::net_revenue')
    const ids = new Set(lineage.nodes.map((n) => n.node_id))
    // upstream to the source column
    expect(ids.has('model.demo.stg_payments::amount')).toBe(true)
    expect(ids.has('source.demo.app.events::amount')).toBe(true)
    // downstream through model column -> field -> cards -> dashboard
    expect(ids.has('model.demo.mart_board::net_revenue')).toBe(true)
    expect(ids.has('mb_field::101')).toBe(true)
    expect(ids.has('mb_card::412')).toBe(true)
    expect(ids.has('mb_card::418')).toBe(true)
    expect(ids.has('mb_dash::7')).toBe(true)
  })

  it('renders the reachable subgraph only, never the whole graph', () => {
    const lineage = lineageFor(index, 'model.demo.fct_revenue::net_revenue')
    const ids = new Set(lineage.nodes.map((n) => n.node_id))
    // dim_users is only connected via relates_to -> must not be pulled in
    expect(ids.has('model.demo.dim_users::user_id')).toBe(false)
    expect(ids.has('model.demo.dim_users')).toBe(false)
    // model-level references chain is disjoint from the column chain
    expect(ids.has('model.demo.stg_payments')).toBe(false)
  })

  it('never includes relates_to edges', () => {
    const fromUserId = lineageFor(index, 'model.demo.fct_revenue::user_id')
    expect(fromUserId.edges.every((e) => e.edge_type !== 'relates_to')).toBe(true)
    const ids = new Set(fromUserId.nodes.map((n) => n.node_id))
    expect(ids.has('model.demo.dim_users::user_id')).toBe(false)
    // but the dangling-star upstream IS traversed via the synthesized node
    expect(ids.has('model.demo.stg_payments::*')).toBe(true)
  })

  it('walks model-level lineage from a model node', () => {
    const lineage = lineageFor(index, 'model.demo.fct_revenue')
    const ids = new Set(lineage.nodes.map((n) => n.node_id))
    expect(ids.has('model.demo.stg_payments')).toBe(true)
    expect(ids.has('source.demo.app.events')).toBe(true)
    expect(ids.has('model.demo.mart_board')).toBe(true)
  })

  it('assigns strictly increasing layers along every edge (left-to-right)', () => {
    const lineage = lineageFor(index, 'model.demo.fct_revenue::net_revenue')
    for (const edge of lineage.edges) {
      const from = lineage.layers.get(edge.from)!
      const to = lineage.layers.get(edge.to)!
      expect(from).toBeLessThan(to)
    }
    // layers normalized to 0-based
    const layers = [...lineage.layers.values()]
    expect(Math.min(...layers)).toBe(0)
  })

  it('positions every node, one column per layer', () => {
    const lineage = lineageFor(index, 'model.demo.fct_revenue::net_revenue')
    const positions = layoutLineage(lineage)
    expect(positions.size).toBe(lineage.nodes.length)
    for (const node of lineage.nodes) {
      const pos = positions.get(node.node_id)!
      expect(pos.x).toBe((lineage.layers.get(node.node_id) ?? 0) * 300)
    }
  })

  it('respects the node cap and flags truncation', () => {
    const lineage = lineageFor(index, 'model.demo.fct_revenue::net_revenue', { maxNodes: 2 })
    expect(lineage.truncated).toBe(true)
    // root + at most 2 per direction
    expect(lineage.nodes.length).toBeLessThanOrEqual(5)
  })

  it('tracks bottleneck confidence on downstream walks', () => {
    const lineage = lineageFor(index, 'model.demo.stg_payments::amount')
    // stg_payments.amount -> fct_revenue.net_revenue is `parsed`, everything after inherits it
    const parsedEdge = lineage.edges.find((e) => e.to === 'model.demo.fct_revenue::net_revenue')
    expect(parsedEdge?.confidence).toBe('parsed')
  })
})

describe('layoutLineage wrapping', () => {
  const wide = {
    nodes: Array.from({ length: 7 }, (_, i) => ({ node_id: `n${i}` })),
    edges: [] as Array<{ from: string; to: string }>,
    layers: new Map(Array.from({ length: 7 }, (_, i) => [`n${i}`, i < 5 ? 0 : 1] as const)),
  }

  it('wraps a layer taller than maxRows into side-by-side columns', () => {
    const positions = layoutLineage(wide, { columnWidth: 100, rowHeight: 10, maxRows: 2 })
    const xs = new Set([...positions.values()].map((p) => p.x))
    // layer 0 has 5 nodes at 2 rows -> 3 columns; layer 1 has 2 -> 1 column
    expect(xs.size).toBe(4)
  })

  it('shifts later layers right so a wrapped layer never overlaps them', () => {
    const positions = layoutLineage(wide, { columnWidth: 100, rowHeight: 10, maxRows: 2 })
    const layer0 = Array.from({ length: 5 }, (_, i) => positions.get(`n${i}`)!.x)
    const layer1 = [positions.get('n5')!.x, positions.get('n6')!.x]
    expect(Math.max(...layer0)).toBeLessThan(Math.min(...layer1))
  })

  it('leaves a layer that fits in one column exactly where it was', () => {
    const positions = layoutLineage(wide, { columnWidth: 100, rowHeight: 10, maxRows: 50 })
    expect([...new Set([...positions.values()].map((p) => p.x))]).toEqual([0, 100])
  })
})

// --- keeping converging edges apart (#176) -----------------------------------

const fanEdge = (source: string, target: string) => ({ source, target })

describe('edgeFans', () => {
  it('leaves an edge with no company exactly where it attaches', () => {
    // the common case must not move: a lone edge keeps the handle's own point
    expect(edgeFans([fanEdge('a', 'b')]).size).toBe(0)
  })

  it('spreads two edges arriving at one card, centred on the handle', () => {
    // the reported defect: two relationships into one column row drawn as one line
    const fans = edgeFans([fanEdge('a', 'z'), fanEdge('b', 'z')])
    expect(fans.get(0)?.target).toBe(-FAN_PX / 2)
    expect(fans.get(1)?.target).toBe(FAN_PX / 2)
    // and their sources are untouched, being on different cards
    expect(fans.get(0)?.source).toBe(0)
    expect(fans.get(1)?.source).toBe(0)
  })

  it('spreads a fan LEAVING one card the same way', () => {
    const fans = edgeFans([fanEdge('h', 'x'), fanEdge('h', 'y'), fanEdge('h', 'z')])
    expect([fans.get(0)?.source, fans.get(1)?.source, fans.get(2)?.source]).toEqual([
      -FAN_PX,
      0,
      FAN_PX,
    ])
  })

  it('fans both ends of an edge that is crowded at both', () => {
    const fans = edgeFans([fanEdge('h', 'z'), fanEdge('h', 'y'), fanEdge('b', 'z')])
    const first = fans.get(0)
    expect(first?.source).toBe(-FAN_PX / 2)
    expect(first?.target).toBe(-FAN_PX / 2)
  })

  it('keeps the spread symmetric, so the bundle stays centred on the row', () => {
    for (const count of [2, 3, 4, 5]) {
      const fans = edgeFans(Array.from({ length: count }, (_, i) => fanEdge('h', `t${i}`)))
      const offsets = Array.from({ length: count }, (_, i) => fans.get(i)?.source ?? 0)
      expect(offsets.reduce((a, b) => a + b, 0)).toBeCloseTo(0)
      expect(Math.max(...offsets)).toBeCloseTo(-Math.min(...offsets))
    }
  })

  it('does not fan a hub with hundreds of edges into a smear', () => {
    // 295 edges leave one handle in the real graph; spreading them would be wider
    // than the cards they join, and that view needs a layout fix, not a nudge
    const many = Array.from({ length: 295 }, (_, i) => fanEdge('hub', `card${i}`))
    const fans = edgeFans(many)
    expect(fans.get(0)?.source ?? 0).toBe(0)
    expect(fans.get(294)?.source ?? 0).toBe(0)
  })

  it('separates every pair it fans by at least a stroke width', () => {
    const fans = edgeFans([fanEdge('a', 'z'), fanEdge('b', 'z'), fanEdge('c', 'z')])
    const ys = [0, 1, 2].map((i) => fans.get(i)?.target ?? 0).sort((a, b) => a - b)
    for (let i = 1; i < ys.length; i++) expect(ys[i] - ys[i - 1]).toBeGreaterThanOrEqual(4)
  })

  it('is deterministic for the same edge list', () => {
    const edges = [fanEdge('a', 'z'), fanEdge('b', 'z'), fanEdge('a', 'y')]
    expect([...edgeFans(edges).entries()]).toEqual([...edgeFans(edges).entries()])
  })

  it('treats different handles on one card as different attachment points', () => {
    const fans = edgeFans([
      { source: 'a', target: 'z', targetHandle: 'left' },
      { source: 'b', target: 'z', targetHandle: 'other' },
    ])
    expect(fans.size).toBe(0)
  })
})
