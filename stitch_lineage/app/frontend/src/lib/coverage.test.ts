import { describe, expect, it } from 'vitest'
import { coverageList, coverageTiles, graphStats, startingPoints } from './coverage'
import { fixtureGraph } from './fixture'
import { buildIndex } from './graph'

const graph = fixtureGraph()
const withCoverage = {
  ...graph,
  coverage: {
    models_bound: 1,
    models_total: 4,
    columns_traced: 5,
    columns_total: 8,
    mbql_cards_resolved: 1,
    mbql_cards_total: 2,
    native_cards_resolved: 0,
    native_cards_total: 1,
    dashboards: 1,
    dashboards_total: 1,
    unbound_models: ['model.demo.dim_users', 'model.demo.gone'],
    untraced_columns: ['model.demo.mart_board::net_revenue'],
    unresolved_cards: [418],
  },
}
const index = buildIndex(withCoverage)

describe('coverageTiles', () => {
  const tiles = coverageTiles(withCoverage.coverage)
  const tile = (key: string) => tiles.find((t) => t.key === key)!

  it('turns the coverage block into four tiles with their gaps', () => {
    expect(tiles.map((t) => t.key)).toEqual(['models', 'columns', 'cards', 'dashboards'])
    expect(tile('models').value).toBe(1)
    expect(tile('models').total).toBe(4)
    expect(tile('models').list).toBe('unbound-models')
    expect(tile('models').listLabel).toBe('2 unbound')
  })

  it('sums MBQL and native cards into one resolved figure', () => {
    expect(tile('cards').value).toBe(1)
    expect(tile('cards').total).toBe(3)
  })

  it('offers no list when there is no gap to list', () => {
    expect(tile('dashboards').list).toBeNull()
  })

  it('survives a graph with no coverage block at all', () => {
    const empty = coverageTiles(undefined)
    expect(empty).toHaveLength(4)
    expect(empty.every((t) => t.value === 0 && t.list === null)).toBe(true)
  })
})

describe('graphStats', () => {
  it('counts nodes by type in a fixed order and reports the graph age', () => {
    const stats = graphStats(withCoverage, new Date('2026-08-14T00:00:00Z'))
    expect(stats.nodeCount).toBe(withCoverage.nodes.length)
    expect(stats.edgeCount).toBe(withCoverage.edges.length)
    expect(stats.byType.map((e) => e.type)).toEqual([
      'source',
      'model',
      'column',
      'mb_field',
      'mb_card',
      'mb_dashboard',
    ])
    expect(stats.ageDays).toBe(7) // fixture is built 2026-08-07
  })

  it('reports no age when the graph carries no timestamp', () => {
    const stats = graphStats({ ...withCoverage, generated_at: null }, new Date())
    expect(stats.ageDays).toBeNull()
    expect(stats.generatedAt).toBeNull()
  })
})

describe('startingPoints', () => {
  it('ranks models by the cards that depend on them', () => {
    const starts = startingPoints(index)
    expect(starts.mostConsumedModels[0].node.node_id).toBe('model.demo.fct_revenue')
    expect(starts.mostConsumedModels[0].count).toBe(2)
  })

  it('ranks dashboards by the cards on them', () => {
    const starts = startingPoints(index)
    expect(starts.biggestDashboards[0].node.node_id).toBe('mb_dash::7')
    expect(starts.biggestDashboards[0].count).toBe(2)
  })
})

describe('coverageList', () => {
  it('resolves each entry to its node, keeping the ones the graph lost', () => {
    const list = coverageList(index, 'unbound-models')
    expect(list.entries.map((e) => e.nodeId)).toEqual(['model.demo.dim_users', 'model.demo.gone'])
    expect(list.entries[0].node?.name).toBe('dim_users')
    expect(list.entries[1].node).toBeNull()
  })

  it('turns unresolved card ids into node ids', () => {
    const list = coverageList(index, 'unresolved-cards')
    expect(list.entries[0].nodeId).toBe('mb_card::418')
    expect(list.entries[0].node?.name).toBe('Weekly revenue trend')
  })

  it('is empty, not broken, when the coverage block has nothing to say', () => {
    const bare = buildIndex(fixtureGraph())
    expect(coverageList(bare, 'untraced-columns').entries).toEqual([])
  })
})
