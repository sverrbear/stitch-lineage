// Read-only ERD data prep: scope listing (schema / dbt tag) and per-scope
// extraction of models + relates_to edges. Pure TS, unit-testable.

import { lineageHref, nodeHref } from '../router'
import type { GraphEdge, GraphNode } from '../types'
import { type GraphIndex, modelIdOfColumn } from './graph'
import { displayName, isPlaceholder, packageOf } from './present'

export interface ErdScope {
  kind: 'schema' | 'tag'
  value: string
  modelCount: number
  /** relates_to edges whose FK side lives in this scope. */
  relationshipCount: number
  /**
   * Plumbing rather than analytics — a package's own schema (elementary,
   * dbt_artifacts) or a warehouse-internal one. Still selectable, but never
   * offered first and never auto-opened.
   */
  internal: boolean
}

export interface ErdRelationship {
  edge: GraphEdge
  fromModelId: string
  toModelId: string
  fromColumn: string
  toColumn: string
  validated: boolean
}

export interface ErdColumn {
  /** Absent from the catalog for a phantom column, where only the id is known. */
  node: GraphNode | null
  /**
   * The dbt column key: the node id's tail, lowercased by the resolver. Handles
   * and relationship endpoints are wired on this, NOT on the display name —
   * a column named `COUNTRY_CODE` still keys on `country_code`.
   */
  key: string
  nodeId: string
  name: string
  dataType: string | null
  /** Participates in a relationship in this scope — always visible. */
  isKey: boolean
  /** Declared by a relationship but missing from the model's column list. */
  phantom: boolean
}

export interface ErdModel {
  node: GraphNode
  /** Key columns first, then the rest in catalog order. */
  columns: ErdColumn[]
  /** Model pulled in only because a scoped relationship points at it. */
  external: boolean
}

export interface ErdData {
  scope: ErdScope
  models: ErdModel[]
  relationships: ErdRelationship[]
}

function isErdModel(node: GraphNode): boolean {
  return node.node_type === 'model' || node.node_type === 'source'
}

function tagsOf(node: GraphNode): string[] {
  const tags = node.properties?.tags
  return Array.isArray(tags) ? tags.map(String) : []
}

function modelInScope(node: GraphNode, scope: ErdScope): boolean {
  if (scope.kind === 'schema') return (node.schema ?? '') === scope.value
  return tagsOf(node).includes(scope.value)
}

function relationships(index: GraphIndex): ErdRelationship[] {
  const rels: ErdRelationship[] = []
  for (const edge of index.graph.edges) {
    if (edge.edge_type !== 'relates_to') continue
    const fromModelId = modelIdOfColumn(edge.from)
    const toModelId = modelIdOfColumn(edge.to)
    if (!fromModelId || !toModelId) continue
    rels.push({
      edge,
      fromModelId,
      toModelId,
      fromColumn: edge.from.slice(fromModelId.length + 2),
      toColumn: edge.to.slice(toModelId.length + 2),
      validated: edge.confidence === 'validated',
    })
  }
  return rels
}

/**
 * Schemas that exist to hold a tool's own bookkeeping — dbt package models
 * (elementary, dbt_artifacts) and warehouse-internal schemas. Nobody browsing
 * an ERD means these, so they sort last and never win `defaultScope`.
 */
const INTERNAL_SCHEMAS =
  /^(elementary|elementary_.*|dbt_artifacts|artifacts|audit|dbt_test__audit|information_schema|account_usage|snowflake)$/i

/** The project's own dbt package: the one owning the most model nodes. */
function rootPackage(index: GraphIndex): string | null {
  const counts = new Map<string, number>()
  for (const node of index.nodes) {
    if (node.node_type !== 'model') continue
    const pkg = packageOf(node.node_id)
    if (pkg) counts.set(pkg, (counts.get(pkg) ?? 0) + 1)
  }
  let best: string | null = null
  let bestCount = 0
  for (const [pkg, count] of counts) {
    if (count > bestCount || (count === bestCount && best !== null && pkg < best)) {
      best = pkg
      bestCount = count
    }
  }
  return best
}

