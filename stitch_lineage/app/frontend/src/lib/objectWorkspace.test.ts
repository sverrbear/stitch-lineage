import { describe, expect, it } from 'vitest'
import { buildIndex } from './graph'
import { erdForScope, focusErd } from './erd'
import { relationshipScope, relationshipTable } from './objectWorkspace'
import type { GraphNode, StitchGraph } from '../types'

function table(node_id: string, schema?: string, properties = {}): GraphNode {
  return { node_id, node_type: node_id.startsWith('source.') ? 'source' : 'model', name: node_id, schema, properties }
}
function indexFor(nodes: GraphNode[]) {
  return buildIndex({ schema_version: 1, nodes, edges: [] } as StitchGraph)
}

describe('contextual relationship entry', () => {
  it.each(['model.demo.orders', 'seed.demo.countries', 'snapshot.demo.accounts', 'source.demo.raw.orders'])('opens the selected table scope for %s', (id) => {
    const node = table(id, 'warehouse')
    const index = indexFor([node, table('model.demo.busiest', 'other')])
    expect(relationshipTable(index, node)).toBe(node)
    const scope = relationshipScope(index, node)!
    expect(scope).toMatchObject({ kind: 'schema', value: 'warehouse' })
    // No declared joins is still a valid, one-table diagram.
    expect(focusErd(erdForScope(index, scope), id).models.map((entry) => entry.node.node_id)).toEqual([id])
  })

  it('resolves a column owner without widening the selected object to a sibling', () => {
    const owner = table('model.demo.orders', 'marts')
    const column: GraphNode = { node_id: `${owner.node_id}::amount`, node_type: 'column', name: 'amount', properties: {} }
    const index = indexFor([owner, column])
    expect(relationshipTable(index, column)).toBe(owner)
    expect(column.node_id).toBe('model.demo.orders::amount')
  })

  it('never offers table relationships for semantic views or BI objects', () => {
    const semantic = table('model.demo.metrics', 'marts', { materialization: 'semantic_view' })
    const column: GraphNode = { node_id: `${semantic.node_id}::amount`, node_type: 'column', name: 'amount', properties: {} }
    const card: GraphNode = { node_id: 'mb_card::1', node_type: 'mb_card', name: 'Revenue', properties: {} }
    const index = indexFor([semantic, column, card])
    for (const node of [semantic, column, card]) expect(relationshipTable(index, node)).toBeNull()
  })

  it('uses a containing tag when schema is absent and does not invent a global fallback', () => {
    const tagged = table('model.demo.tagged', undefined, { tags: ['finance'] })
    const bare = table('model.demo.bare')
    const index = indexFor([tagged, bare, table('model.demo.other', 'busy')])
    expect(relationshipScope(index, tagged)).toMatchObject({ kind: 'tag', value: 'finance' })
    expect(relationshipScope(index, bare)).toBeNull()
  })

  it('keeps related tables across schema boundaries in the focused diagram', () => {
    const orders = table('model.demo.orders', 'marts')
    const customers = table('model.demo.customers', 'dimensions')
    const index = buildIndex({ schema_version: 1, nodes: [orders, customers], edges: [
      { from: `${orders.node_id}::customer_id`, to: `${customers.node_id}::id`, edge_type: 'relates_to', confidence: 'declared', evidence: {} },
    ] } as StitchGraph)
    const diagram = focusErd(erdForScope(index, relationshipScope(index, orders)!), orders.node_id)
    expect(diagram.models.map((entry) => entry.node.node_id).sort()).toEqual([customers.node_id, orders.node_id])
    expect(diagram.relationships).toHaveLength(1)
  })
})
