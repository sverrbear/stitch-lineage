// What the build learned about each column while tracing it (#147, #148).
//
// Mirrors TraceReason / DefinedAs in stitch_lineage/resolve/dbt.py the same way
// types.ts mirrors graph/schema.py. Every model column says either how it is
// defined or why stitch could not tell — those are the same slot in the panel, so
// they are one function here.
//
// Pure TS, unit-tested.

import type { GraphNode } from '../types'

export type TraceStatus = 'traced' | 'untraced'

/** The §7.3 failure taxonomy, verbatim from the resolver. */
export type TraceReasonCode =
  | 'no_compiled_code'
  | 'unparseable_sql'
  | 'column_not_in_sql'
  | 'star_not_expandable'
  | 'upstream_not_in_schema_map'
  | 'upstream_not_in_project'
  | 'no_upstream_columns'
  | 'lineage_failed'

export interface TraceReason {
  code: string
  /** One line, in the words of the thing to go and fix. */
  label: string
  /** What it means and what unblocks it — the tooltip on every row. */
  hint: string
}

const REASONS: Record<TraceReasonCode, { label: string; hint: string }> = {
  no_compiled_code: {
    label: 'Model has no compiled SQL',
    hint: 'The manifest carries no compiled_code for this model, so there was no SQL to trace. Usually a model disabled in this build, or a manifest written without compiling.',
  },
  unparseable_sql: {
    label: 'SQL could not be parsed',
    hint: 'sqlglot could not parse the model’s compiled SQL — an exotic PIVOT or dialect corner. The model keeps its model-level references; only its column lineage is missing.',
  },
  column_not_in_sql: {
    label: 'Documented but not in the SQL',
    hint: 'The column is declared in schema.yml but the compiled SQL never projects it. Either the SQL dropped it, or the documentation is ahead of the model.',
  },
  star_not_expandable: {
    label: 'Star not expandable',
    hint: 'The column arrives through SELECT * over a relation stitch has no column list for, so there was nothing to expand the star against. Documenting that upstream usually rescues the whole subtree.',
  },
  upstream_not_in_schema_map: {
    label: 'Upstream absent from the schema map',
    hint: 'Every relation here is a dbt model or source, but one of them is in neither the catalog nor schema.yml — a dev catalog only holds what that developer built. Document it, or build it and run dbt docs generate.',
  },
  upstream_not_in_project: {
    label: 'Upstream not a dbt model or source',
    hint: 'The SQL reads a relation dbt does not own, so lineage has nowhere upstream to go. Declaring it as a source in schema.yml connects it.',
  },
  no_upstream_columns: {
    label: 'Literal — nothing upstream',
    hint: 'The column is a constant or a generated value (a literal, current_timestamp()), so it genuinely has no upstream column. A fact about the column, not a gap in the build.',
  },
  lineage_failed: {
    label: 'Parser could not walk this column',
    hint: 'The query names this output but sqlglot could not resolve it back to any input — a parser limit rather than a missing document.',
  },
}

/** Model columns carry a status; a source column is a lineage root and carries none. */
export function traceStatus(node: GraphNode): TraceStatus | null {
  const status = node.properties?.trace_status
  return status === 'traced' || status === 'untraced' ? status : null
}

/**
 * Why this column is untraced. Null when it is traced, when the node is not a
 * column, or when the graph predates #147 — an absent reason is never a claim.
 * An unrecognised code still reads as itself rather than vanishing: a graph built
 * by a newer stitch must not silently drop rows out of this list.
 */
export function traceReason(node: GraphNode): TraceReason | null {
  const raw = node.properties?.trace_reason
  if (typeof raw !== 'string' || !raw) return null
  const known = REASONS[raw as TraceReasonCode]
  return {
    code: raw,
    label: known?.label ?? raw.replace(/_/g, ' '),
    hint: known?.hint ?? 'This build recorded a reason this version of the app does not know about.',
  }
}

// ---------------------------------------------------------------------------

export type DefinedAsKind = 'expression' | 'passthrough' | 'star'

/** Where a passthrough chain was actually defined (#162). */
export type DefinedOriginKind = 'expression' | 'source'

export interface DefinedOrigin {
  /** `expression` — computed in `model`; `source` — a warehouse column, no SQL. */
  kind: DefinedOriginKind
  model: string
  /** Its name at the origin, which a rename mid-chain makes different from here. */
  column: string
  sql: string | null
  /** Passthrough hops walked to get there; 1 is the immediate upstream. */
  hops: number
}

