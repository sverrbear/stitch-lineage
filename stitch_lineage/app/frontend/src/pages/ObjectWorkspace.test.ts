import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { buildIndex } from '../lib/graph'
import { buildConsequenceIndex } from '../lib/consequence'
import type { GraphNode, StitchGraph } from '../types'
import type { ObjectRoute } from '../router'
import { ObjectWorkspace } from './ObjectWorkspace'
import { DescriptionEditor } from '../components/DescriptionEditor'
import { ObjectWorkspaceContext } from '../components/NodeIdentity'
import { ErdPage } from './ErdPage'
import { LineagePage } from './LineagePage'

const state = vi.hoisted(() => ({ current: {} as unknown }))
vi.mock('../data', () => ({ useStitch: () => state.current }))
// Hover subscriptions are client-only; these markup tests inspect navigation and focus.
vi.mock('../components/erdHighlight', async () => ({
  ...await vi.importActual('../components/erdHighlight'),
  useLitColumn: () => false,
}))

const model: GraphNode = { node_id: 'model.demo.orders', node_type: 'model', name: 'orders', schema: 'marts', properties: {} }
const column: GraphNode = { node_id: `${model.node_id}::amount`, node_type: 'column', name: 'amount', description: 'Money received.', properties: {} }
const card: GraphNode = { node_id: 'mb_card::1', node_type: 'mb_card', name: 'Revenue', properties: { archived: true } }
function load(nodes: GraphNode[] = [model, column, card]) {
  const index = buildIndex({ schema_version: 1, nodes, edges: [] } as StitchGraph)
  state.current = { index, consequence: buildConsequenceIndex(index), meta: { staging_enabled: false, metabase_url: 'https://metabase.example.test', erd_default_scope: null }, reload: vi.fn() }
}
function render(route: ObjectRoute) {
  load()
  return renderToStaticMarkup(createElement(ObjectWorkspace, { route }))
}

describe('one workspace for each object', () => {
  it('renders one qualified identity above Summary and Lineage links', () => {
    const html = render({ page: 'node', nodeId: column.node_id })
    expect(html.match(/<h1/g)).toHaveLength(1)
    expect(html).toContain('orders.</span>amount</h1>')
    expect(html).toContain('aria-current="page">Summary</a>')
    expect(html).toContain(`href="#/lineage/${encodeURIComponent(column.node_id)}"`)
    expect(html).toContain(`href="#/node/${encodeURIComponent(column.node_id)}/relationships"`)
    expect(html).not.toContain('View lineage')
    expect(html).toContain('Money received.')
  })

  it('keeps column identity and Summary link when its diagram is at table grain', () => {
    const html = render({ page: 'lineage', nodeId: column.node_id, grain: 'table' })
    expect(html).toContain('orders.</span>amount</h1>')
    expect(html).toContain(`href="#/node/${encodeURIComponent(column.node_id)}">Summary</a>`)
    expect(html).toContain(`href="#/lineage/${encodeURIComponent(column.node_id)}/table" aria-current="page">Lineage</a>`)
  })

  it('keeps external links and archive status without offering BI relationships', () => {
    const html = render({ page: 'node', nodeId: card.node_id })
    expect(html).toContain('archived-tag')
    expect(html).toContain('https://metabase.example.test/question/1')
    expect(html).not.toContain('>Relationships</a>')
  })

  it('recovers a missing object without offering invalid views', () => {
    const html = render({ page: 'lineage', nodeId: 'model.demo.missing', grain: 'column' })
    expect(html).toContain('Node not found')
    expect(html).not.toContain('object-views')
  })

  it('explains unavailable relationship views without opening an unrelated scope', () => {
    const semantic = { ...model, properties: { materialization: 'semantic_view' } }
    load([semantic])
    let html = renderToStaticMarkup(createElement(ObjectWorkspace, { route: { page: 'relationships', nodeId: model.node_id } }))
    expect(html).toContain('Relationships are available for tables and their columns.')
    expect(html).not.toContain('>Relationships</a>')
    load([{ ...model, schema: undefined }])
    html = renderToStaticMarkup(createElement(ObjectWorkspace, { route: { page: 'relationships', nodeId: model.node_id } }))
    expect(html).toContain('This table has no schema or dbt tag')
  })

  it('embeds the lineage canvas without repeating identity or a Details bounce link', () => {
    load()
    const html = renderToStaticMarkup(createElement(LineagePage, { nodeId: column.node_id, grain: 'column', embedded: true }))
    expect(html).toContain('<section class="graph-page">')
    expect(html).not.toContain('graph-toolbar-title')
    expect(html).not.toContain('>Details</a>')
    expect(html).toContain('Downstream: 0 cards')
  })

  it('starts the contextual diagram focused on its table and read-only without API capabilities', () => {
    load()
    const html = renderToStaticMarkup(createElement(ErdPage, { workspaceNodeId: column.node_id, scopeKind: 'schema', scopeValue: 'marts', focusId: model.node_id }))
    expect(html).toContain('<section class="graph-page">')
    expect(html).toContain('erd-unfocus')
    expect(html).toContain('Showing orders')
    expect(html).not.toContain('with-panel')
    expect(html).not.toContain('with-staged')
  })

  it('does not silently substitute a configured scope for a missing contextual scope', () => {
    load()
    const html = renderToStaticMarkup(createElement(ErdPage, { workspaceNodeId: model.node_id, scopeKind: 'schema', scopeValue: 'gone', focusId: model.node_id }))
    expect(html).toContain('This relationship scope is not present in this build.')
    expect(html).toContain(`href="#/node/${model.node_id}/relationships"`)
    expect(html).not.toContain('react-flow')
  })
})


describe('staged review stays in the selected object', () => {
  function stagedLink(nodes: GraphNode[], nodeId: string) {
    load(nodes)
    return renderToStaticMarkup(createElement(ObjectWorkspaceContext, { value: { nodeId, summaryActive: true } },
      createElement(DescriptionEditor, { entity: 'orders', column: null, applied: 'Old', staged: { id: 'edit', entity: 'orders', column: null, new_description: 'New' }, canStage: true, onChanged: () => {} }),
    ))
  }
  it('opens Relationships from a staged column description without losing the column', () => {
    expect(stagedLink([model, column], column.node_id)).toContain(`href="#/node/${encodeURIComponent(column.node_id)}/relationships"`)
  })
  it.each([
    { ...model, schema: undefined },
    { ...model, properties: { materialization: 'semantic_view' } },
  ])('keeps the legacy staged review reachable for an object without a diagram', (entry) => {
    expect(stagedLink([entry], model.node_id)).toContain('href="#/erd"')
  })
})
