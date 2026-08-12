// Small hand-written graph exercising every node type, every flow edge type,
// relates_to, non-exact confidences, and a dangling `::*` endpoint (as seen in
// real graphs). Used by the vitest suites.

import type { GraphEdge, GraphNode, StitchGraph } from '../types'

const M = 'model.demo'
const S = 'source.demo'

function node(partial: Partial<GraphNode> & Pick<GraphNode, 'node_id' | 'node_type' | 'name'>): GraphNode {
  return { properties: {}, ...partial }
}

function edge(partial: Partial<GraphEdge> & Pick<GraphEdge, 'from' | 'to' | 'edge_type' | 'confidence'>): GraphEdge {
  return { evidence: {}, ...partial }
}

export function fixtureGraph(): StitchGraph {
  const nodes: GraphNode[] = [
    node({
      node_id: `${S}.app.events`,
      node_type: 'source',
      name: 'events',
      schema: 'raw',
      table: 'events',
    }),
    node({ node_id: `${S}.app.events::amount`, node_type: 'column', name: 'amount', column: 'amount', data_type: 'number', schema: 'raw', table: 'events' }),

    node({
      node_id: `${M}.stg_payments`,
      node_type: 'model',
      name: 'stg_payments',
      schema: 'staging',
      table: 'stg_payments',
      properties: { tags: ['core'], materialization: 'view' },
    }),
    node({ node_id: `${M}.stg_payments::amount`, node_type: 'column', name: 'amount', column: 'amount', data_type: 'number', schema: 'staging', table: 'stg_payments' }),

    // Physical table carries a dev USER_PREFIX, as dev-target artifacts do: no
    // surface may ever label this model or its columns `sis_fct_revenue`.
    node({
      node_id: `${M}.fct_revenue`,
      node_type: 'model',
      name: 'fct_revenue',
      schema: 'marts',
      table: 'sis_fct_revenue',
      description: 'Daily net revenue facts',
      properties: { tags: ['core', 'finance'], materialization: 'incremental' },
    }),
    node({ node_id: `${M}.fct_revenue::net_revenue`, node_type: 'column', name: 'net_revenue', column: 'net_revenue', data_type: 'number', schema: 'marts', table: 'sis_fct_revenue', description: 'Net of refunds', properties: { warehouse_name: 'NET_REVENUE' } }),
    node({ node_id: `${M}.fct_revenue::user_id`, node_type: 'column', name: 'user_id', column: 'user_id', data_type: 'varchar', schema: 'marts', table: 'sis_fct_revenue' }),

    node({
      node_id: `${M}.dim_users`,
      node_type: 'model',
      name: 'dim_users',
      schema: 'marts',
      table: 'dim_users',
      properties: { tags: ['core'] },
    }),
    node({ node_id: `${M}.dim_users::user_id`, node_type: 'column', name: 'user_id', column: 'user_id', data_type: 'varchar', schema: 'marts', table: 'dim_users' }),

    // A model whose feeds edge comes from a star pseudo-column (dangling endpoint).
    node({
      node_id: `${M}.mart_board`,
      node_type: 'model',
      name: 'mart_board',
      schema: 'marts',
      table: 'mart_board',
      properties: { tags: ['reporting'] },
    }),
    node({ node_id: `${M}.mart_board::net_revenue`, node_type: 'column', name: 'net_revenue', column: 'net_revenue', data_type: 'number', schema: 'marts', table: 'mart_board' }),

    // A Snowflake semantic view: a dbt model, a real lineage consumer of
    // fct_revenue, and never an ERD table (#191). It sits in `marts` and carries
    // `core`, so any ERD count that includes it is a count that is wrong.
    node({
      node_id: `${M}.sv_revenue`,
      node_type: 'model',
      name: 'sv_revenue',
      schema: 'marts',
      table: 'sv_revenue',
      properties: { tags: ['core', 'semantic'], materialization: 'semantic_view' },
    }),

    // Models shipped by an installed dbt package, in a schema nobody browses:
    // the ERD scope picker must not offer these ahead of the analytics schemas.
    node({
      node_id: 'model.elementary.alerts_anomaly_detection',
      node_type: 'model',
      name: 'alerts_anomaly_detection',
      schema: 'elementary',
      table: 'sis_alerts_anomaly_detection',
      properties: { tags: [], materialization: 'view' },
    }),
    node({ node_id: 'model.elementary.alerts_anomaly_detection::alert_id', node_type: 'column', name: 'ALERT_ID', column: 'ALERT_ID', data_type: 'varchar', schema: 'elementary', table: 'sis_alerts_anomaly_detection' }),
    node({
      node_id: 'source.demo.artifacts.dbt_runs',
      node_type: 'source',
      name: 'dbt_runs',
      schema: 'artifacts',
      table: 'dbt_runs',
      properties: { source_name: 'artifacts' },
    }),

    node({
      node_id: 'mb_field::101',
      node_type: 'mb_field',
      name: 'Net Revenue',
      column: 'NET_REVENUE',
      table: 'FCT_REVENUE',
      schema: 'MARTS',
      database: 'Analytics',
      data_type: 'type/Float',
      description: 'Net of refunds, as Metabase sees it',
      properties: { semantic_type: 'type/Currency', visibility: 'normal' },
    }),
    node({
      node_id: 'mb_card::412',
      node_type: 'mb_card',
      name: 'Revenue by country',
      properties: { creator: 'sverrir', collection_name: 'Board', archived: false },
    }),
    node({
      node_id: 'mb_card::418',
      node_type: 'mb_card',
      name: 'Weekly revenue trend',
      properties: { creator: 'sverrir', collection_name: 'Board', archived: true },
    }),
    node({
      node_id: 'mb_dash::7',
      node_type: 'mb_dashboard',
      name: 'Board dashboard',
      properties: { collection_name: 'Board' },
    }),
  ]

  const edges: GraphEdge[] = [
    edge({ from: `${S}.app.events`, to: `${M}.stg_payments`, edge_type: 'references', confidence: 'exact' }),
    edge({ from: `${M}.stg_payments`, to: `${M}.fct_revenue`, edge_type: 'references', confidence: 'exact' }),
    edge({ from: `${M}.fct_revenue`, to: `${M}.mart_board`, edge_type: 'references', confidence: 'exact' }),
    // The semantic view's dependency is real lineage, drawn like any other.
    edge({ from: `${M}.fct_revenue`, to: `${M}.sv_revenue`, edge_type: 'references', confidence: 'exact' }),

    edge({ from: `${S}.app.events::amount`, to: `${M}.stg_payments::amount`, edge_type: 'feeds', confidence: 'exact' }),
    edge({
      from: `${M}.stg_payments::amount`,
      to: `${M}.fct_revenue::net_revenue`,
      edge_type: 'feeds',
      confidence: 'parsed',
      evidence: { source: 'sqlglot.lineage', sql: 'amount - refunds AS net_revenue' },
    }),
    // Dangling star endpoint: no node entry for stg_payments::* on purpose.
    edge({ from: `${M}.stg_payments::*`, to: `${M}.fct_revenue::user_id`, edge_type: 'feeds', confidence: 'inferred' }),
    edge({ from: `${M}.fct_revenue::net_revenue`, to: `${M}.mart_board::net_revenue`, edge_type: 'feeds', confidence: 'exact' }),

    edge({ from: `${M}.fct_revenue::net_revenue`, to: 'mb_field::101', edge_type: 'binds_to', confidence: 'exact' }),
    // Field the Metabase pull never returned a definition for: the only thing
    // known about it is its id, which must still not render as `mb_field::902`.
    edge({ from: `${M}.fct_revenue::user_id`, to: 'mb_field::902', edge_type: 'binds_to', confidence: 'fuzzy' }),
    edge({ from: 'mb_field::101', to: 'mb_card::412', edge_type: 'consumed_by', confidence: 'exact' }),
    edge({ from: 'mb_field::101', to: 'mb_card::418', edge_type: 'consumed_by', confidence: 'parsed' }),
    edge({ from: 'mb_card::412', to: 'mb_dash::7', edge_type: 'appears_on', confidence: 'exact' }),
    edge({ from: 'mb_card::418', to: 'mb_dash::7', edge_type: 'appears_on', confidence: 'exact' }),

    // Declaration, not flow: must never appear in lineage.
    edge({
      from: `${M}.fct_revenue::user_id`,
      to: `${M}.dim_users::user_id`,
      edge_type: 'relates_to',
      confidence: 'validated',
      evidence: { source: 'relationships_test' },
    }),
  ]

  return { schema_version: 1, generated_at: '2026-08-07T00:00:00Z', nodes, edges }
}
