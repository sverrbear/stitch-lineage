import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { GraphEdge, GraphNode, StitchGraph } from '../types'
import { buildIndex, walk } from '../lib/graph'
import { buildConsequenceIndex } from '../lib/consequence'
import { biDetail, columnDetail, modelDetail } from '../lib/details'
import { NodePage } from './NodePage'

const state = vi.hoisted(() => ({ current: {} as unknown }))
vi.mock('../data', () => ({ useStitch: () => state.current }))

const node = (node_id: string, node_type: GraphNode['node_type']): GraphNode =>
  ({ node_id, node_type, name: node_id.split('::').at(-1)!, properties: {} })
const edge = (from: string, to: string, edge_type: GraphEdge['edge_type']): GraphEdge =>
  ({ from, to, edge_type, confidence: 'exact', evidence: {} })

function chain(length = 33): StitchGraph {
  const nodes: GraphNode[] = []
  const edges: GraphEdge[] = []
  for (let i = 0; i < length; i++) {
    nodes.push(node(`model.demo.stage_${i}`, 'model'), node(`model.demo.stage_${i}::amount`, 'column'))
    if (i) {
      edges.push(edge(`model.demo.stage_${i - 1}`, `model.demo.stage_${i}`, 'references'))
      edges.push(edge(`model.demo.stage_${i - 1}::amount`, `model.demo.stage_${i}::amount`, 'feeds'))
    }
  }
  nodes.push(node('mb_card::1', 'mb_card'), node('mb_dash::1', 'mb_dashboard'))
  edges.push(edge(`model.demo.stage_${length - 1}::amount`, 'mb_card::1', 'consumed_by'))
  edges.push(edge('mb_card::1', 'mb_dash::1', 'appears_on'))
  return { schema_version: 1, nodes, edges }
}

function fanout(size = 2100): StitchGraph {
  const nodes = [node('model.demo.orders', 'model'), node('model.demo.orders::amount', 'column'),
    node('mb_field::1', 'mb_field'), node('mb_dash::1', 'mb_dashboard')]
  const edges = [edge('model.demo.orders::amount', 'mb_field::1', 'binds_to')]
  for (let i = 0; i < size; i++) {
    const id = `mb_card::${i}`
    nodes.push(node(id, 'mb_card'))
    edges.push(edge('mb_field::1', id, 'consumed_by'), edge(id, 'mb_dash::1', 'appears_on'))
  }
  return { schema_version: 1, nodes, edges }
}

function render(graph: StitchGraph, nodeId: string) {
  const index = buildIndex(graph)
  state.current = { index, consequence: buildConsequenceIndex(index), meta: { staging_enabled: false } }
  return renderToStaticMarkup(createElement(NodePage, { nodeId })).replace(/<svg[\s\S]*?<\/svg>/g, '')
}

