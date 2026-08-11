// The rules the hand-tuned reference is made of (#129), asserted directly on the
// placement rather than through a score. `erdLayout.test.ts` still owns the
// end-to-end guarantees (no overlap, the gutter, the isolated band); this file is
// about the SHAPE: hub in the middle, clusters in their own wedges, a sub-hub
// leading each one.

import { describe, expect, it } from 'vitest'
import { hubLayout, type HubEdge, type HubNode } from './hubLayout'

const OPTIONS = { idealLength: 380, gutter: 34, gap: 68 }

function nodes(...ids: string[]): HubNode[] {
  return ids.map((id) => ({ id, w: 300, h: 140 }))
}

function edges(...pairs: [string, string][]): HubEdge[] {
  return pairs.map(([from, to]) => ({ from, to }))
}

const distance = (a: { x: number; y: number }, b: { x: number; y: number }) =>
  Math.hypot(a.x - b.x, a.y - b.y)

/** A hub with two self-contained clusters hanging off it, plus two bare spokes. */
function twoClusters() {
  const all = nodes(
    'dim_users',
    'dim_matches',
    'fct_match_a',
    'fct_match_b',
    'dim_subs',
    'fct_sub_a',
    'fct_sub_b',
    'spoke_a',
    'spoke_b',
  )
  const rels = edges(
    ['dim_users', 'dim_matches'],
    ['dim_matches', 'fct_match_a'],
    ['dim_matches', 'fct_match_b'],
    ['fct_match_a', 'fct_match_b'],
    ['dim_users', 'dim_subs'],
    ['dim_subs', 'fct_sub_a'],
    ['dim_subs', 'fct_sub_b'],
    ['fct_sub_a', 'fct_sub_b'],
    ['dim_users', 'spoke_a'],
    ['dim_users', 'spoke_b'],
  )
  const communities = [
    ['dim_matches', 'fct_match_a', 'fct_match_b'],
    ['dim_subs', 'fct_sub_a', 'fct_sub_b'],
    ['dim_users', 'spoke_a', 'spoke_b'],
  ]
  return { all, rels, communities }
}

