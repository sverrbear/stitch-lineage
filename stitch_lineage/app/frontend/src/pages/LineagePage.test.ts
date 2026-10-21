// The click split on a lineage node (#140): the body re-traces, the NAME navigates.
// This covers where the name goes; the re-root itself is one `navigate(lineageHref)`
// call asserted by hand against the real graph (see the PR).

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { StitchData } from '../data'
import { buildConsequenceIndex } from '../lib/consequence'
import { buildIndex } from '../lib/graph'
import { lineageFor } from '../lib/lineage'
import type { Grain } from '../lib/rollup'
import type { GraphEdge, GraphNode, StitchGraph } from '../types'
import { LineagePage, titleTarget } from './LineagePage'

const node = (node_id: string, node_type: GraphNode['node_type'], name = 'x'): GraphNode =>
  ({ node_id, node_type, name }) as GraphNode

const MB = 'https://metabase.example.com'

describe('titleTarget', () => {
  it('sends a card and a dashboard to Metabase itself', () => {
    expect(titleTarget(node('mb_card::412', 'mb_card'), MB)).toEqual({
      href: `${MB}/question/412`,
      external: true,
    })
    expect(titleTarget(node('mb_dash::7', 'mb_dashboard'), MB)).toEqual({
      href: `${MB}/dashboard/7`,
      external: true,
    })
  })

  it('sends a dbt model or column to its own detail page', () => {
    for (const entry of [
      node('model.demo.fct_orders', 'model'),
      node('model.demo.fct_orders::customer_id', 'column'),
    ]) {
      const target = titleTarget(entry, MB)
      expect(target.external).toBe(false)
      expect(target.href).toContain(encodeURIComponent(entry.node_id))
    }
  })

  it('sends a FIELD to its detail page, not to a Metabase URL it cannot build', () => {
    // Metabase's field URLs need the database and table ids too, and the graph
    // carries only the field id — a link built from that would 404
    const target = titleTarget(node('mb_field::101', 'mb_field'), MB)
    expect(target.external).toBe(false)
    expect(target.href).toContain('101')
  })

  it('stays internal when there is no Metabase to link to', () => {
    // a static export built without a metabase url must not emit href="null/question/1"
    expect(titleTarget(node('mb_card::412', 'mb_card'), null).external).toBe(false)
  })
})

// Render the actual toolbar so the regression covers the count's wiring, its
// qualifiers and both grain routes, not just the already-tested consequence map.
let data: Pick<StitchData, 'index' | 'consequence' | 'meta'>
vi.mock('../data', () => ({ useStitch: () => data }))

function loadGraph(nodes: GraphNode[], edges: GraphEdge[]) {
  const index = buildIndex({ schema_version: 1, nodes, edges } as StitchGraph)
  data = {
    index,
    consequence: buildConsequenceIndex(index),
    meta: { metabase_url: null } as StitchData['meta'],
  }
}

const flow = (from: string, to: string, edge_type: GraphEdge['edge_type']): GraphEdge =>
  ({ from, to, edge_type, confidence: 'exact', evidence: {} })

function renderLineage(nodeId: string, grain: Grain = 'column') {
  return renderToStaticMarkup(createElement(LineagePage, { nodeId, grain }))
}

function headline(html: string) {
  return html.match(/<span class="graph-toolbar-impact">([^<]+)<\/span>/)?.[1]
}

function largeFanout(allOnFirstDashboard = false) {
  const modelId = 'model.demo.fct_orders'
  const columnId = `${modelId}::amount`
  const siblingId = `${modelId}::other_amount`
  const fieldId = 'mb_field::1'
  const nodes = [
    node(modelId, 'model'), node(columnId, 'column'), node(siblingId, 'column'),
    node(fieldId, 'mb_field'), node('mb_dash::1', 'mb_dashboard'),
    node('mb_dash::2', 'mb_dashboard'), node('mb_dash::3', 'mb_dashboard'),
    node('mb_card::999', 'mb_card'),
  ]
  const edges = [
    flow(columnId, fieldId, 'binds_to'),
    flow(siblingId, 'mb_card::999', 'consumed_by'),
    flow('mb_card::999', 'mb_dash::3', 'appears_on'),
  ]
  for (let i = 1; i <= 340; i++) {
    nodes.push(node(`mb_card::${i}`, 'mb_card'))
    edges.push(
      flow(fieldId, `mb_card::${i}`, 'consumed_by'),
      flow(`mb_card::${i}`, `mb_dash::${allOnFirstDashboard ? 1 : 1 + i % 2}`, 'appears_on'),
    )
  }
  loadGraph(nodes, edges)
  return { modelId, columnId }
}

describe('lineage headline consequence counts', () => {
  it.each(['column', 'table'] as const)('shows exact totals beyond the drawing cap at %s grain', (grain) => {
    const { columnId } = largeFanout()
    const drawing = lineageFor(data.index, columnId)
    expect(drawing.truncated).toBe(true)
    expect(drawing.nodes.filter((n) => n.node_type === 'mb_card')).toHaveLength(299)
    expect(drawing.nodes.filter((n) => n.node_type === 'mb_dashboard')).toHaveLength(0)

    const html = renderLineage(columnId, grain)
    expect(headline(html)).toBe('Downstream: 340 cards · 2 dashboards')
    expect(html).toContain('drawing truncated; counts are exact')
    expect(html).not.toContain('graph-toolbar-impact partial')
    expect(html).not.toContain('lower bounds')
  })

  it('keeps a selected column distinct from its model at table grain', () => {
    const { modelId, columnId } = largeFanout()
    expect(headline(renderLineage(columnId, 'table'))).toBe('Downstream: 340 cards · 2 dashboards')
    // Selecting the model itself includes its other column's unique card/dashboard.
    expect(headline(renderLineage(modelId, 'table'))).toBe('Downstream: 341 cards · 3 dashboards')
  })

  it.each(['column', 'table'] as const)('excludes the root and upstream cards at %s grain', (grain) => {
    loadGraph(
      [node('mb_card::1', 'mb_card'), node('mb_card::2', 'mb_card'),
        node('mb_card::3', 'mb_card'), node('mb_dash::1', 'mb_dashboard')],
      [flow('mb_card::1', 'mb_card::2', 'consumed_by'),
        flow('mb_card::2', 'mb_card::3', 'consumed_by'),
        flow('mb_card::3', 'mb_dash::1', 'appears_on')],
    )
    const drawing = lineageFor(data.index, 'mb_card::2')
    expect(drawing.nodes.filter((n) => n.node_type === 'mb_card')).toHaveLength(3)
    const html = renderLineage('mb_card::2', grain)
    expect(headline(html)).toBe('Downstream: 1 card · 1 dashboard')
    expect(html).not.toContain('drawing truncated')
    expect(headline(renderLineage('mb_dash::1', grain))).toBe('Downstream: 0 cards · 0 dashboards')
  })

  it('does not qualify exact zeroes when only the upstream drawing is capped', () => {
    largeFanout(true)
    expect(lineageFor(data.index, 'mb_dash::1').truncated).toBe(true)
    const html = renderLineage('mb_dash::1')
    expect(headline(html)).toBe('Downstream: 0 cards · 0 dashboards')
    expect(html).toContain('drawing truncated; counts are exact')
  })
})