export interface DefinedAs {
  kind: DefinedAsKind
  /** The defining SQL, already truncated for display by the build. */
  sql: string
  /** The upstream column (passthrough) or relation (star), when unambiguous. */
  upstream: string | null
  /** Set on a passthrough or star whose chain reaches a definition. */
  origin: DefinedOrigin | null
}

const KINDS: DefinedAsKind[] = ['expression', 'passthrough', 'star']
const ORIGIN_KINDS: DefinedOriginKind[] = ['expression', 'source']

/**
 * `defined_as.origin`, validated. Null when the build could not walk the chain — and
 * on every graph built before #162, which is why an absent origin falls back to the
 * local projection rather than blanking the block.
 */
function definedOrigin(raw: unknown): DefinedOrigin | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  const { kind, model, column, hops } = value
  if (typeof kind !== 'string' || !ORIGIN_KINDS.includes(kind as DefinedOriginKind)) return null
  if (typeof model !== 'string' || !model) return null
  if (typeof column !== 'string' || !column) return null
  if (typeof hops !== 'number' || !Number.isInteger(hops) || hops < 1) return null
  return {
    kind: kind as DefinedOriginKind,
    model,
    column,
    sql: typeof value.sql === 'string' && value.sql ? value.sql : null,
    hops,
  }
}

/** `properties.defined_as`, validated. Null when the build could not say. */
export function definedAs(node: GraphNode): DefinedAs | null {
  const raw = node.properties?.defined_as
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Record<string, unknown>
  const kind = value.kind
  if (typeof kind !== 'string' || !KINDS.includes(kind as DefinedAsKind)) return null
  if (typeof value.sql !== 'string' || !value.sql) return null
  return {
    kind: kind as DefinedAsKind,
    sql: value.sql,
    upstream: typeof value.upstream === 'string' && value.upstream ? value.upstream : null,
    origin: definedOrigin(value.origin),
  }
}

/**
 * What the 'Defined as' slot renders (#148): how the column is built, why lineage
 * could not follow it, or both.
 *
 * Both is the important case, and the reason is never dropped when there is one:
 * a `select *` that would not expand still says HOW the column arrives, but a
 * panel showing `passthrough / feed_id` above an empty upstream list and no
 * explanation is exactly the "looks broken" failure these two issues exist to
 * remove. The reason leads, because it is the finding.
 *
 * Null means the block is not shown at all: a source column is warehouse-native
 * with no definition to give, and a traced column from a graph built before #148
 * has upstreams without a kept expression. Neither is a gap worth a row.
 */
export interface ColumnDefinition {
  /** The projection, when this build kept one. */
  definition: DefinedAs | null
  /** Why lineage stopped here — set only on an untraced column. */
  reason: TraceReason | null
}

export function columnDefinition(node: GraphNode): ColumnDefinition | null {
  if (node.node_type !== 'column') return null
  const definition = definedAs(node)
  const reason = traceReason(node)
  return definition || reason ? { definition, reason } : null
}

/** "passthrough from stg_orders.order_id" — the prose above the monospace SQL. */
export function definedAsSummary(definition: DefinedAs): string {
  if (definition.kind === 'star') {
    return definition.upstream ? `via star from ${definition.upstream}` : 'via star'
  }
  if (definition.kind === 'passthrough') {
    return definition.upstream ? `passthrough from ${definition.upstream}` : 'passthrough'
  }
  return 'expression'
}

/**
 * "defined in stg_orders · 3 hops upstream" (#162).
 *
 * The line that answers the question the summary above it cannot: a passthrough names
 * its parent, and its parent is usually another passthrough. The hop count is what
 * says whether the definition is next door or across the project, and it is dropped
 * at one hop where "1 hop upstream" is just noise next to a named model.
 */
export function definedOriginSummary(origin: DefinedOrigin): string {
  const where =
    origin.kind === 'source'
      ? `originates in ${origin.model}.${origin.column}`
      : `defined in ${origin.model}`
  return origin.hops > 1 ? `${where} · ${origin.hops} hops upstream` : where
}

/**
 * The SQL the 'Defined as' block should show: the origin's expression when the chain
 * found one, else the column's own projection.
 *
 * This is the fix in one line (#162) — for a passthrough, the local projection is a
 * restatement of the edge the upstream list already shows, and the expression that
 * actually defines the value lives at the far end of the chain.
 */
export function definitionSql(definition: DefinedAs): string {
  return definition.origin?.sql || definition.sql
}