describe('hubLayout — the hand-tuned rules', () => {
  it('puts the busiest table dead centre', () => {
    const { all, rels, communities } = twoClusters()
    const places = hubLayout(all, rels, communities, OPTIONS)
    expect(places.get('dim_users')).toEqual({ x: 0, y: 0 })
    // ...and it is the busiest one because of its degree, not its name
    for (const id of ['dim_matches', 'dim_subs', 'spoke_a']) {
      expect(distance(places.get(id)!, { x: 0, y: 0 })).toBeGreaterThan(0)
    }
  })

  it('gives each cluster its own wedge, clear of the other', () => {
    const { all, rels, communities } = twoClusters()
    const places = hubLayout(all, rels, communities, OPTIONS)
    const bearing = (id: string) => {
      const at = places.get(id)!
      return Math.atan2(at.y, at.x)
    }
    // Bearings wrap at ±π, so "is it inside that span" is the wrong question — a
    // wedge straddling the boundary would read as spanning the whole circle. Ask
    // instead which lead each table is angularly nearer: that is what "a cluster is
    // a place on the map" actually means.
    const apart = (a: number, b: number) => {
      const raw = Math.abs(a - b) % (2 * Math.PI)
      return raw > Math.PI ? 2 * Math.PI - raw : raw
    }
    for (const [own, other, members] of [
      ['dim_matches', 'dim_subs', ['fct_match_a', 'fct_match_b']],
      ['dim_subs', 'dim_matches', ['fct_sub_a', 'fct_sub_b']],
    ] as const) {
      for (const id of members) {
        expect(apart(bearing(id), bearing(own)), `${id} strayed towards ${other}`).toBeLessThan(
          apart(bearing(id), bearing(other)),
        )
      }
    }
  })

  it('leads each cluster with its own sub-hub, nearest the centre', () => {
    const { all, rels, communities } = twoClusters()
    const places = hubLayout(all, rels, communities, OPTIONS)
    const fromHub = (id: string) => distance(places.get(id)!, { x: 0, y: 0 })
    for (const [lead, satellites] of [
      ['dim_matches', ['fct_match_a', 'fct_match_b']],
      ['dim_subs', ['fct_sub_a', 'fct_sub_b']],
    ] as const) {
      for (const satellite of satellites) {
        expect(fromHub(lead), `${lead} vs ${satellite}`).toBeLessThan(fromHub(satellite))
      }
    }
  })

  it('rings a pure star around its fact rather than stacking it on one side', () => {
    const dims = Array.from({ length: 7 }, (_, i) => `dim_${i}`)
    const places = hubLayout(
      nodes('fct', ...dims),
      edges(...dims.map((dim) => ['fct', dim] as [string, string])),
      [['fct', ...dims]],
      OPTIONS,
    )
    const angles = dims.map((dim) => Math.atan2(places.get(dim)!.y, places.get(dim)!.x))
    expect(angles.some((a) => a > 0)).toBe(true)
    expect(angles.some((a) => a < 0)).toBe(true)
    // the centroid of the ring stays on the hub: a lopsided ring is what stops the
    // hub reading as the middle of its own map
    const centroid = dims.reduce(
      (acc, dim) => ({
        x: acc.x + places.get(dim)!.x / dims.length,
        y: acc.y + places.get(dim)!.y / dims.length,
      }),
      { x: 0, y: 0 },
    )
    const orbit = distance(places.get(dims[0])!, { x: 0, y: 0 })
    expect(distance(centroid, { x: 0, y: 0 })).toBeLessThan(orbit * 0.35)
  })

  it('spills a crowded fact onto a second orbit instead of one huge ring', () => {
    const dims = Array.from({ length: 16 }, (_, i) => `dim_${String(i).padStart(2, '0')}`)
    const places = hubLayout(
      nodes('fct', ...dims),
      edges(...dims.map((dim) => ['fct', dim] as [string, string])),
      [['fct', ...dims]],
      OPTIONS,
    )
    const orbits = new Set(
      dims.map((dim) => Math.round(distance(places.get(dim)!, { x: 0, y: 0 }) / 50)),
    )
    expect(orbits.size).toBeGreaterThan(1)
  })

  it('moves a dimension shared by two clusters between them, not into one', () => {
    const all = nodes('dim_users', 'fct_a', 'fct_b', 'dim_shared', 'a1', 'b1')
    const rels = edges(
      ['dim_users', 'fct_a'],
      ['dim_users', 'fct_b'],
      ['fct_a', 'a1'],
      ['fct_b', 'b1'],
      ['fct_a', 'dim_shared'],
      ['fct_b', 'dim_shared'],
    )
    const places = hubLayout(all, rels, [['dim_users', 'fct_a', 'a1'], ['fct_b', 'b1'], ['dim_shared']], OPTIONS)
    const a = places.get('fct_a')!
    const b = places.get('fct_b')!
    const shared = places.get('dim_shared')!
    // on or near the segment between them: the detour through it is small
    expect(distance(shared, a) + distance(shared, b)).toBeLessThan(distance(a, b) * 1.5)
  })

  it('is deterministic, whatever order the scope arrives in', () => {
    const { all, rels, communities } = twoClusters()
    const forward = hubLayout(all, rels, communities, OPTIONS)
    const backward = hubLayout(
      [...all].reverse(),
      [...rels].reverse(),
      [...communities].reverse().map((community) => [...community].reverse()),
      OPTIONS,
    )
    for (const [id, at] of forward) expect(backward.get(id)).toEqual(at)
  })

  it('places a lone table, and an empty scope, without complaint', () => {
    expect(hubLayout(nodes('only'), [], [['only']], OPTIONS).get('only')).toEqual({ x: 0, y: 0 })
    expect(hubLayout([], [], [], OPTIONS).size).toBe(0)
  })
})
