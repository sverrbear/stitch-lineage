import { describe, expect, it } from 'vitest'
import { biDetail, columnDetail, modelDetail } from './details'
import { buildIndex } from './graph'
import { fixtureGraph } from './fixture'

const index = buildIndex(fixtureGraph())

describe('columnDetail', () => {
  it('computes the "consumed by N cards on M dashboards" fan-out', () => {
    const detail = columnDetail(index, 'model.demo.fct_revenue::net_revenue')!
    expect(detail.cards.map((r) => r.node.node_id).sort()).toEqual(['mb_card::412', 'mb_card::418'])
    expect(detail.dashboards.map((r) => r.node.node_id)).toEqual(['mb_dash::7'])
    expect(detail.model?.node_id).toBe('model.demo.fct_revenue')
    expect(detail.upstreamSources.map((r) => r.node.node_id)).toEqual(['source.demo.app.events'])
  })

  it('flags non-exact downstream chains with the weakest hop', () => {
    const detail = columnDetail(index, 'model.demo.fct_revenue::net_revenue')!
    const card418 = detail.cards.find((r) => r.node.node_id === 'mb_card::418')!
    expect(card418.confidence).toBe('parsed')
    const card412 = detail.cards.find((r) => r.node.node_id === 'mb_card::412')!
    expect(card412.confidence).toBe('exact')
  })

  it('lists declared relationships without traversing them', () => {
    const detail = columnDetail(index, 'model.demo.fct_revenue::user_id')!
    expect(detail.relationships).toHaveLength(1)
    expect(detail.relationships[0].other?.node_id).toBe('model.demo.dim_users::user_id')
    expect(detail.relationships[0].validated).toBe(true)
    // relates_to target is NOT downstream
    expect(detail.downstreamColumns.every((r) => r.node.node_id !== 'model.demo.dim_users::user_id')).toBe(true)
  })
})

describe('biDetail (reverse view)', () => {
  it('resolves every dbt column a card ultimately depends on, with confidence', () => {
    const detail = biDetail(index, 'mb_card::412')!
    const columnIds = detail.dependsOnColumns.map((r) => r.node.node_id)
    expect(columnIds).toContain('model.demo.fct_revenue::net_revenue')
    expect(columnIds).toContain('model.demo.stg_payments::amount')
    expect(columnIds).toContain('source.demo.app.events::amount')
    const stg = detail.dependsOnColumns.find((r) => r.node.node_id === 'model.demo.stg_payments::amount')!
    expect(stg.confidence).toBe('parsed') // weakest hop on the chain
    expect(detail.dashboards.map((r) => r.node.node_id)).toEqual(['mb_dash::7'])
  })

  it('lists the cards on a dashboard', () => {
    const detail = biDetail(index, 'mb_dash::7')!
    expect(detail.cards.map((r) => r.node.node_id).sort()).toEqual(['mb_card::412', 'mb_card::418'])
    expect(detail.dependsOnColumns.length).toBeGreaterThan(0)
  })
})

describe('modelDetail', () => {
  it('computes columns, fan-in/fan-out and BI reach', () => {
    const detail = modelDetail(index, 'model.demo.fct_revenue')!
    expect(detail.columns.map((c) => c.name).sort()).toEqual(['net_revenue', 'user_id'])
    expect(detail.upstreamModels.map((m) => m.node_id).sort()).toEqual([
      'model.demo.stg_payments',
      'source.demo.app.events',
    ])
    expect(detail.downstreamModels.map((m) => m.node_id)).toEqual(['model.demo.mart_board'])
    expect(detail.cards.length).toBe(2)
    expect(detail.dashboards.length).toBe(1)
    expect(detail.relationships).toHaveLength(1)
  })
})