/**
 * Available ERD scopes: every schema and every dbt tag that has models.
 * Schemas are ordered analytics-first (see `internal`), then by relationship
 * count desc, model count desc, name — so `defaultScope` is the most connected
 * schema the project actually owns and the initial render is never a hairball.
 */
export function listScopes(index: GraphIndex): ErdScope[] {
  const rels = relationships(index)
  const relSchema = new Map<string, number>()
  const relTag = new Map<string, number>()
  for (const rel of rels) {
    const model = index.nodesById.get(rel.fromModelId)
    if (!model) continue
    const schema = model.schema ?? ''
    if (schema) relSchema.set(schema, (relSchema.get(schema) ?? 0) + 1)
    for (const tag of tagsOf(model)) relTag.set(tag, (relTag.get(tag) ?? 0) + 1)
  }

  const root = rootPackage(index)
  const schemaCounts = new Map<string, number>()
  const schemaOwnModels = new Map<string, number>()
  const tagCounts = new Map<string, number>()
  for (const node of index.nodes) {
    if (!isErdModel(node)) continue
    const schema = node.schema ?? ''
    if (schema) {
      schemaCounts.set(schema, (schemaCounts.get(schema) ?? 0) + 1)
      if (root === null || packageOf(node.node_id) === root) {
        schemaOwnModels.set(schema, (schemaOwnModels.get(schema) ?? 0) + 1)
      }
    }
    for (const tag of tagsOf(node)) tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1)
  }

  const schemas: ErdScope[] = [...schemaCounts.entries()].map(([value, modelCount]) => ({
    kind: 'schema',
    value,
    modelCount,
    relationshipCount: relSchema.get(value) ?? 0,
    // a schema holding nothing the project itself declares is somebody else's plumbing
    internal: INTERNAL_SCHEMAS.test(value) || (schemaOwnModels.get(value) ?? 0) === 0,
  }))
  schemas.sort(
    (a, b) =>
      Number(a.internal) - Number(b.internal) ||
      b.relationshipCount - a.relationshipCount ||
      b.modelCount - a.modelCount ||
      a.value.localeCompare(b.value),
  )
  const tags: ErdScope[] = [...tagCounts.entries()].map(([value, modelCount]) => ({
    kind: 'tag',
    value,
    modelCount,
    relationshipCount: relTag.get(value) ?? 0,
    internal: false,
  }))
  tags.sort((a, b) => a.value.localeCompare(b.value))
  return [...schemas, ...tags]
}

export function defaultScope(scopes: ErdScope[]): ErdScope | null {
  return (
    scopes.find((s) => s.kind === 'schema' && !s.internal) ??
    scopes.find((s) => s.kind === 'schema') ??
    scopes[0] ??
    null
  )
}

/** `schema:marts` / `tag:core` -> the matching scope, null when it is not in the graph. */
export function findScope(scopes: ErdScope[], key: string): ErdScope | null {
  const separator = key.indexOf(':')
  if (separator < 0) return null
  const kind = key.slice(0, separator)
  const value = key.slice(separator + 1)
  if (kind !== 'schema' && kind !== 'tag') return null
  return scopes.find((s) => s.kind === kind && s.value === value) ?? null
}

export interface InitialScope {
  scope: ErdScope | null
  /** Configured scope key the graph does not have — surfaced next to the picker. */
  unknownConfigured: string | null
}

/**
 * Which scope the ERD opens on: the one pinned in `serve.erd_default_scope` when
 * the graph has it, otherwise the auto-picked (most connected) one.
 */
export function initialScope(scopes: ErdScope[], configured?: string | null): InitialScope {
  const key = configured?.trim()
  if (!key) return { scope: defaultScope(scopes), unknownConfigured: null }
  const pinned = findScope(scopes, key)
  if (pinned) return { scope: pinned, unknownConfigured: null }
  return { scope: defaultScope(scopes), unknownConfigured: key }
}

/**
 * ERD for one scope: its models, plus any model a scoped relationship points
 * at (marked `external` — FK targets usually live in another schema).
 */
