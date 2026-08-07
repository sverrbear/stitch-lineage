// Scale/smoke test against a real graph (dev-public/dev-graph.json, created by
// scripts/make-dev-graph.mjs — gitignored). Skips silently when absent, so the
// suite stays green on machines without a local graph.

import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import type { StitchGraph } from '../types'
import { buildIndex } from './graph'
import { lineageFor, layoutLineage } from './lineage'
import { defaultScope, erdForScope, listScopes } from './erd'
import { GraphSearch } from './search'

const graphPath = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'dev-public', 'dev-graph.json')

describe.skipIf(!existsSync(graphPath))('real graph scale', () => {
  const graph: StitchGraph = existsSync(graphPath)
    ? JSON.parse(readFileSync(graphPath, 'utf8'))
    : { schema_version: 1, nodes: [], edges: [] }

  it('indexes thousands of nodes fast and synthesizes dangling endpoints', () => {
    const start = performance.now()
    const index = buildIndex(graph)
    const elapsed = performance.now() - start
    expect(index.nodes.length).toBeGreaterThanOrEqual(graph.nodes.length)
    expect(elapsed).toBeLessThan(500)
    // every edge endpoint resolvable after synthesis
    for (const edge of graph.edges) {
      expect(index.nodesById.has(edge.from)).toBe(true)
      expect(index.nodesById.has(edge.to)).toBe(true)
    }
  })

  it('search over the full graph stays interactive', () => {
    const index = buildIndex(graph)
    const search = new GraphSearch(index)
    const start = performance.now()
    const hits = search.search('event')
    const elapsed = performance.now() - start
    expect(hits.length).toBeGreaterThan(0)
    expect(elapsed).toBeLessThan(200)
  })

  it('lineage extraction returns a bounded subgraph, never the whole graph', () => {
    const index = buildIndex(graph)
    const someColumn = graph.nodes.find((n) => n.node_type === 'column' && index.outEdges.has(n.node_id))
    expect(someColumn).toBeDefined()
    const start = performance.now()
    const lineage = lineageFor(index, someColumn!.node_id)
    const positions = layoutLineage(lineage)
    const elapsed = performance.now() - start
    expect(lineage.nodes.length).toBeLessThan(graph.nodes.length)
    expect(positions.size).toBe(lineage.nodes.length)
    expect(elapsed).toBeLessThan(500)
  })

  it('ERD scopes partition the graph into digestible views', () => {
    const index = buildIndex(graph)
    const scopes = listScopes(index)
    expect(scopes.length).toBeGreaterThan(0)
    const scope = defaultScope(scopes)!
    const erd = erdForScope(index, scope)
    expect(erd.models.length).toBeGreaterThan(0)
    expect(erd.models.length).toBeLessThan(graph.nodes.length)
  })
})
