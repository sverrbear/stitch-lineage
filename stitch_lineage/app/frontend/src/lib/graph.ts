// Indexed, traversable view over graph.json. Pure TS (no DOM) so it is unit-testable.

import type { Confidence, GraphEdge, GraphNode, NodeType, StitchGraph } from '../types'

export interface GraphIndex {
  graph: StitchGraph
  /** graph.nodes plus any nodes synthesized for dangling edge endpoints (e.g. `model::*`). */
  nodes: GraphNode[]
  nodesById: Map<string, GraphNode>
  outEdges: Map<string, GraphEdge[]>
  inEdges: Map<string, GraphEdge[]>
  /** model/source node_id -> its column nodes (catalog order preserved). */
  columnsByModel: Map<string, GraphNode[]>
  synthesizedIds: Set<string>
}

/** `relates_to` is a declaration, not data flow — ERD only, never lineage/impact. */
export function isFlowEdge(edge: GraphEdge): boolean {
  return edge.edge_type !== 'relates_to'
}

/** Owning model/source unique_id for a dbt column node id, null for mb_* ids. */
export function modelIdOfColumn(nodeId: string): string | null {
  const sep = nodeId.indexOf('::')
  if (sep <= 0) return null
  const head = nodeId.slice(0, sep)
  return head.startsWith('mb_') ? null : head
}

/** Last path segment of a node id: `model.smitten.fct_matches::user_id` -> `user_id`. */
export function idTail(nodeId: string): string {
  const afterCols = nodeId.includes('::') ? nodeId.slice(nodeId.lastIndexOf('::') + 2) : nodeId
  const lastDot = afterCols.lastIndexOf('.')
  return lastDot >= 0 ? afterCols.slice(lastDot + 1) : afterCols
}

/** Numeric Metabase id from `mb_card::412` / `mb_dash::7` / `mb_field::9001`. */
export function mbId(nodeId: string): number | null {
  const sep = nodeId.indexOf('::')
  if (sep < 0) return null
  const n = Number(nodeId.slice(sep + 2))
  return Number.isFinite(n) ? n : null
}

export function metabaseLink(metabaseUrl: string | null, node: GraphNode): string | null {
  if (!metabaseUrl) return null
  const id = mbId(node.node_id)
  if (id === null) return null
  const base = metabaseUrl.replace(/\/+$/, '')
  if (node.node_type === 'mb_card') return `${base}/question/${id}`
  if (node.node_type === 'mb_dashboard') return `${base}/dashboard/${id}`
  return null
}

/**
 * Real graphs contain edges whose endpoint has no node entry — notably
 * `{model}::*` star pseudo-columns from sqlglot lineage (69 of them in the
 * reference Smitten graph). Dropping those edges would sever upstream chains,
 * so we synthesize a placeholder node instead and mark it `synthetic`.
 */
function synthesizeNode(nodeId: string, nodesById: Map<string, GraphNode>): GraphNode {
  const mbPrefixes: Array<[string, NodeType]> = [
    ['mb_field::', 'mb_field'],
    ['mb_card::', 'mb_card'],
    ['mb_dash::', 'mb_dashboard'],
  ]
  for (const [prefix, nodeType] of mbPrefixes) {
    if (nodeId.startsWith(prefix)) {
      return {
        node_id: nodeId,
        node_type: nodeType,
        name: nodeId.slice(prefix.length),
        properties: { synthetic: true },
      }
    }
  }
  const modelId = modelIdOfColumn(nodeId)
  if (modelId) {
    const parent = nodesById.get(modelId)
    const columnName = nodeId.slice(modelId.length + 2)
    return {
      node_id: nodeId,
      node_type: 'column',
      name: columnName,
      column: columnName,
      database: parent?.database ?? null,
      schema: parent?.schema ?? null,
      table: parent?.table ?? null,
      properties: { synthetic: true },
    }
  }
  return {
    node_id: nodeId,
    node_type: nodeId.startsWith('source.') ? 'source' : 'model',
    name: idTail(nodeId),
    properties: { synthetic: true },
  }
}

