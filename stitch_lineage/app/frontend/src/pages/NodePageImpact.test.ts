import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { buildConsequenceIndex } from '../lib/consequence'
import { fixtureGraph } from '../lib/fixture'
import { buildIndex } from '../lib/graph'
import type { StitchGraph } from '../types'
import { NodePage } from './NodePage'

const state = vi.hoisted(() => ({ current: {} as unknown }))
vi.mock('../data', () => ({ useStitch: () => state.current }))

function render(nodeId: string, graph: StitchGraph = fixtureGraph()) {
  const index = buildIndex(graph)
  state.current = {
    index,
    consequence: buildConsequenceIndex(index),
    meta: { staging_enabled: false, metabase_url: 'https://bi.example.test' },
  }
  return renderToStaticMarkup(createElement(NodePage, { nodeId })).replace(/<svg[\s\S]*?<\/svg>/g, '')
}

function impactSection(html: string) {
  const section = html.match(/<section class="panel-section impact-section">[\s\S]*?<\/section>/)?.[0]
  expect(section).toBeDefined()
  return section!
}

describe('the impact-first object answer', () => {
  it('puts dashboard/card consequences before a column’s path, definition, description and facts', () => {
    const graph = fixtureGraph()
    const column = graph.nodes.find((node) => node.node_id === 'model.demo.fct_revenue::net_revenue')!
    column.properties!.defined_as = { kind: 'expression', sql: 'amount - refunds', upstream: null }
    const html = render(column.node_id, graph)
    const impact = impactSection(html)
    expect(impact).toContain('Consumed by 2 cards on 1 dashboard.')
    expect(impact).toContain('Board dashboard')
    expect(impact).toContain('Revenue by country')
    expect(impact).toContain('Weekly revenue trend')
    expect(impact).toContain('https://bi.example.test/dashboard/7')
    expect(impact).toContain('https://bi.example.test/question/412')
    const after = html.slice(html.indexOf(impact) + impact.length)
    for (const evidence of ['Downstream paths', 'Defined as', 'Upstream —', 'Net of refunds', 'fact-list']) {
      expect(after).toContain(evidence)
      expect(impact).not.toContain(evidence)
    }
    // The same totals no longer reappear as a second summary below the answer.
    expect(html.match(/Consumed by 2 cards on 1 dashboard\./g)).toHaveLength(1)
    expect(impact).not.toMatch(/<details[^>]+\bopen(?:\s|>)/)
  })

  it.each(['model.demo.fct_revenue', 'source.demo.app.events'])(
    'leads with exact BI impact for %s before metadata and dependencies',
    (nodeId) => {
      const html = render(nodeId)
      const impact = impactSection(html)
      expect(impact).toContain('Consumed by 2 cards on 1 dashboard.')
      const after = html.slice(html.indexOf(impact) + impact.length)
      expect(after).toContain('fact-list')
      expect(after).not.toContain('star-canvas')
      if (nodeId === 'model.demo.fct_revenue') expect(after).toContain('Declared relationships')
      expect(after).toContain('Dependencies —')
      expect(after).toContain('Columns —')
    },
  )

  it('keeps distinct totals when one card appears on several dashboards, and labels archived consumers', () => {
    const graph = fixtureGraph()
    graph.nodes.push({ node_id: 'mb_dash::8', node_type: 'mb_dashboard', name: 'Archived review', properties: { archived: true } })
    graph.edges.push({ from: 'mb_card::418', to: 'mb_dash::8', edge_type: 'appears_on', confidence: 'exact', evidence: {} })
    const impact = impactSection(render('model.demo.fct_revenue::net_revenue', graph))
    expect(impact).toContain('Consumed by 2 cards on 2 dashboards.')
    expect(impact).toContain('class="conf-tag conf-parsed"')
    expect(impact.match(/class="archived-tag">archived/g)).toHaveLength(3)
    expect(impact.match(/Weekly revenue trend/g)).toHaveLength(2)
  })

  it('keeps cards with no dashboard visible and directly accessible', () => {
    const graph = fixtureGraph()
    graph.edges = graph.edges.filter((edge) => edge.edge_type !== 'appears_on')
    const impact = impactSection(render('model.demo.fct_revenue::net_revenue', graph))
    expect(impact).toContain('Consumed by 2 cards on 0 dashboards.')
    expect(impact).toContain('Cards on no dashboard')
    expect(impact).toContain('Revenue by country')
    expect(impact).toContain('https://bi.example.test/question/412')
  })

  it('qualifies an empty result and places a recorded trace gap beside the answer', () => {
    const graph = fixtureGraph()
    const column = graph.nodes.find((node) => node.node_id === 'model.demo.dim_users::user_id')!
    column.properties = { trace_status: 'untraced', trace_reason: 'no_compiled_code' }
    const impact = impactSection(render(column.node_id, graph))
    expect(impact).toContain('Not consumed by any Metabase card in this graph.')
    expect(impact).toContain('Unresolved lineage may hide other consumers.')
    expect(impact).toContain('Upstream lineage is incomplete: Model has no compiled SQL.')
    expect(impact).not.toContain('No entries in this displayed portion.')
    expect(impactSection(render('model.demo.dim_users', graph)))
      .toContain('1 column with incomplete upstream lineage in this table.')
  })

  it('does not label a literal’s lack of upstream columns as incomplete lineage', () => {
    const graph = fixtureGraph()
    graph.nodes.find((node) => node.node_id === 'model.demo.dim_users::user_id')!.properties = {
      trace_status: 'untraced', trace_reason: 'no_upstream_columns',
    }
    for (const id of ['model.demo.dim_users::user_id', 'model.demo.dim_users']) {
      expect(impactSection(render(id, graph))).not.toContain('chain-gap')
    }
  })

  it('retains the dashboard reverse view instead of presenting a terminal dashboard as zero impact', () => {
    const html = render('mb_dash::7')
    expect(html).not.toContain('impact-section')
    expect(html).toContain('Cards on this dashboard — 2')
    expect(html).toContain('Depends on 3 dbt columns across 3 models')
  })
})
