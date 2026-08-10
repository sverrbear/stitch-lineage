// Per-node-type detail computations for the routed detail panels (spec §9).
// Pure TS, unit-testable.

import type { GraphEdge, GraphNode } from '../types'
import {
  type GraphIndex,
  type Reach,
  modelIdOfColumn,
  reachedOfType,
  walk,
} from './graph'

export interface RelationshipRef {
  edge: GraphEdge
  /** The column on the other side of the relationship. */
  other: GraphNode | null
  direction: 'outgoing' | 'incoming'
  validated: boolean
}

function relationshipsTouching(index: GraphIndex, columnIds: Set<string>): RelationshipRef[] {
  const refs: RelationshipRef[] = []
  for (const edge of index.graph.edges) {
    if (edge.edge_type !== 'relates_to') continue
    if (columnIds.has(edge.from)) {
      refs.push({
        edge,
        other: index.nodesById.get(edge.to) ?? null,
        direction: 'outgoing',
        validated: edge.confidence === 'validated',
      })
    } else if (columnIds.has(edge.to)) {
      refs.push({
        edge,
        other: index.nodesById.get(edge.from) ?? null,
        direction: 'incoming',
        validated: edge.confidence === 'validated',
      })
    }
  }
  return refs
}

// ---------------------------------------------------------------------------

export interface ColumnDetail {
  node: GraphNode
  model: GraphNode | null
  upstreamColumns: Reach[]
  upstreamSources: Reach[]
  downstreamColumns: Reach[]
  /** Distinct downstream models (via the columns they own). */
  downstreamModels: GraphNode[]
  fields: Reach[]
  cards: Reach[]
  dashboards: Reach[]
  relationships: RelationshipRef[]
  truncated: boolean
}

export function columnDetail(index: GraphIndex, nodeId: string): ColumnDetail | null {
  const node = index.nodesById.get(nodeId)
  if (!node) return null
  const modelId = modelIdOfColumn(nodeId)
  const model = modelId ? (index.nodesById.get(modelId) ?? null) : null

  const up = walk(index, nodeId, 'up')
  const down = walk(index, nodeId, 'down')

  const upstreamColumns = reachedOfType(up, 'column')
  const upstreamSources: Reach[] = []
  const seenSourceModels = new Set<string>()
  for (const reach of upstreamColumns) {
    const owner = modelIdOfColumn(reach.node.node_id)
    if (!owner || seenSourceModels.has(owner)) continue
    const ownerNode = index.nodesById.get(owner)
    if (ownerNode?.node_type === 'source') {
      seenSourceModels.add(owner)
      upstreamSources.push({ ...reach, node: ownerNode })
    }
  }

  const downstreamColumns = reachedOfType(down, 'column')
  const downstreamModels: GraphNode[] = []
  const seenModels = new Set<string>()
  for (const reach of downstreamColumns) {
    const owner = modelIdOfColumn(reach.node.node_id)
    if (!owner || seenModels.has(owner)) continue
    seenModels.add(owner)
    const ownerNode = index.nodesById.get(owner)
    if (ownerNode) downstreamModels.push(ownerNode)
  }

  return {
    node,
    model,
    upstreamColumns,
    upstreamSources,
    downstreamColumns,
    downstreamModels,
    fields: reachedOfType(down, 'mb_field'),
    cards: reachedOfType(down, 'mb_card'),
    dashboards: reachedOfType(down, 'mb_dashboard'),
    relationships: relationshipsTouching(index, new Set([nodeId])),
    truncated: up.truncated || down.truncated,
  }
}

// ---------------------------------------------------------------------------

/**
 * Why the reverse view found no dbt column. The chain is
 * `column -binds_to-> mb_field -consumed_by-> mb_card -appears_on-> mb_dashboard`,
 * so an empty result always means one specific hop is missing — and naming that
 * hop is the difference between a documented gap and a panel that looks broken
 * (#25).
 */
export type ChainGap =
  /** Fields resolved, but no dbt column binds to any of them. */
  | 'fields-unbound'
  /** Native SQL card: not resolved into column lineage in this build (#32). */
  | 'native-unresolved'
  /** MBQL card whose query yielded no field ref at all. */
  | 'query-unresolved'
  /** An mb_field no dbt column binds to. */
  | 'field-unbound'
  /** A dashboard whose cards resolved no field between them. */
  | 'dashboard-unresolved'

