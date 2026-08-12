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
// `managerOf` lives here for the same reason: who manages a table is one rule the
// whole app has to agree on, and like every other identity question on this page it
// is answered from the node id.
//
// Pure TS, unit-tested.

import type { Confidence, GraphNode, NodeType } from '../types'
import { type GraphIndex, idTail, mbId, modelIdOfColumn } from './graph'

/**
 * Routing prefixes hidden from a model's DISPLAY name (#69). `viz_dim_users`
 * reads as `dim_users` when `serve.strip_model_prefixes` says so — the id, the
 * search key, the staging API and everything written back keep the real dbt
 * name, because the prefix is a routing convention, not part of the entity.
 */
let stripPrefixes: string[] = []

/** Set from `/api/meta` (or the static export's globals) as the app loads. */
export function setStripModelPrefixes(prefixes: string[] | null | undefined): void {
  stripPrefixes = (prefixes ?? []).filter((prefix) => prefix.trim().length > 0)
}

/** The prefixes currently hidden — exported for tests and for the detail panel. */
export function strippedPrefixes(): string[] {
  return [...stripPrefixes]
}

/**
 * `metabase.databases[].table_prefix` — the prefix a dev dbt target puts on physical
 * table names (`sis_fct_matches`) that the BI database does not have. Binding already
 * strips it to match the two; showing it is the reader's own initials as noise (#80).
 */
let tablePrefixes: string[] = []

/** Set from `/api/meta` (or the static export's globals) as the app loads. */
export function setTablePrefixes(prefixes: string[] | null | undefined): void {
  tablePrefixes = (prefixes ?? []).filter((prefix) => prefix.trim().length > 0)
}

/**
 * A physical table name as it should READ. Display only: the graph, the bindings and
 * everything written back keep the real alias, which is still the tooltip.
 */
export function displayTableName(table: string | null | undefined): string | null {
  const name = table?.trim()
  if (!name) return null
  for (const prefix of tablePrefixes) {
    if (name.length > prefix.length && name.toLowerCase().startsWith(prefix.toLowerCase())) {
      return name.slice(prefix.length)
    }
  }
  return name
}

/** The name as dbt spells it, prefix and all. Always available as secondary detail. */
export function fullName(node: GraphNode): string {
  return node.name?.trim() || idTail(node.node_id)
}

/** Whether this node's display name hides a prefix (so a panel can show both). */
export function hasHiddenPrefix(node: GraphNode): boolean {
  return fullName(node) !== displayName(node)
}

/**
 * The display spelling of a dbt model NAME (as opposed to a node). The staging
 * and suggestion APIs speak real dbt names; every surface that shows one runs it
 * through here, and every surface that SENDS one keeps the original (#69).
 */
export function displayModelName(name: string): string {
  for (const prefix of stripPrefixes) {
    if (name.length > prefix.length && name.toLowerCase().startsWith(prefix.toLowerCase())) {
      return name.slice(prefix.length)
    }
  }
  return name
}

function stripModelPrefix(node: GraphNode, name: string): string {
  if (node.node_type !== 'model' && node.node_type !== 'source') return name
  return displayModelName(name)
}

export const NODE_TYPE_NAME: Record<NodeType, string> = {
  source: 'source',
  model: 'model',
  column: 'column',
  mb_field: 'field',
  mb_card: 'card',
  mb_dashboard: 'dashboard',
}

/**
 * Who MANAGES the thing behind a node — the question the badge answers (#187).
 *
 * Not "which system does it live in": a source table lands in Snowflake by something
 * nobody in the dbt repo controls, so a `dbt run` neither creates it nor can change
 * it, and badging it with the same mark as a mart it feeds hid the one distinction a
 * reader is actually asking about. So the warehouse side splits:
 *
 * - `dbt` — the dbt pipeline produces it: models, and the seeds and snapshots that
 *   are pipeline output too (they carry no node type of their own today, so they
 *   arrive here as their dependents' upstreams — see resolve/dbt.py).
 * - `snowflake` — a dbt `source`: landed in the warehouse, not managed by dbt. Also
 *   the fallback for a warehouse node whose owner cannot be read, because claiming
 *   dbt manages something we could not identify is the wrong way to be wrong.
 * - `metabase` — the BI side, unchanged.
 *
 * A COLUMN inherits its parent table's manager, read off the node id (always
 * `{dbt unique_id}::{column}`) rather than the graph index, so this stays pure and
 * usable from a component with no index in hand. `nodeId` is therefore required in
 * practice for columns; without it a column falls back to `snowflake`.
 */
export type Manager = 'dbt' | 'snowflake' | 'metabase'

export function managerOf(nodeType: NodeType, nodeId?: string): Manager {
  if (nodeType.startsWith('mb_')) return 'metabase'
  if (nodeType === 'model') return 'dbt'
  if (nodeType === 'source') return 'snowflake'
  // column: whoever manages the table it belongs to
  const owner = nodeId ? modelIdOfColumn(nodeId) : null
  if (owner?.startsWith('model.')) return 'dbt'
  return 'snowflake'
}

/** The same question for a node in hand — the form every call site actually wants. */
export function managerOfNode(node: Pick<GraphNode, 'node_type' | 'node_id'>): Manager {
  return managerOf(node.node_type, node.node_id)
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
    return stripModelPrefix(node, name)
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
      // collection_path is the breadcrumb the resolver writes ("Growth/Retention");
      // the other two are older spellings, kept so a graph built before this still
      // says something. Three cards named "Match to Conversation Ratio" are told
      // apart by nothing else (#122).
      return stringProperty(node, 'collection_path', 'collection_name', 'collection')
  }
}

/**
 * Archived in Metabase. It stays in the graph — a change still breaks it, and
 * `doctor --dead` reports archived-but-bound cards — but it must never be
 * mistaken for the live card of the same name sitting next to it in search.
 */
export function isArchived(node: GraphNode): boolean {
  return node.properties?.archived === true
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
