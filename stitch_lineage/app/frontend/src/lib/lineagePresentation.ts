import type { Confidence, GraphEdge, GraphNode } from '../types'
import { modelIdOfColumn, weakest, type GraphIndex } from './graph'
import type { LineageGraph } from './lineage'

export interface LineageBlock {
  node: GraphNode
  columns: GraphNode[]
  bindings: GraphNode[]
}
export interface PresentedEdge {
  from: string
  to: string
  confidence: Confidence
  evidence: GraphEdge[]
}
export interface PresentedLineage {
  blocks: LineageBlock[]
  edges: PresentedEdge[]
  focusId: string
}

/** Group only the selected trace, never all columns of a reached model. Binding
 * waypoints remain inspectable, and a selected or unbound field stays a node. */
export function presentLineage(
  index: GraphIndex,
  trace: LineageGraph,
  rootId: string,
  showBindings = false,
): PresentedLineage {
  const incoming = new Set(trace.edges.map((edge) => edge.to))
  const owner = new Map<string, string | null>()
  const blocks = new Map<string, LineageBlock>()
  for (const node of trace.nodes) {
    const modelId = node.node_type === 'column' ? modelIdOfColumn(node.node_id) : null
    const model = modelId ? index.nodesById.get(modelId) : undefined
    const hidden = node.node_type === 'mb_field' && node.node_id !== rootId && !showBindings && incoming.has(node.node_id)
    const id = hidden ? null : model?.node_id ?? node.node_id
    owner.set(node.node_id, id)
    if (id === null) continue
    let block = blocks.get(id)
    if (!block) {
      block = { node: model ?? node, columns: [], bindings: [] }
      blocks.set(id, block)
    }
    if (node.node_type === 'column') block.columns.push(node)
  }
  const outgoing = new Map<string, GraphEdge[]>()
  for (const edge of trace.edges) {
    const list = outgoing.get(edge.from) ?? []
    list.push(edge)
    outgoing.set(edge.from, list)
    const from = owner.get(edge.from)
    const field = index.nodesById.get(edge.to)
    if (from && field?.node_type === 'mb_field') {
      const bindings = blocks.get(from)?.bindings
      if (bindings && !bindings.some((item) => item.node_id === field.node_id)) bindings.push(field)
    }
  }
  const merged = new Map<string, PresentedEdge>()
  const add = (from: string, to: string, path: GraphEdge[]) => {
    if (from === to || !blocks.has(to)) return
    const confidence = path.reduce((grade, edge) => weakest(grade, edge.confidence), 'exact' as Confidence)
    const key = `${from}\0${to}`
    const existing = merged.get(key)
    if (existing) {
      // A grouped line must not promote its inferred contributions to exact.
      existing.confidence = weakest(existing.confidence, confidence)
      for (const edge of path) if (!existing.evidence.includes(edge)) existing.evidence.push(edge)
    } else merged.set(key, { from, to, confidence, evidence: [...path] })
  }
  for (const first of trace.edges) {
    const from = owner.get(first.from)
    if (!from) continue
    const queue: Array<{ edge: GraphEdge; path: GraphEdge[] }> = [{ edge: first, path: [first] }]
    const visited = new Set<string>()
    while (queue.length) {
      const { edge, path } = queue.shift()!
      const to = owner.get(edge.to)
      if (to) { add(from, to, path); continue }
      const grade = path.reduce((value, item) => weakest(value, item.confidence), 'exact' as Confidence)
      const visit = `${edge.to}\0${grade}`
      if (visited.has(visit)) continue
      visited.add(visit)
      for (const next of outgoing.get(edge.to) ?? []) queue.push({ edge: next, path: [...path, next] })
    }
  }
  for (const block of blocks.values()) {
    const selectedName = index.nodesById.get(rootId)?.name
    block.columns.sort((a, b) => a.node_id === rootId ? -1 : b.node_id === rootId ? 1 : a.name === selectedName ? -1 : b.name === selectedName ? 1 : a.name.localeCompare(b.name))
  }
  return {
    blocks: [...blocks.values()],
    edges: [...merged.values()],
    focusId: owner.get(rootId) ?? rootId,
  }
}

export function neighbours(view: PresentedLineage, id: string, direction: 'up' | 'down'): string[] {
  return [...new Set(view.edges.flatMap((edge) =>
    direction === 'up' ? edge.to === id ? [edge.from] : [] : edge.from === id ? [edge.to] : [],
  ))].sort()
}

/** An explicit frontier, not a silently truncated graph. Every hidden adjacent
 * group is counted on its visible neighbour and can be opened in small batches. */
export function initialLineageBlocks(view: PresentedLineage): Set<string> {
  return new Set([
    view.focusId,
    ...neighbours(view, view.focusId, 'up').slice(0, 2),
    ...neighbours(view, view.focusId, 'down').slice(0, 2),
  ])
}
