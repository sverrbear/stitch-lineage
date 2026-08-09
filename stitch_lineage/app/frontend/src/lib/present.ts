// One naming rule for every surface (issues #44 / #45): what a node is *called*
// and what human context sits next to that name. Panels, chips, canvases and
// search results all go through here, so a node reads the same everywhere.
//
// The rule: dbt entities read as dbt names. A column's context is the dbt MODEL
// it belongs to (derived from the node id, which is always the dbt unique_id) —
// never Node.table, which is the physical alias and on dev artifacts carries the
// USER_PREFIX (`sis_stg_...`). The warehouse spelling is a secondary detail row,
// never a label. Metabase entities read as their Metabase display name with the
// Metabase table/collection as context.
//
// Pure TS, unit-tested.

import type { Confidence, GraphNode, NodeType } from '../types'
import { type GraphIndex, idTail, mbId, modelIdOfColumn } from './graph'

export const NODE_TYPE_NAME: Record<NodeType, string> = {
  source: 'source',
  model: 'model',
  column: 'column',
  mb_field: 'field',
  mb_card: 'card',
  mb_dashboard: 'dashboard',
}

/** dbt package of a unique_id: `model.smitten.fct_matches::user_id` -> `smitten`. */
export function packageOf(nodeId: string): string | null {
  const head = nodeId.split('::')[0]
  const parts = head.split('.')
  if (parts.length < 3) return null
  if (parts[0] !== 'model' && parts[0] !== 'source') return null
  return parts[1] || null
}

/**
 * A node the graph never actually contained — buildIndex conjured it for a
 * dangling edge endpoint, so its `name` is whatever the id happened to carry
 * (a bare Metabase id, or `*` for a star pseudo-column).
 */
export function isPlaceholder(node: GraphNode): boolean {
  return node.properties?.synthetic === true
}

/**
 * The primary label. Never a raw `mb_field::1074`-style id, never the physical
 * (prefix-carrying) table name, never an empty string.
 */
export function displayName(node: GraphNode): string {
  if (node.node_type === 'column' && node.name === '*') return 'all columns (*)'
  const name = node.name?.trim()
  if (name) {
    // A placeholder mb node is named after its id — say what it is instead.
    if (isPlaceholder(node) && node.node_type.startsWith('mb_') && /^\d+$/.test(name)) {
      return `${NODE_TYPE_NAME[node.node_type]} ${name}`
    }
    return name
  }
  const id = mbId(node.node_id)
  if (id !== null) return `${NODE_TYPE_NAME[node.node_type]} ${id}`
  return idTail(node.node_id)
}

/** The dbt model/source a column belongs to, when the graph has it. */
export function ownerOf(index: GraphIndex, node: GraphNode): GraphNode | null {
  if (node.node_type !== 'column') return null
  const ownerId = modelIdOfColumn(node.node_id)
  if (!ownerId) return null
  return index.nodesById.get(ownerId) ?? null
}

/** dbt name of the model/source owning a column, from the id when the node is absent. */
export function ownerName(index: GraphIndex, node: GraphNode): string | null {
  const ownerId = modelIdOfColumn(node.node_id)
  if (!ownerId) return null
  const owner = index.nodesById.get(ownerId)
  return owner ? displayName(owner) : idTail(ownerId)
}

function stringProperty(node: GraphNode, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = node.properties?.[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return null
}

/**
 * The one-line locator shown next to the name: the dbt model for a column, the
 * dbt schema for a model, the source name for a source, the Metabase table for a
 * field, the collection for a card/dashboard. Null when there is nothing to say.
 */
export function nodeContext(index: GraphIndex, node: GraphNode): string | null {
  switch (node.node_type) {
    case 'column':
      return ownerName(index, node)
    case 'model':
      return node.schema ?? null
    case 'source':
      return stringProperty(node, 'source_name') ?? node.schema ?? null
    case 'mb_field':
      return node.table ?? null
    case 'mb_card':
    case 'mb_dashboard':
      return stringProperty(node, 'collection_name', 'collection')
  }
}

/**
 * Physical warehouse locator (`schema.table`) — a secondary detail row only.
 * On dev artifacts the table carries the USER_PREFIX, which is exactly why it
 * must never be a label.
 */
export function warehouseRelation(node: GraphNode): string | null {
  if (!node.table) return null
  return node.schema ? `${node.schema}.${node.table}` : node.table
}

/**
 * The warehouse's spelling of a dbt column when it differs from the dbt one
 * (`DOWNLOADS` for `downloads`). The resolver only sets it on a difference, so
 * null means "same as the name".
 */
export function warehouseColumn(node: GraphNode): string | null {
  const value = node.properties?.warehouse_name
  return typeof value === 'string' && value ? value : null
}

/** Metabase locator for a field: `database · schema.table`, whatever parts exist. */
export function metabaseRelation(node: GraphNode): string | null {
  const relation = warehouseRelation(node)
  if (!relation) return node.database ?? null
  return node.database ? `${node.database} · ${relation}` : relation
}

// ---------------------------------------------------------------------------
// Confidence, in words

/** Short human label for a confidence — `fuzzy` alone means nothing to a reader. */
export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  exact: 'exact',
  validated: 'validated',
  declared: 'declared',
  parsed: 'parsed',
  inferred: 'inferred',
  fuzzy: 'name match',
}

/** Tooltip explaining how the link was established. */
export const CONFIDENCE_HELP: Record<Confidence, string> = {
  exact: 'Exact match — identifiers line up end to end.',
  validated: 'Validated by a dbt relationships test.',
  declared: 'Declared in dbt metadata, not derived from SQL.',
  parsed: 'Parsed out of the compiled SQL by sqlglot.',
  inferred: 'Inferred through a select * expansion — the column was never named.',
  fuzzy: 'Matched on name alone after folding case and underscores.',
}
