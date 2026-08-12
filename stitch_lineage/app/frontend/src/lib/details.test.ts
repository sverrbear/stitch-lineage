import { describe, expect, it } from 'vitest'
import { biDetail, columnDetail, dataTypeLabel, modelDetail } from './details'
import { buildIndex } from './graph'
import { fixtureGraph } from './fixture'
import type { DataTypeSource, GraphEdge, GraphNode, StitchGraph } from '../types'

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
    // the semantic view is downstream like anything else: the ERD stops drawing it
    // as a table (#191), the lineage never stops reporting the dependency
    expect(detail.downstream.map((r) => r.node.node_id).sort()).toEqual([
      'model.demo.mart_board',
      'model.demo.sv_revenue',
    ])
    expect(detail.cards.length).toBe(2)
    expect(detail.dashboards.length).toBe(1)
    expect(detail.relationships).toHaveLength(1)
  })
})

// #122: a bare "unknown" data type reads as a broken tool. The graph knows why.
describe('dataTypeLabel', () => {
  const column = (
    data_type: string | null,
    reason?: string,
    data_type_source?: DataTypeSource,
  ): GraphNode => ({
    node_id: 'model.demo.fct_revenue::net_revenue',
    node_type: 'column',
    name: 'net_revenue',
    data_type,
    data_type_source,
    properties: reason ? { unknown_type_reason: reason } : {},
  })

  it('passes a real type through untouched', () => {
    expect(dataTypeLabel(column('NUMBER'))).toEqual({ text: 'NUMBER', hint: null, source: null })
  })

  it('says the model was never built when the catalog has no entry for it', () => {
    const label = dataTypeLabel(column(null, 'relation_not_in_catalog'))
    expect(label.text).toBe('unknown — not in catalog')
    expect(label.hint).toContain('dbt docs generate')
  })

  it('distinguishes an undeployed column from a missing model', () => {
    const label = dataTypeLabel(column(null, 'column_not_in_catalog'))
    expect(label.text).toBe('unknown — not in catalog yet')
    expect(label.hint).toContain('undeployed, not lost')
  })

  it('claims no reason it does not have', () => {
    expect(dataTypeLabel(column(null))).toEqual({ text: 'unknown', hint: null, source: null })
    expect(dataTypeLabel(column(null, 'something_new'))).toEqual({
      text: 'unknown',
      hint: null,
      source: null,
    })
  })

  // #149: types now come from three places, so the line has to say which one answered
  it('names the Metabase sync as the source of a type the catalog never had', () => {
    const label = dataTypeLabel(column('NUMBER(38,0)', undefined, 'metabase'))
    expect(label.text).toBe('NUMBER(38,0)')
    expect(label.source?.label).toBe('from Metabase sync')
    expect(label.source?.hint).toContain('binds to')
  })

  it('marks an inferred type as a parse result rather than a warehouse fact', () => {
    const label = dataTypeLabel(column('DOUBLE', undefined, 'inferred'))
    expect(label.source?.label).toBe('inferred from expression')
    expect(label.source?.hint).toContain('sqlglot')
  })

  it('names the catalog for the types that always worked', () => {
    expect(dataTypeLabel(column('FLOAT', undefined, 'catalog')).source?.label).toBe(
      'from the dbt catalog',
    )
  })

  it('shows no provenance for an unknown type, whatever the graph claims', () => {
    // a source without a type is a contradiction; the type is what the subtext qualifies
    expect(dataTypeLabel(column(null, undefined, 'metabase')).source).toBeNull()
  })
})