describe('bounded detail lists with complete summaries', () => {
  it('reports a depth-limited downstream list without losing distant consumers', () => {
    const graph = chain()
    const detail = columnDetail(buildIndex(graph), 'model.demo.stage_0::amount')!
    expect(detail.downstreamColumns).toHaveLength(30)
    expect(detail.cards).toHaveLength(0)
    expect(detail.downstreamTruncated).toBe(true)
    expect(detail.upstreamTruncated).toBe(false)
    expect(detail.counts).toMatchObject({ downstreamModels: 32, cards: 1, dashboards: 1 })
    const html = render(graph, 'model.demo.stage_0::amount')
    expect(html).toContain('Consumed by 1 card on 1 dashboard.')
    expect(html).toContain('Only part of the result is listed below; summary counts are complete.')
    expect(html).not.toContain('Not consumed by any Metabase card')
    const impact = html.match(/<section class="panel-section impact-section">[\s\S]*?<\/section>/)![0]
    expect(impact).toContain('Consumed by 1 card on 1 dashboard.')
    expect(impact).toContain('Only part of the result is listed below; summary counts are complete.')
    expect(impact).toContain('No entries in this displayed portion.')
  })

  it('counts upstream columns beyond the depth cap and keeps the reverse chain resolved', () => {
    const graph = chain()
    const detail = biDetail(buildIndex(graph), 'mb_card::1')!
    expect(detail.dependsOnColumns).toHaveLength(30)
    expect(detail.counts.columns).toBe(33)
    expect(detail.counts.models).toBe(33)
    expect(detail.upstreamTruncated).toBe(true)
    expect(detail.downstreamTruncated).toBe(false)
    expect(detail.gap).toBeNull()
    expect(render(graph, 'mb_card::1')).toContain('Depends on 33 dbt columns across 33 models')
  })

  it('counts model references beyond the depth cap in either direction', () => {
    const index = buildIndex(chain())
    const first = modelDetail(index, 'model.demo.stage_0')!
    const last = modelDetail(index, 'model.demo.stage_32')!
    expect(first.downstream).toHaveLength(30)
    expect(first.counts).toEqual({ upstream: 0, downstream: 32 })
    expect(first.downstreamTruncated).toBe(true)
    expect(first.upstreamTruncated).toBe(false)
    expect(last.upstream).toHaveLength(30)
    expect(last.counts).toEqual({ upstream: 32, downstream: 0 })
    expect(last.upstreamTruncated).toBe(true)
    expect(last.downstreamTruncated).toBe(false)
  })

  it('uses exact indexed model BI totals while retaining bounded card groups', () => {
    const graph = fanout()
    const detail = modelDetail(buildIndex(graph), 'model.demo.orders')!
    expect(detail.cards).toHaveLength(1999)
    expect(detail.usageTruncated).toBe(true)
    const html = render(graph, 'model.demo.orders')
    expect(html).toContain('2100 cards')
    expect(html).toContain('1 dashboard')
    expect(html).toContain('Only part of the result is listed below; summary counts are complete.')
    const impact = html.match(/<section class="panel-section impact-section">[\s\S]*?<\/section>/)![0]
    expect(impact).toContain('Consumed by 2100 cards on 1 dashboard.')
    expect(impact).toContain('Only part of the result is listed below; summary counts are complete.')
  })

  it('counts all dashboard cards and dbt dependencies even if the list cap hides every column', () => {
    const graph = fanout()
    const detail = biDetail(buildIndex(graph), 'mb_dash::1')!
    expect(detail.cards).toHaveLength(2000)
    expect(detail.dependsOnColumns).toHaveLength(0)
    expect(detail.upstreamTruncated).toBe(true)
    expect(detail.gap).toBeNull()
    expect(detail.counts).toMatchObject({ cards: 2100, columns: 1, models: 1, fields: 1 })
    const html = render(graph, 'mb_dash::1')
    expect(html).toContain('Cards on this dashboard — 2100')
    expect(html).toContain('Depends on 1 dbt column across 1 model')
    expect(html).toContain('No entries in this displayed portion.')
    expect(html).not.toContain('no dbt column resolved')
  })

  it('counts field fanout past the node cap and shows the omitted dashboard section', () => {
    const graph = fanout()
    const detail = biDetail(buildIndex(graph), 'mb_field::1')!
    expect(detail.cards).toHaveLength(2000)
    expect(detail.dashboards).toHaveLength(0)
    expect(detail.counts).toMatchObject({ cards: 2100, dashboards: 1 })
    expect(detail.downstreamTruncated).toBe(true)
    expect(detail.upstreamTruncated).toBe(false)
    const html = render(graph, 'mb_field::1')
    expect(html).toContain('2100 cards')
    expect(html).toContain('Appears on 1 dashboard')
    expect(html).toContain('No entries in this displayed portion.')
  })

  it('does not mark a complete graph at exactly the depth limit as partial', () => {
    const graph = chain(31)
    graph.nodes = graph.nodes.filter((n) => !n.node_type.startsWith('mb_'))
    graph.edges = graph.edges.filter((e) => e.edge_type === 'feeds' || e.edge_type === 'references')
    const detail = columnDetail(buildIndex(graph), 'model.demo.stage_0::amount')!
    expect(detail.downstreamColumns).toHaveLength(30)
    expect(detail.downstreamTruncated).toBe(false)
  })

  it('terminates cyclic complete traversals and excludes the root from its own dependencies', () => {
    const graph = chain()
    graph.edges.push(edge('model.demo.stage_32::amount', 'model.demo.stage_0::amount', 'feeds'))
    graph.edges.push(edge('model.demo.stage_32', 'model.demo.stage_0', 'references'))
    const index = buildIndex(graph)
    const detail = columnDetail(index, 'model.demo.stage_0::amount')!
    expect(detail.upstreamColumns.some((r) => r.node.node_id === detail.node.node_id)).toBe(false)
    expect(detail.counts.upstreamColumns).toBe(32)
    expect(detail.counts.cards).toBe(1)
    expect(modelDetail(index, 'model.demo.stage_0')!.counts).toEqual({ upstream: 32, downstream: 32 })
  })

  it('does not call a cycle back to an already reached node a depth truncation', () => {
    const graph: StitchGraph = { schema_version: 1,
      nodes: [node('a', 'column'), node('b', 'column'), node('c', 'column')],
      edges: [edge('a', 'b', 'feeds'), edge('b', 'c', 'feeds'), edge('c', 'a', 'feeds'), edge('c', 'b', 'feeds')] }
    const result = walk(buildIndex(graph), 'a', 'down', { maxDepth: 2 })
    expect([...result.reached.keys()]).toEqual(['b', 'c'])
    expect(result.truncated).toBe(false)
  })
})
