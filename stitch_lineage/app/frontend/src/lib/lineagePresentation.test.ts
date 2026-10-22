import { describe, expect, it } from 'vitest'
import { fixtureGraph } from './fixture'
import { buildIndex } from './graph'
import { lineageFor } from './lineage'
import { initialLineageBlocks, neighbours, presentLineage } from './lineagePresentation'
import type { GraphEdge, GraphNode, StitchGraph } from '../types'

const model = 'model.demo.orders'
const root = `${model}::amount`
const node = (node_id: string, node_type: GraphNode['node_type']): GraphNode => ({ node_id, node_type, properties: {}, name: node_id.split('::').at(-1)! })
const edge = (from: string, to: string, confidence: GraphEdge['confidence'] = 'exact'): GraphEdge => ({ from, to, confidence, edge_type: 'feeds', evidence: {} })

describe('grouped lineage', () => {
  it('only includes columns in the selected trace and preserves binding evidence', () => {
    const index = buildIndex(fixtureGraph())
    const id = 'model.demo.fct_revenue::net_revenue'
    const trace = lineageFor(index, id)
    const view = presentLineage(index, trace, id)
    const block = view.blocks.find((block) => block.node.node_id === 'model.demo.fct_revenue')!
    expect(block.columns.map((column) => column.node_id)).toEqual([id])
    expect(view.focusId).toBe(block.node.node_id)
    expect(view.edges.every((item) => item.evidence.length > 0)).toBe(true)
  })

  it('does not label a mixed exact/inferred model connection exact', () => {
    const next = 'model.demo.daily'
    const graph = { schema_version: 1, nodes: [node(model, 'model'), node(root, 'column'), node(next, 'model'), node(`${next}::a`, 'column'), node(`${next}::b`, 'column')],
      edges: [edge(root, `${next}::a`), edge(root, `${next}::b`, 'inferred')] } as StitchGraph
    const index = buildIndex(graph)
    const view = presentLineage(index, lineageFor(index, root), root)
    expect(view.edges).toHaveLength(1)
    expect(view.edges[0].confidence).toBe('inferred')
    expect(view.edges[0].evidence).toHaveLength(2)
  })

  it('contracts a binding without losing its weaker grade, and keeps the selected field visible', () => {
    const field = 'mb_field::1', card = 'mb_card::1'
    const index = buildIndex({ schema_version: 1, nodes: [node(model, 'model'), node(root, 'column'), node(field, 'mb_field'), node(card, 'mb_card')], edges: [edge(root, field, 'fuzzy'), edge(field, card)] } as StitchGraph)
    const trace = lineageFor(index, root)
    const view = presentLineage(index, trace, root)
    expect(view.edges[0]).toMatchObject({ from: model, to: card, confidence: 'fuzzy' })
    expect(view.blocks.find((block) => block.node.node_id === model)?.bindings[0].node_id).toBe(field)
    expect(presentLineage(index, trace, root, true).blocks.some((block) => block.node.node_id === field)).toBe(true)
    expect(presentLineage(index, lineageFor(index, field), field).focusId).toBe(field)
  })

  it('opens a bounded neighbourhood and exposes every remaining branch for a large hub', () => {
    const cards = Array.from({ length: 80 }, (_, i) => node(`mb_card::${i}`, 'mb_card'))
    const index = buildIndex({ schema_version: 1, nodes: [node(model, 'model'), node(root, 'column'), ...cards], edges: cards.map((card) => edge(root, card.node_id)) } as StitchGraph)
    const view = presentLineage(index, lineageFor(index, root), root)
    const shown = initialLineageBlocks(view)
    expect(shown.size).toBe(3)
    expect(shown.has(model)).toBe(true)
    expect(neighbours(view, model, 'down').filter((id) => !shown.has(id))).toHaveLength(78)
    expect(view.blocks).toHaveLength(81)
  })
})
