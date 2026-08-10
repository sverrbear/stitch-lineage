import { describe, expect, it } from 'vitest'
import { erdNodeHeight, layoutErd, type ErdLayoutEdge, type ErdLayoutNode } from './erdLayout'

const NODE_WIDTH = 300

function nodes(...ids: string[]): ErdLayoutNode[] {
  return ids.map((id) => ({ id, height: erdNodeHeight(6, true) }))
}

function edge(from: string, to: string): ErdLayoutEdge {
  return { from, to }
}

/** Every pair of boxes must be disjoint on at least one axis. */
function overlaps(
  layout: Map<string, { x: number; y: number }>,
  heights: Map<string, number>,
): string[] {
  const entries = [...layout.entries()]
  const bad: string[] = []
  for (let i = 0; i < entries.length; i++) {
    for (let j = i + 1; j < entries.length; j++) {
      const [aId, a] = entries[i]
      const [bId, b] = entries[j]
      const overlapX = a.x < b.x + NODE_WIDTH && b.x < a.x + NODE_WIDTH
      const overlapY =
        a.y < b.y + (heights.get(bId) as number) && b.y < a.y + (heights.get(aId) as number)
      if (overlapX && overlapY) bad.push(`${aId} / ${bId}`)
    }
  }
  return bad
}

describe('layoutErd', () => {
  it('places every node exactly once', () => {
    const layout = layoutErd(nodes('a', 'b', 'c'), [edge('a', 'b')])
    expect([...layout.keys()].sort()).toEqual(['a', 'b', 'c'])
  })

  it('returns an empty layout for an empty scope', () => {
    expect(layoutErd([], []).size).toBe(0)
  })

  it('puts the FK side left of the table it references', () => {
    const layout = layoutErd(nodes('zz_fct_orders', 'aa_dim_users'), [
      edge('zz_fct_orders', 'aa_dim_users'),
    ])
    // alphabetical order would put dim_users first; FK direction must win
    expect(layout.get('zz_fct_orders')!.x).toBeLessThan(layout.get('aa_dim_users')!.x)
  })

  it('gives a chain one column per hop', () => {
    const layout = layoutErd(nodes('a', 'b', 'c'), [edge('a', 'b'), edge('b', 'c')])
    const xs = ['a', 'b', 'c'].map((id) => layout.get(id)!.x)
    expect(xs[0]).toBeLessThan(xs[1])
    expect(xs[1]).toBeLessThan(xs[2])
  })

  it('pulls connected tables onto the same row instead of crossing edges', () => {
    // alphabetical rows would pair a→x and b→y across each other; the barycenter
    // sweeps must swap the second column so each edge runs straight across
    const layout = layoutErd(nodes('a_fct', 'b_fct', 'x_dim', 'y_dim'), [
      edge('a_fct', 'y_dim'),
      edge('b_fct', 'x_dim'),
    ])
    expect(layout.get('a_fct')!.y).toBe(layout.get('y_dim')!.y)
    expect(layout.get('b_fct')!.y).toBe(layout.get('x_dim')!.y)
  })

  it('keeps a table next to its only neighbour in a wide layer', () => {
    const ids = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']
    const layout = layoutErd(nodes(...ids, 'target'), [edge('h6', 'target')])
    const gap = (id: string) => Math.abs(layout.get(id)!.y - layout.get('target')!.y)
    // h6 sorts last alphabetically, so only a neighbour-aware layout lands it level
    expect(gap('h6')).toBeLessThan(gap('h1'))
  })

  it('wraps a tall layer into side-by-side stacks, still left of what it points at', () => {
    // the shape a real scope hits: a dozen facts all pointing at one dimension
    const sources = Array.from({ length: 12 }, (_, i) => `fct_${String(i).padStart(2, '0')}`)
    const layout = layoutErd(
      nodes(...sources, 'dim_users'),
      sources.map((id) => edge(id, 'dim_users')),
    )
    const columns = new Set(sources.map((id) => layout.get(id)!.x))
    expect(columns.size).toBeGreaterThan(1)
    const hub = layout.get('dim_users')!.x
    for (const id of sources) expect(layout.get(id)!.x).toBeLessThan(hub)
    // ...and the whole component stays inside the wrap budget
    const ys = [...layout.values()].map((position) => position.y)
    expect(Math.max(...ys) - Math.min(...ys)).toBeLessThan(1400)
  })

  it('honours an explicit wrap budget', () => {
    const ids = ['a', 'b', 'c', 'target']
    const wrapped = layoutErd(
      nodes(...ids),
      ['a', 'b', 'c'].map((id) => edge(id, 'target')),
      { maxLayerHeight: 1 },
    )
    expect(new Set(['a', 'b', 'c'].map((id) => wrapped.get(id)!.x)).size).toBe(3)
  })

  it('separates connected components vertically', () => {
    const layout = layoutErd(nodes('a', 'b', 'c', 'd'), [edge('a', 'b'), edge('c', 'd')])
    const first = Math.max(layout.get('a')!.y, layout.get('b')!.y)
    const second = Math.min(layout.get('c')!.y, layout.get('d')!.y)
    expect(second).toBeGreaterThan(first)
  })

  it('drops unrelated tables into a grid below everything connected', () => {
    const layout = layoutErd(nodes('fct', 'dim', 'lonely_a', 'lonely_b'), [edge('fct', 'dim')])
    const connectedBottom = Math.max(layout.get('fct')!.y, layout.get('dim')!.y)
    expect(layout.get('lonely_a')!.y).toBeGreaterThan(connectedBottom)
    expect(layout.get('lonely_b')!.y).toBeGreaterThan(connectedBottom)
    // ...and side by side, not in one tall stack
    expect(layout.get('lonely_b')!.x).toBeGreaterThan(layout.get('lonely_a')!.x)
  })

  it('never overlaps two tables', () => {
    const all = nodes('fct_orders', 'dim_users', 'dim_dates', 'bridge', 'x', 'y', 'z')
    all[0].height = erdNodeHeight(40, false) // an expanded table
    const layout = layoutErd(all, [
      edge('fct_orders', 'dim_users'),
      edge('fct_orders', 'dim_dates'),
      edge('bridge', 'dim_users'),
    ])
    const heights = new Map(all.map((node) => [node.id, node.height]))
    expect(overlaps(layout, heights)).toEqual([])
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
    expect(layout.get('a')!.x).toBeLessThan(layout.get('b')!.x)
    expect(layout.size).toBe(2)
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
