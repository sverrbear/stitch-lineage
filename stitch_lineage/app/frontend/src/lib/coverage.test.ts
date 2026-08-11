import { describe, expect, it } from 'vitest'
import {
  buildStamp,
  coverageList,
  coveragePercent,
  coverageRows,
  graphStats,
  homeExamples,
  startingPoints,
} from './coverage'
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

describe('coverageRows', () => {
  const rows = coverageRows(withCoverage.coverage)
  const row = (key: string) => rows.find((r) => r.key === key)!

  it('reads columns first, then models, then cards — the 7a order', () => {
    expect(rows.map((r) => r.key)).toEqual(['columns', 'models', 'cards'])
  })

  it('puts each ratio next to the gap it leaves', () => {
    expect(row('models').value).toBe(1)
    expect(row('models').total).toBe(4)
    expect(row('models').gap).toBe(2)
    expect(row('models').list).toBe('unbound-models')
    expect(row('models').gapLabel).toBe('2 unbound')
  })

  it('sums MBQL and native cards into one resolved figure', () => {
    expect(row('cards').value).toBe(1)
    expect(row('cards').total).toBe(3)
  })

  it('offers no list when there is no gap to list', () => {
    const clean = coverageRows({ ...withCoverage.coverage, untraced_columns: [] })
    expect(clean.find((r) => r.key === 'columns')!.list).toBeNull()
    expect(clean.find((r) => r.key === 'columns')!.gapLabel).toBeNull()
  })

  it('survives a graph with no coverage block at all', () => {
    const empty = coverageRows(undefined)
    expect(empty).toHaveLength(3)
    expect(empty.every((r) => r.value === 0 && r.list === null)).toBe(true)
  })

  // #119: a ratio with an unstated caveat oversells the build.
  it('states the inferred share of what it calls traced', () => {
    const inferred = coverageRows({ ...withCoverage.coverage, columns_inferred: 3 })
    expect(inferred.find((r) => r.key === 'columns')!.note).toBe('3 of those inferred (60%)') // 3/5
  })

  it('says nothing about inference when nothing was inferred', () => {
    expect(row('columns').note).toBeNull()
    expect(row('columns').noteHint).toBeNull()
  })

  it('never divides by a traced count of zero', () => {
    const none = coverageRows({ columns_inferred: 2, columns_traced: 0, columns_total: 8 })
    expect(none.find((r) => r.key === 'columns')!.note).toBe('2 of those inferred')
  })

  it('admits the models config took out of the denominator', () => {
    const excluded = coverageRows({ ...withCoverage.coverage, models_excluded: 30 })
    expect(excluded.find((r) => r.key === 'models')!.note).toBe('30 excluded by config')
    expect(row('models').note).toBeNull()
  })
})

describe('coveragePercent', () => {
  it('is the share of columns this build can trace', () => {
    expect(coveragePercent(withCoverage.coverage)).toBe(63) // 5/8
  })

  it('is null when there is no total to be a fraction of', () => {
    expect(coveragePercent(undefined)).toBeNull()
    expect(coveragePercent({ columns_traced: 4 })).toBeNull()
  })
})

describe('buildStamp', () => {
  const built = '2026-08-10T16:07:00'

  it('says today, with the clock time', () => {
    expect(buildStamp(built, new Date('2026-08-10T23:00:00'))).toEqual({
      text: 'Built today, 16:07',
      ageDays: 0,
      stale: false,
    })
  })

  it('counts calendar days, not elapsed hours', () => {
    // three hours later, but the reader calls it yesterday
    expect(buildStamp('2026-08-10T23:30:00', new Date('2026-08-11T02:30:00'))?.text).toBe(
      'Built yesterday, 23:30',
    )
  })

  it('names the day up to a week out, then the date', () => {
    expect(buildStamp(built, new Date('2026-08-13T09:00:00'))?.text).toBe('Built 3 days ago, 16:07')
    expect(buildStamp(built, new Date('2026-08-20T09:00:00'))?.text).toBe('Built 2026-08-10, 16:07')
  })

  it('marks a graph a week old as stale — a stale graph is a wrong answer', () => {
    expect(buildStamp(built, new Date('2026-08-16T09:00:00'))?.stale).toBe(false)
    expect(buildStamp(built, new Date('2026-08-17T09:00:00'))?.stale).toBe(true)
  })

  it('is null when the graph carries no usable timestamp', () => {
    expect(buildStamp(null, new Date())).toBeNull()
    expect(buildStamp('not a date', new Date())).toBeNull()
  })
})

describe('homeExamples', () => {
  it('offers real identifiers from this graph, columns qualified by their model', () => {
    const examples = homeExamples(index)
    expect(examples.map((e) => e.label)).toEqual([
      'fct_revenue.net_revenue',
      'fct_revenue.user_id',
      'Board dashboard',
    ])
  })

  it('ranks a column by the cards it reaches, not by its one binds_to edge', () => {
    // net_revenue binds to a field two cards read; user_id binds to a field with
    // none. Counting edges would tie them at 1 and order them alphabetically.
    const [first] = homeExamples(index, 2)
    expect(first.label).toBe('fct_revenue.net_revenue')
  })

  it('still finds somewhere to start when nothing reaches Metabase', () => {
    const bare = buildIndex({ ...fixtureGraph(), edges: [] })
    const examples = homeExamples(bare)
    expect(examples).toHaveLength(3)
    expect(examples.every((e) => e.label.length > 0)).toBe(true)
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