export function buildIndex(graph: StitchGraph): GraphIndex {
  const nodesById = new Map<string, GraphNode>()
  for (const node of graph.nodes) nodesById.set(node.node_id, node)

  const synthesizedIds = new Set<string>()
  for (const edge of graph.edges) {
    for (const endpoint of [edge.from, edge.to]) {
      if (!nodesById.has(endpoint)) {
        nodesById.set(endpoint, synthesizeNode(endpoint, nodesById))
        synthesizedIds.add(endpoint)
      }
    }
  }

  const nodes = [...nodesById.values()]
  const outEdges = new Map<string, GraphEdge[]>()
  const inEdges = new Map<string, GraphEdge[]>()
  for (const edge of graph.edges) {
    const out = outEdges.get(edge.from)
    if (out) out.push(edge)
    else outEdges.set(edge.from, [edge])
    const inn = inEdges.get(edge.to)
    if (inn) inn.push(edge)
    else inEdges.set(edge.to, [edge])
  }

  const columnsByModel = new Map<string, GraphNode[]>()
  for (const node of nodes) {
    if (node.node_type !== 'column') continue
    const modelId = modelIdOfColumn(node.node_id)
    if (!modelId) continue
    const cols = columnsByModel.get(modelId)
    if (cols) cols.push(node)
    else columnsByModel.set(modelId, [node])
  }

  return { graph, nodes, nodesById, outEdges, inEdges, columnsByModel, synthesizedIds }
}

// ---------------------------------------------------------------------------
// Traversal

/** Rank for bottleneck ("weakest hop") confidence along a path. Higher = stronger. */
export const CONFIDENCE_RANK: Record<Confidence, number> = {
  exact: 5,
  validated: 4,
  parsed: 3,
  declared: 3,
  inferred: 2,
  fuzzy: 1,
}

export function weakest(a: Confidence, b: Confidence): Confidence {
  return CONFIDENCE_RANK[a] <= CONFIDENCE_RANK[b] ? a : b
}

export interface Reach {
  node: GraphNode
  /** Minimum hop count from the start node. */
  depth: number
  /** Best-path bottleneck confidence: `exact` iff some path is exact end to end. */
  confidence: Confidence
}

export interface WalkResult {
  /** Reached nodes, excluding the start node itself. */
  reached: Map<string, Reach>
  /** Every flow edge traversed between reached nodes (and the start). */
  edges: GraphEdge[]
  truncated: boolean
}

export interface WalkOptions {
  maxDepth?: number
  maxNodes?: number
  edgeTypes?: ReadonlySet<string>
}

/**
 * BFS over flow edges (`relates_to` excluded) in one direction.
 * `down` follows from -> to (impact); `up` follows to -> from (provenance).
 * Confidence per node is the best over paths of the weakest hop on the path,
 * relaxed iteratively (the graph is small; simplicity wins over Dijkstra).
 */
export function walk(
  index: GraphIndex,
  startId: string,
  direction: 'up' | 'down',
  options: WalkOptions = {},
): WalkResult {
  const { maxDepth = 30, maxNodes = 2000, edgeTypes } = options
  const adjacency = direction === 'down' ? index.outEdges : index.inEdges
  const neighborOf = (edge: GraphEdge) => (direction === 'down' ? edge.to : edge.from)

  const reached = new Map<string, Reach>()
  const edges: GraphEdge[] = []
  const seenEdges = new Set<GraphEdge>()
  let truncated = false

  interface QueueItem {
    id: string
    depth: number
    confidence: Confidence
  }
  const queue: QueueItem[] = [{ id: startId, depth: 0, confidence: 'exact' }]

  while (queue.length > 0) {
    const item = queue.shift() as QueueItem
    if (item.depth >= maxDepth) continue
    for (const edge of adjacency.get(item.id) ?? []) {
      if (!isFlowEdge(edge)) continue
      if (edgeTypes && !edgeTypes.has(edge.edge_type)) continue
      const neighborId = neighborOf(edge)
      const node = index.nodesById.get(neighborId)
      if (!node) continue
      if (!seenEdges.has(edge)) {
        seenEdges.add(edge)
        edges.push(edge)
      }
      const pathConfidence = weakest(item.confidence, edge.confidence)
      const existing = reached.get(neighborId)
      if (existing) {
        let improved = false
        if (item.depth + 1 < existing.depth) {
          existing.depth = item.depth + 1
          improved = true
        }
        if (CONFIDENCE_RANK[pathConfidence] > CONFIDENCE_RANK[existing.confidence]) {
          existing.confidence = pathConfidence
          improved = true
        }
        if (improved) queue.push({ id: neighborId, depth: existing.depth, confidence: existing.confidence })
        continue
      }
      if (reached.size >= maxNodes) {
        truncated = true
        continue
      }
      reached.set(neighborId, { node, depth: item.depth + 1, confidence: pathConfidence })
      queue.push({ id: neighborId, depth: item.depth + 1, confidence: pathConfidence })
    }
  }

  return { reached, edges, truncated }
}

export function reachedOfType(result: WalkResult, nodeType: NodeType): Reach[] {
  const hits: Reach[] = []
  for (const reach of result.reached.values()) {
    if (reach.node.node_type === nodeType) hits.push(reach)
  }
  hits.sort((a, b) => a.depth - b.depth || a.node.name.localeCompare(b.node.name))
  return hits
}
