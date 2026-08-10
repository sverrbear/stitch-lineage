// TypeScript mirror of stitch_lineage/graph/schema.py (graph.json schema_version 1).

export type NodeType = 'source' | 'model' | 'column' | 'mb_field' | 'mb_card' | 'mb_dashboard'

export type EdgeType =
  | 'references'
  | 'feeds'
  | 'binds_to'
  | 'consumed_by'
  | 'appears_on'
  | 'relates_to'

export type Confidence = 'exact' | 'parsed' | 'inferred' | 'fuzzy' | 'declared' | 'validated'

export interface GraphNode {
  node_id: string
  node_type: NodeType
  name: string
  database?: string | null
  schema?: string | null
  table?: string | null
  column?: string | null
  data_type?: string | null
  description?: string | null
  owner?: string | null
  properties: Record<string, unknown>
}

export interface GraphEdge {
  from: string
  to: string
  edge_type: EdgeType
  confidence: Confidence
  evidence: Record<string, unknown>
}

export interface Coverage {
  models_bound?: number
  models_total?: number
  mbql_cards_resolved?: number
  mbql_cards_total?: number
  native_cards_resolved?: number
  native_cards_total?: number
  dashboards?: number
  dashboards_total?: number
  columns_traced?: number
  columns_total?: number
  columns_inferred?: number
  unbound_models?: string[]
  unresolved_cards?: number[]
  untraced_columns?: string[]
  dangling_relationships?: string[]
}

export interface StitchGraph {
  schema_version: number
  generated_at?: string | null
  dbt_invocation_id?: string | null
  metabase_version?: string | null
  coverage?: Coverage
  nodes: GraphNode[]
  edges: GraphEdge[]
}

/** GET /api/meta body; also inlined as window.__STITCH_META__ in static export. */
export interface StitchMeta {
  metabase_url: string | null
  generated_at: string | null
  schema_version: number
  /** `schema:<name>` / `tag:<name>` the ERD opens on (serve.erd_default_scope). */
  erd_default_scope?: string | null
  /** Routing prefixes hidden from model display names (serve.strip_model_prefixes). */
  strip_model_prefixes?: string[]
  /**
   * Per-database `metabase.databases[].table_prefix` values. Binding strips them to
   * match dev-target artifacts against a prod-pointed Metabase; the app hides them
   * from the physical names it displays for the same reason (#80).
   */
  table_prefixes?: string[]
  /**
   * Whether this build has the staging API: true under `stitch serve`, false in
   * a static export, absent on a serve that predates the flag (probe instead).
   */
  staging_enabled?: boolean
}

declare global {
  interface Window {
    __STITCH_GRAPH__?: StitchGraph
    __STITCH_META__?: StitchMeta
  }
}
