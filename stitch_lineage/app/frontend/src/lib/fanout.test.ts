import { describe, expect, it } from 'vitest'
import { dashboardCount, dashboardGroups, hopRange, layerGroups } from './fanout'
import { fixtureGraph } from './fixture'
import { buildIndex, type Reach } from './graph'
import { modelDetail } from './details'
import type { GraphNode } from '../types'

const index = buildIndex(fixtureGraph())

function reach(node: Partial<GraphNode> & { node_id: string }, depth: number): Reach {
  return {
    node: {
      node_type: 'model',
      name: node.node_id.split('.').pop() ?? node.node_id,
      properties: {},
      ...node,
    } as GraphNode,
    depth,
    confidence: 'exact',
  }
}

describe('layerGroups', () => {
  it('groups upstream models by schema, sources first, then farthest hop first', () => {
    const groups = layerGroups(
      [
        reach({ node_id: 'model.demo.int_a', schema: 'intermediate' }, 1),
        reach({ node_id: 'model.demo.stg_a', schema: 'intermediate' }, 2),
        reach({ node_id: 'model.demo.mart_a', schema: 'marts' }, 1),
        reach({ node_id: 'source.demo.app.events', node_type: 'source', schema: 'raw' }, 3),
      ],
      'up',
    )
    expect(groups.map((group) => group.label)).toEqual(['sources', 'intermediate', 'marts'])
    // inside a layer, the farther-upstream model leads: staging before intermediate
    expect(groups[1].entries.map((entry) => entry.node.node_id)).toEqual([
      'model.demo.stg_a',
      'model.demo.int_a',
    ])
  })

  it('puts sources first even when the model reads one directly', () => {
    const groups = layerGroups(
      [
        reach({ node_id: 'source.demo.app.events', node_type: 'source', schema: 'raw' }, 1),
        reach({ node_id: 'model.demo.stg_a', schema: 'staging' }, 4),
      ],
      'up',
    )
    expect(groups.map((group) => group.label)).toEqual(['sources', 'staging'])
  })

  it('reads downstream the other way round — nearest consumer first', () => {
    const groups = layerGroups(
      [
        reach({ node_id: 'model.demo.far', schema: 'visualisation' }, 3),
        reach({ node_id: 'model.demo.near', schema: 'marts' }, 1),
        reach({ node_id: 'model.demo.nearer', schema: 'marts' }, 2),
      ],
      'down',
    )
    expect(groups.map((group) => group.label)).toEqual(['marts', 'visualisation'])
    expect(groups[0].entries.map((entry) => entry.node.node_id)).toEqual([
      'model.demo.near',
      'model.demo.nearer',
    ])
  })

  it('keeps a model with no schema out of the way rather than dropping it', () => {
    const groups = layerGroups([reach({ node_id: 'model.demo.x', schema: null }, 1)], 'up')
    expect(groups.map((group) => group.label)).toEqual(['no schema'])
  })

  it('is empty for an empty fan', () => {
    expect(layerGroups([], 'up')).toEqual([])
  })
})

describe('dashboardGroups', () => {
  const detail = modelDetail(index, 'model.demo.fct_revenue')!

  it('groups the model’s cards under the dashboard they appear on', () => {
    const groups = dashboardGroups(index, detail.cards)
    expect(groups).toHaveLength(1)
    expect(groups[0].dashboard?.node_id).toBe('mb_dash::7')
    expect(groups[0].cards.map((card) => card.node.node_id).sort()).toEqual([
      'mb_card::412',
      'mb_card::418',
    ])
    expect(dashboardCount(groups)).toBe(1)
  })

  it('collects cards on no dashboard in a group of their own, last', () => {
    const loose = index.nodesById.get('mb_card::412')!
    const groups = dashboardGroups(index, [
      ...detail.cards,
      { node: { ...loose, node_id: 'mb_card::999', name: 'Ad hoc' }, depth: 1, confidence: 'exact' },
    ])
    expect(groups.map((group) => group.dashboard?.node_id ?? null)).toEqual(['mb_dash::7', null])
    expect(groups[1].cards.map((card) => card.node.name)).toEqual(['Ad hoc'])
    // the loose card is not a dashboard, and must not be counted as one
    expect(dashboardCount(groups)).toBe(1)
  })

  it('is empty for a model no card touches', () => {
    expect(dashboardGroups(index, [])).toEqual([])
    expect(dashboardCount([])).toBe(0)
  })
})

describe('hopRange', () => {
  it('says direct when everything in the layer is one hop away', () => {
    expect(hopRange([reach({ node_id: 'a' }, 1), reach({ node_id: 'b' }, 1)])).toBe('direct')
  })

  it('says the distance when the layer sits at one', () => {
    expect(hopRange([reach({ node_id: 'a' }, 3)])).toBe('3 hops')
  })

  it('gives a range when the layer spans several', () => {
    expect(hopRange([reach({ node_id: 'a' }, 2), reach({ node_id: 'b' }, 5)])).toBe('2–5 hops')
  })

  it('has nothing to say about an empty layer', () => {
    expect(hopRange([])).toBe('')
  })
})
