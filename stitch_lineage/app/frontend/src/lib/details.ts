// Per-node-type detail computations for the routed detail panels (spec §9).
// Pure TS, unit-testable.

import type { DataTypeSource, GraphEdge, GraphNode } from '../types'
import {
  type GraphIndex,
  type Reach,
  type WalkOptions,
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

/** Bounded lists, with a complete traversal only when a limit hid something. */
function detailWalk(index: GraphIndex, nodeId: string, direction: 'up' | 'down', options: WalkOptions = {}) {
  const shown = walk(index, nodeId, direction, options)
  const full = shown.truncated
    ? walk(index, nodeId, direction, { ...options, maxDepth: Infinity, maxNodes: Infinity })
    : shown
  return { shown, full }
}

function owners(index: GraphIndex, columns: readonly Reach[], sourcesOnly = false): Set<string> {
  const ids = new Set<string>()
  for (const { node } of columns) {
    const id = modelIdOfColumn(node.node_id)
    const owner = id ? index.nodesById.get(id) : undefined
    if (owner && (!sourcesOnly || owner.node_type === 'source')) ids.add(owner.node_id)
  }
  return ids
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
  upstreamTruncated: boolean
  downstreamTruncated: boolean
  counts: {
    upstreamColumns: number
    upstreamSources: number
    downstreamModels: number
    fields: number
    cards: number
    dashboards: number
  }
}

export function columnDetail(index: GraphIndex, nodeId: string): ColumnDetail | null {
  const node = index.nodesById.get(nodeId)
  if (!node) return null
  const modelId = modelIdOfColumn(nodeId)
  const model = modelId ? (index.nodesById.get(modelId) ?? null) : null

  const upstream = detailWalk(index, nodeId, 'up')
  const downstream = detailWalk(index, nodeId, 'down')
  const up = upstream.shown
  const down = downstream.shown

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
    upstreamTruncated: up.truncated,
    downstreamTruncated: down.truncated,
    counts: {
      upstreamColumns: reachedOfType(upstream.full, 'column').length,
      upstreamSources: owners(index, reachedOfType(upstream.full, 'column'), true).size,
      downstreamModels: owners(index, reachedOfType(downstream.full, 'column')).size,
      fields: reachedOfType(downstream.full, 'mb_field').length,
      cards: reachedOfType(downstream.full, 'mb_card').length,
      dashboards: reachedOfType(downstream.full, 'mb_dashboard').length,
    },
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
  /** Displayed dbt dependencies, with bottleneck confidence; bounded by the walk limits. */
  dependsOnColumns: Reach[]
  dependsOnModels: GraphNode[]
  fields: Reach[]
  /** Fields upstream of this node that no dbt column binds to: where the chain stops. */
  unboundFields: Reach[]
  cards: Reach[]
  dashboards: Reach[]
  /** Set only when no dbt column exists in the complete upstream chain. */
  gap: ChainGap | null
  upstreamTruncated: boolean
  downstreamTruncated: boolean
  counts: {
    columns: number
    models: number
    fields: number
    unboundFields: number
    cards: number
    dashboards: number
  }
}

/** Detail for mb_card, mb_dashboard and mb_field nodes (the reverse view). */
export function biDetail(index: GraphIndex, nodeId: string): BiDetail | null {
  const node = index.nodesById.get(nodeId)
  if (!node) return null
  const upstream = detailWalk(index, nodeId, 'up')
  const downstream = detailWalk(index, nodeId, 'down')
  const up = upstream.shown
  const down = downstream.shown

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
    gap: chainGap(node, reachedOfType(upstream.full, 'mb_field'), reachedOfType(upstream.full, 'column')),
    upstreamTruncated: up.truncated,
    downstreamTruncated: down.truncated,
    counts: {
      columns: reachedOfType(upstream.full, 'column').length,
      models: owners(index, reachedOfType(upstream.full, 'column')).size,
      fields: reachedOfType(upstream.full, 'mb_field').length,
      unboundFields: reachedOfType(upstream.full, 'mb_field').filter((reach) => !fieldIsBound(index, reach.node.node_id)).length,
      cards: reachedOfType(node.node_type === 'mb_dashboard' ? upstream.full : downstream.full, 'mb_card').length,
      dashboards: reachedOfType(downstream.full, 'mb_dashboard').length,
    },
  }
}

// ---------------------------------------------------------------------------

export interface ModelDetail {
  node: GraphNode
  columns: GraphNode[]
  /** Reaches, not bare nodes: the hop distance is what orders the layers (#82). */
  upstream: Reach[]
  downstream: Reach[]
  cards: Reach[]
  dashboards: Reach[]
  relationships: RelationshipRef[]
  upstreamTruncated: boolean
  downstreamTruncated: boolean
  usageTruncated: boolean
  counts: { upstream: number; downstream: number }
}

