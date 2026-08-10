import { describe, expect, it } from 'vitest'
import { fixtureGraph } from './fixture'
import { buildIndex } from './graph'
import { overviewFor } from './overview'

const index = buildIndex(fixtureGraph())
const ids = (data: ReturnType<typeof overviewFor>) => data.nodes.map((n) => n.node.node_id)

describe('overviewFor', () => {
  it('draws models and sources, never columns or fields', () => {
    const data = overviewFor(index)
    for (const entry of data.nodes) {
      expect(['model', 'source', 'mb_card', 'mb_dashboard']).toContain(entry.node.node_type)
    }
    expect(ids(data)).toContain('model.demo.fct_revenue')
    expect(ids(data)).toContain('source.demo.app.events')
  })

  it('counts a model’s Metabase reach whether or not those nodes are drawn', () => {
    const hidden = overviewFor(index, { metabase: 'none' })
    const fct = hidden.nodes.find((n) => n.node.node_id === 'model.demo.fct_revenue')!
    expect(fct.cardCount).toBe(2)
    expect(fct.dashboardCount).toBe(1)
    expect(fct.columnCount).toBe(2)
    expect(ids(hidden).some((id) => id.startsWith('mb_'))).toBe(false)
    expect(hidden.omitted.metabase).toBe(3) // 2 cards + 1 dashboard
  })

  it('aggregates the BI side per dashboard by default', () => {
    const data = overviewFor(index)
    expect(ids(data)).toContain('mb_dash::7')
    expect(ids(data)).not.toContain('mb_card::412')
    // the two-hop model -> dashboard shortcut, or the dashboards would float free
    expect(data.edges.some((e) => e.from === 'model.demo.fct_revenue' && e.to === 'mb_dash::7')).toBe(true)
  })

  it('draws cards when asked', () => {
    const data = overviewFor(index, { metabase: 'cards' })
    expect(ids(data)).toContain('mb_card::412')
    expect(ids(data)).toContain('mb_card::418')
    expect(ids(data)).not.toContain('mb_dash::7')
  })

  it('hides package and warehouse-internal schemas unless asked', () => {
    const data = overviewFor(index)
    expect(ids(data)).not.toContain('model.elementary.alerts_anomaly_detection')
    expect(data.omitted.internal).toBeGreaterThan(0)
    const withInternal = overviewFor(index, { includeInternal: true })
    expect(ids(withInternal)).toContain('model.elementary.alerts_anomaly_detection')
    expect(withInternal.omitted.internal).toBe(0)
  })

  it('scopes by schema and by dbt tag, counting what it left out', () => {
    const marts = overviewFor(index, { scope: { kind: 'schema', value: 'marts' } })
    expect(ids(marts)).toContain('model.demo.fct_revenue')
    expect(ids(marts)).not.toContain('model.demo.stg_payments')
    expect(marts.omitted.outOfScope).toBeGreaterThan(0)

    const finance = overviewFor(index, { scope: { kind: 'tag', value: 'finance' } })
    expect(ids(finance)).toEqual(expect.arrayContaining(['model.demo.fct_revenue']))
    expect(ids(finance)).not.toContain('model.demo.dim_users')
  })

  it('caps the Metabase side and reports the remainder rather than dropping it quietly', () => {
    const data = overviewFor(index, { metabase: 'cards', maxMetabaseNodes: 1 })
    expect(data.counts.card).toBe(1)
    // every Metabase node not on the canvas is counted, whatever kept it off:
    // the second card is over the cap, the dashboard is not the chosen grain
    expect(data.omitted.metabase).toBe(2)
  })

  it('layers the pipeline in dependency order', () => {
    const data = overviewFor(index)
    const layer = (id: string) => data.nodes.find((n) => n.node.node_id === id)!.layer
    expect(layer('source.demo.app.events')).toBe(0)
    expect(layer('model.demo.stg_payments')).toBeLessThan(layer('model.demo.fct_revenue'))
    expect(layer('model.demo.fct_revenue')).toBeLessThan(layer('mb_dash::7'))
  })

  it('never draws an edge to a node it did not draw', () => {
    for (const metabase of ['none', 'dashboards', 'cards'] as const) {
      const data = overviewFor(index, { metabase })
      const drawn = new Set(ids(data))
      expect(data.edges.every((e) => drawn.has(e.from) && drawn.has(e.to))).toBe(true)
    }
  })
})
