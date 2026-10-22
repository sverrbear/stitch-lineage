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
import { LineagePage } from './LineagePage'

const state = vi.hoisted(() => ({ current: {} as unknown }))
vi.mock('../data', () => ({ useStitch: () => state.current }))

const model: GraphNode = { node_id: 'model.demo.orders', node_type: 'model', name: 'orders', schema: 'marts', properties: {} }
const column: GraphNode = { node_id: `${model.node_id}::amount`, node_type: 'column', name: 'amount', description: 'Money received.', properties: {} }
const card: GraphNode = { node_id: 'mb_card::1', node_type: 'mb_card', name: 'Revenue', properties: { archived: true } }
function load(nodes: GraphNode[] = [model, column, card]) {
  const index = buildIndex({ schema_version: 1, nodes, edges: [] } as StitchGraph)
  state.current = { index, consequence: buildConsequenceIndex(index), meta: { staging_enabled: false, metabase_url: 'https://metabase.example.test' }, reload: vi.fn() }
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
    expect(html).not.toContain('>Relationships</a>')
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

  it('embeds the lineage canvas without repeating identity or a Details bounce link', () => {
    load()
    const html = renderToStaticMarkup(createElement(LineagePage, { nodeId: column.node_id, grain: 'column', embedded: true }))
    expect(html).toContain('<section class="graph-page">')
    expect(html).not.toContain('graph-toolbar-title')
    expect(html).not.toContain('>Details</a>')
    expect(html).toContain('Downstream: 0 cards')
  })
})


describe('staged review survives canvas retirement', () => {
  function stagedLink(nodes: GraphNode[], nodeId: string) {
    load(nodes)
    return renderToStaticMarkup(createElement(ObjectWorkspaceContext, { value: { nodeId, summaryActive: true } },
      createElement(DescriptionEditor, { entity: 'orders', column: null, applied: 'Old', staged: { id: 'edit', entity: 'orders', column: null, new_description: 'New' }, canStage: true, onChanged: () => {} }),
    ))
  }
  it('links staged descriptions to the independent review page', () => {
    expect(stagedLink([model, column], column.node_id)).toContain('href="#/staged"')
  })
  it.each([
    { ...model, schema: undefined },
    { ...model, properties: { materialization: 'semantic_view' } },
  ])('keeps staged review reachable for an object without a diagram', (entry) => {
    expect(stagedLink([entry], model.node_id)).toContain('href="#/staged"')
  })
})