export function modelDetail(index: GraphIndex, nodeId: string): ModelDetail | null {
  const node = index.nodesById.get(nodeId)
  if (!node) return null
  const columns = index.columnsByModel.get(nodeId) ?? []

  const refTypes = new Set(['references'])
  const up = detailWalk(index, nodeId, 'up', { edgeTypes: refTypes })
  const down = detailWalk(index, nodeId, 'down', { edgeTypes: refTypes })
  const isUpstreamModel = (r: Reach) => r.node.node_type === 'model' || r.node.node_type === 'source'
  const isDownstreamModel = (r: Reach) => r.node.node_type === 'model'
  const upstream = [...up.shown.reached.values()].filter(isUpstreamModel)
  const downstream = [...down.shown.reached.values()].filter(isDownstreamModel)

  // BI fan-out flows through this model's columns, not the model node itself.
  const cards = new Map<string, Reach>()
  const dashboards = new Map<string, Reach>()
  let usageTruncated = false
  for (const column of columns) {
    const down = walk(index, column.node_id, 'down')
    usageTruncated ||= down.truncated
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
    upstream,
    downstream,
    cards: [...cards.values()],
    dashboards: [...dashboards.values()],
    relationships: relationshipsTouching(index, columnIds),
    upstreamTruncated: up.shown.truncated,
    downstreamTruncated: down.shown.truncated,
    usageTruncated,
    counts: {
      upstream: [...up.full.reached.values()].filter(isUpstreamModel).length,
      downstream: [...down.full.reached.values()].filter(isDownstreamModel).length,
    },
  }
}

// ---------------------------------------------------------------------------
// Why a column has no data type

export interface DataTypeLabel {
  text: string
  /** The explanation, when "unknown" needs one. */
  hint: string | null
  /** Where the type came from, for the subtext under it. Null when there is no type. */
  source: { label: string; hint: string } | null
}

/**
 * The provenance subtext for a resolved type (#149). Types no longer come from one
 * place, so the line has to say which -- a NUMBER(38,0) the warehouse reported and a
 * DOUBLE sqlglot guessed at are not the same claim, and only one of them is safe to
 * act on. Same principle as confidence on an edge: show the weaker evidence, do not
 * launder it into the strong case by rendering both identically.
 */
const TYPE_SOURCE: Record<DataTypeSource, { label: string; hint: string }> = {
  catalog: {
    label: 'from the dbt catalog',
    hint: 'Read from this build’s dbt artifacts — the catalog the target actually built, or a data_type declared in schema.yml.',
  },
  metabase: {
    label: 'from Metabase sync',
    hint: 'This column has no entry in the dbt catalog, so the type comes from the Metabase field it binds to — the warehouse type Metabase recorded when it synced the database. Metabase syncs the whole schema, so it knows types for relations your target never built.',
  },
  inferred: {
    label: 'inferred from expression',
    hint: 'Nothing observed this column’s type: sqlglot worked it out from the compiled SQL. A parse result, not a warehouse fact, and spelled the way sqlglot spells types rather than the way the warehouse does.',
  },
}

const UNKNOWN_REASON: Record<string, { suffix: string; hint: string }> = {
  relation_not_in_catalog: {
    suffix: 'not in catalog',
    hint: 'This build’s dbt catalog has no entry for the model at all — usually a dev target that never built it. Run `dbt docs generate` against a target that has it.',
  },
  column_not_in_catalog: {
    suffix: 'not in catalog yet',
    hint: 'The model is in the catalog but this column is not: it exists in the SQL and has not been deployed yet. It is undeployed, not lost — which is what lets a pre-deploy graph diff see it.',
  },
}

/**
 * The `data type` fact. A bare "unknown" reads as a broken tool, so when the
 * graph knows WHY the type is missing it says so on the line (#122). A type that
 * resolved is returned unchanged, with the source that answered for it (#149) --
 * an older graph.json carries no source, and simply shows no subtext.
 */
export function dataTypeLabel(node: GraphNode): DataTypeLabel {
  if (node.data_type) {
    const source = node.data_type_source
    return { text: node.data_type, hint: null, source: (source && TYPE_SOURCE[source]) ?? null }
  }
  const reason = node.properties?.unknown_type_reason
  const known = typeof reason === 'string' ? UNKNOWN_REASON[reason] : undefined
  if (!known) return { text: 'unknown', hint: null, source: null }
  return { text: `unknown — ${known.suffix}`, hint: known.hint, source: null }
}
