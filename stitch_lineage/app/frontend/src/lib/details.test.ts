import { describe, expect, it } from 'vitest'
import { biDetail, columnDetail, modelDetail } from './details'
import { buildIndex } from './graph'
import { fixtureGraph } from './fixture'
import type { GraphEdge, GraphNode, StitchGraph } from '../types'

const index = buildIndex(fixtureGraph())

/**
 * The broken-chain shapes the shared fixture deliberately lacks: a resolved card
 * whose fields nothing in dbt binds to, a native card, an MBQL card that
 * resolved no field, and an unbound field (#25). Kept local because the shared
 * fixture's node counts are asserted elsewhere.
 */
function gapGraph(): StitchGraph {
  const nodes: GraphNode[] = [
    { node_id: 'mb_field::301', node_type: 'mb_field', name: 'Refund Amount', column: 'REFUND_AMOUNT', table: 'FCT_REFUNDS', schema: 'MARTS', properties: {} },
    { node_id: 'mb_card::501', node_type: 'mb_card', name: 'Refund rate', properties: { query_type: 'query' } },
    { node_id: 'mb_card::502', node_type: 'mb_card', name: 'Handwritten funnel', properties: { query_type: 'native' } },
    { node_id: 'mb_card::503', node_type: 'mb_card', name: 'Constant only', properties: { query_type: 'query' } },
    { node_id: 'mb_dash::9', node_type: 'mb_dashboard', name: 'Ops', properties: {} },
  ]
  const edges: GraphEdge[] = [
    { from: 'mb_field::301', to: 'mb_card::501', edge_type: 'consumed_by', confidence: 'exact', evidence: {} },
    { from: 'mb_card::503', to: 'mb_dash::9', edge_type: 'appears_on', confidence: 'exact', evidence: {} },
  ]
  return { schema_version: 1, nodes, edges }
}

const gapIndex = buildIndex(gapGraph())

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

  it('reports no gap and no unbound field when the chain reaches dbt', () => {
    const detail = biDetail(index, 'mb_card::412')!
    expect(detail.gap).toBeNull()
    expect(detail.unboundFields).toEqual([])
  })

  it('keeps the resolved fields of a card whose fields bind to nothing in dbt', () => {
    const detail = biDetail(gapIndex, 'mb_card::501')!
    expect(detail.dependsOnColumns).toEqual([])
    expect(detail.gap).toBe('fields-unbound')
    expect(detail.fields.map((r) => r.node.node_id)).toEqual(['mb_field::301'])
    expect(detail.unboundFields.map((r) => r.node.node_id)).toEqual(['mb_field::301'])
  })

  it('separates a native card from an MBQL card that resolved no field', () => {
    expect(biDetail(gapIndex, 'mb_card::502')!.gap).toBe('native-unresolved')
    expect(biDetail(gapIndex, 'mb_card::503')!.gap).toBe('query-unresolved')
  })

  it('names the missing hop for an unbound field and an unresolved dashboard', () => {
    expect(biDetail(gapIndex, 'mb_field::301')!.gap).toBe('field-unbound')
    expect(biDetail(gapIndex, 'mb_dash::9')!.gap).toBe('dashboard-unresolved')
  })
})

describe('modelDetail', () => {
  it('computes columns, fan-in/fan-out and BI reach', () => {
    const detail = modelDetail(index, 'model.demo.fct_revenue')!
    expect(detail.columns.map((c) => c.name).sort()).toEqual(['net_revenue', 'user_id'])
    expect(detail.upstream.map((r) => r.node.node_id).sort()).toEqual([
      'model.demo.stg_payments',
      'source.demo.app.events',
    ])
    // the hop distance rides along, so the layers can be ordered by it (#82)
    expect(detail.upstream.find((r) => r.node.name === 'events')!.depth).toBe(2)
    expect(detail.downstream.map((r) => r.node.node_id)).toEqual(['model.demo.mart_board'])
    expect(detail.cards.length).toBe(2)
    expect(detail.dashboards.length).toBe(1)
    expect(detail.relationships).toHaveLength(1)
  })
})