export function erdForScope(index: GraphIndex, scope: ErdScope): ErdData {
  const inScope = new Set<string>()
  for (const node of index.nodes) {
    if (isErdModel(node) && modelInScope(node, scope)) inScope.add(node.node_id)
  }

  const rels = relationships(index).filter(
    (rel) => inScope.has(rel.fromModelId) || inScope.has(rel.toModelId),
  )

  const externalIds = new Set<string>()
  for (const rel of rels) {
    for (const id of [rel.fromModelId, rel.toModelId]) {
      if (!inScope.has(id) && index.nodesById.has(id)) externalIds.add(id)
    }
  }

  const keyColumnsByModel = new Map<string, Set<string>>()
  for (const rel of rels) {
    for (const [modelId, column] of [
      [rel.fromModelId, rel.fromColumn],
      [rel.toModelId, rel.toColumn],
    ] as const) {
      const set = keyColumnsByModel.get(modelId)
      if (set) set.add(column)
      else keyColumnsByModel.set(modelId, new Set([column]))
    }
  }

  const models: ErdModel[] = []
  for (const id of [...inScope, ...externalIds]) {
    const node = index.nodesById.get(id)
    if (!node) continue
    models.push({
      node,
      columns: erdColumns(index, id, keyColumnsByModel.get(id) ?? new Set()),
      external: externalIds.has(id),
    })
  }
  models.sort((a, b) => {
    if (a.external !== b.external) return a.external ? 1 : -1
    const aRel = a.columns.some((c) => c.isKey) ? 0 : 1
    const bRel = b.columns.some((c) => c.isKey) ? 0 : 1
    return aRel - bRel || a.node.name.localeCompare(b.node.name)
  })

  return { scope, models, relationships: rels }
}

/**
 * A model's ERD column rows: relationship columns first (they carry the edges),
 * then the rest in catalog order. buildIndex synthesizes a node for every edge
 * endpoint, so a relationship column the catalog never had still arrives here —
 * flagged `phantom`, because the model does not really declare it.
 */
function erdColumns(index: GraphIndex, modelId: string, keys: Set<string>): ErdColumn[] {
  const rows: ErdColumn[] = (index.columnsByModel.get(modelId) ?? []).map((node) => {
    const key = columnKey(modelId, node.node_id)
    return {
      node,
      key,
      nodeId: node.node_id,
      name: displayName(node),
      dataType: node.data_type ?? null,
      isKey: keys.has(key),
      phantom: isPlaceholder(node),
    }
  })
  const known = new Set(rows.map((row) => row.key))
  for (const key of keys) {
    if (known.has(key)) continue
    rows.push({
      node: null,
      key,
      nodeId: erdColumnNodeId(modelId, key),
      name: key,
      dataType: null,
      isKey: true,
      phantom: true,
    })
  }
  return [...rows.filter((row) => row.isKey), ...rows.filter((row) => !row.isKey)]
}

/** The id tail of a column node, i.e. the dbt column key. */
function columnKey(modelId: string, columnNodeId: string): string {
  return columnNodeId.startsWith(`${modelId}::`)
    ? columnNodeId.slice(modelId.length + 2)
    : columnNodeId
}

/**
 * The column rows to draw: key columns are never hidden, the rest fill the row
 * budget until the reader expands the table.
 */
export function visibleColumns(model: ErdModel, expanded: boolean, limit: number): ErdColumn[] {
  if (expanded) return model.columns
  const keys = model.columns.filter((column) => column.isKey)
  const rest = model.columns.filter((column) => !column.isKey)
  return [...keys, ...rest.slice(0, Math.max(0, limit - keys.length))]
}

// ---------------------------------------------------------------------------
// Canvas interaction (pure, unit-tested — the component only wires events to it)

/** Column node id for a column drawn under `modelId` (may be catalog-missing). */
export function erdColumnNodeId(modelId: string, columnName: string): string {
  return `${modelId}::${columnName}`
}

/** Where a click on an ERD table header / column row goes. ⌘/ctrl skips to lineage. */
export function erdClickHref(nodeId: string, modifiers: { metaKey?: boolean; ctrlKey?: boolean } = {}): string {
  return modifiers.metaKey || modifiers.ctrlKey ? lineageHref(nodeId) : nodeHref(nodeId)
}