/** A field is bound when a dbt column flows into it — the `binds_to` hop. */
function fieldIsBound(index: GraphIndex, fieldId: string): boolean {
  for (const edge of index.inEdges.get(fieldId) ?? []) {
    if (edge.edge_type !== 'binds_to') continue
    if (index.nodesById.get(edge.from)?.node_type === 'column') return true
  }
  return false
}

function chainGap(node: GraphNode, fields: Reach[], columns: Reach[]): ChainGap | null {
  if (columns.length > 0) return null
  // A field's own up-walk yields columns, never fields, so it is asked first.
  if (node.node_type === 'mb_field') return 'field-unbound'
  if (fields.length > 0) return 'fields-unbound'
  if (node.node_type === 'mb_dashboard') return 'dashboard-unresolved'
  return node.properties?.query_type === 'native' ? 'native-unresolved' : 'query-unresolved'
}

export interface BiDetail {
  node: GraphNode
  /** Every dbt column this visual ultimately depends on, with bottleneck confidence. */
  dependsOnColumns: Reach[]
  dependsOnModels: GraphNode[]
  fields: Reach[]
  /** Fields upstream of this node that no dbt column binds to: where the chain stops. */
  unboundFields: Reach[]
  cards: Reach[]
  dashboards: Reach[]
  /** Set only when `dependsOnColumns` is empty: which hop is missing. */
  gap: ChainGap | null
}

/** Detail for mb_card, mb_dashboard and mb_field nodes (the reverse view). */
export function biDetail(index: GraphIndex, nodeId: string): BiDetail | null {
  const node = index.nodesById.get(nodeId)
  if (!node) return null
  const up = walk(index, nodeId, 'up')
  const down = walk(index, nodeId, 'down')

  const dependsOnColumns = reachedOfType(up, 'column')
  const fields = reachedOfType(up, 'mb_field')
  const dependsOnModels: GraphNode[] = []
  const seen = new Set<string>()
  for (const reach of dependsOnColumns) {
    const owner = modelIdOfColumn(reach.node.node_id)
    if (!owner || seen.has(owner)) continue
    seen.add(owner)
    const ownerNode = index.nodesById.get(owner)
    if (ownerNode) dependsOnModels.push(ownerNode)
  }

  return {
    node,
    dependsOnColumns,
    dependsOnModels,
    fields,
    unboundFields: fields.filter((reach) => !fieldIsBound(index, reach.node.node_id)),
    cards: node.node_type === 'mb_dashboard' ? reachedOfType(up, 'mb_card') : reachedOfType(down, 'mb_card'),
    dashboards: reachedOfType(down, 'mb_dashboard'),
    gap: chainGap(node, fields, dependsOnColumns),
  }
}

// ---------------------------------------------------------------------------

export interface ModelDetail {
  node: GraphNode
  columns: GraphNode[]
  upstreamModels: GraphNode[]
  downstreamModels: GraphNode[]
  cards: Reach[]
  dashboards: Reach[]
  relationships: RelationshipRef[]
}

export function modelDetail(index: GraphIndex, nodeId: string): ModelDetail | null {
  const node = index.nodesById.get(nodeId)
  if (!node) return null
  const columns = index.columnsByModel.get(nodeId) ?? []

  const refTypes = new Set(['references'])
  const upstreamModels = [...walk(index, nodeId, 'up', { edgeTypes: refTypes }).reached.values()]
    .map((r) => r.node)
    .filter((n) => n.node_type === 'model' || n.node_type === 'source')
  const downstreamModels = [...walk(index, nodeId, 'down', { edgeTypes: refTypes }).reached.values()]
    .map((r) => r.node)
    .filter((n) => n.node_type === 'model')

  // BI fan-out flows through this model's columns, not the model node itself.
  const cards = new Map<string, Reach>()
  const dashboards = new Map<string, Reach>()
  for (const column of columns) {
    const down = walk(index, column.node_id, 'down')
    for (const [reachSet, target] of [
      [reachedOfType(down, 'mb_card'), cards],
      [reachedOfType(down, 'mb_dashboard'), dashboards],
    ] as const) {
      for (const reach of reachSet) {
        const existing = target.get(reach.node.node_id)
        if (!existing || reach.depth < existing.depth) target.set(reach.node.node_id, reach)
      }
    }
  }

  const columnIds = new Set(columns.map((c) => c.node_id))
  return {
    node,
    columns,
    upstreamModels,
    downstreamModels,
    cards: [...cards.values()],
    dashboards: [...dashboards.values()],
    relationships: relationshipsTouching(index, columnIds),
  }
}
