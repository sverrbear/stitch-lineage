// What the home page needs to be an overview rather than a search box (#48):
// the coverage block turned into tiles that link to in-app lists, graph stats
// with a staleness hint, and a few high-degree places worth starting from.
// Pure TS, unit-tested.

import type { Coverage, GraphNode, NodeType, StitchGraph } from '../types'
import type { GraphIndex } from './graph'
import { rollUp } from './rollup'

export type CoverageListKind = 'unbound-models' | 'untraced-columns' | 'unresolved-cards'

export interface CoverageTile {
  key: string
  label: string
  value: number
  total: number | null
  /** The gap this tile measures, as an in-app list — null when there is nothing to list. */
  list: CoverageListKind | null
  listLabel: string | null
  hint: string
}

function ratio(value: number | undefined, total: number | undefined): [number, number | null] {
  return [value ?? 0, total ?? null]
}

/**
 * The four numbers worth putting above the fold. A tile links to its own gap:
 * "109/239 models bound" is only actionable next to the 130 that are not.
 */
export function coverageTiles(coverage: Coverage | undefined): CoverageTile[] {
  const cov = coverage ?? {}
  const [bound, models] = ratio(cov.models_bound, cov.models_total)
  const [traced, columns] = ratio(cov.columns_traced, cov.columns_total)
  const resolved = (cov.mbql_cards_resolved ?? 0) + (cov.native_cards_resolved ?? 0)
  const cards = (cov.mbql_cards_total ?? 0) + (cov.native_cards_total ?? 0)
  const [dashboards, dashboardsTotal] = ratio(cov.dashboards, cov.dashboards_total)

  const unbound = cov.unbound_models?.length ?? 0
  const untraced = cov.untraced_columns?.length ?? 0
  const unresolved = cov.unresolved_cards?.length ?? 0

  return [
    {
      key: 'models',
      label: 'models bound to Metabase',
      value: bound,
      total: models,
      list: unbound > 0 ? 'unbound-models' : null,
      listLabel: unbound > 0 ? `${unbound} unbound` : null,
      hint: 'A model is bound when Metabase has a table at its database, schema and physical name.',
    },
    {
      key: 'columns',
      label: 'columns traced',
      value: traced,
      total: columns,
      list: untraced > 0 ? 'untraced-columns' : null,
      listLabel: untraced > 0 ? `${untraced} untraced` : null,
      hint: 'Traced means sqlglot resolved the column back to its upstreams. Untraced columns break lineage chains.',
    },
    {
      key: 'cards',
      label: 'cards resolved',
      value: resolved,
      total: cards || null,
      list: unresolved > 0 ? 'unresolved-cards' : null,
      listLabel: unresolved > 0 ? `${unresolved} unresolved` : null,
      hint: 'An unresolved card is one whose query stitch could not walk — its dependencies are missing from the graph.',
    },
    {
      key: 'dashboards',
      label: 'dashboards read',
      value: dashboards,
      total: dashboardsTotal,
      list: null,
      listLabel: null,
      hint: 'Dashboards fetched from Metabase, with their cards.',
    },
  ]
}

export interface GraphStats {
  nodeCount: number
  edgeCount: number
  byType: Array<{ type: NodeType; count: number }>
  generatedAt: string | null
  /** Whole days since the build, or null when the graph carries no timestamp. */
  ageDays: number | null
}

const TYPE_ORDER: NodeType[] = ['source', 'model', 'column', 'mb_field', 'mb_card', 'mb_dashboard']

export function graphStats(graph: StitchGraph, now: Date): GraphStats {
  const counts = new Map<NodeType, number>()
  for (const node of graph.nodes) counts.set(node.node_type, (counts.get(node.node_type) ?? 0) + 1)
  const generatedAt = graph.generated_at ?? null
  let ageDays: number | null = null
  if (generatedAt) {
    const built = Date.parse(generatedAt)
    if (Number.isFinite(built)) {
      ageDays = Math.max(0, Math.floor((now.getTime() - built) / 86_400_000))
    }
  }
  return {
    nodeCount: graph.nodes.length,
    edgeCount: graph.edges.length,
    byType: TYPE_ORDER.filter((type) => counts.has(type)).map((type) => ({
      type,
      count: counts.get(type) as number,
    })),
    generatedAt,
    ageDays,
  }
}

export interface StartingPoint {
  node: GraphNode
  /** Cards for a model, cards on a dashboard. */
  count: number
}

export interface StartingPoints {
  /** Models the most Metabase cards depend on — break these and the most breaks. */
  mostConsumedModels: StartingPoint[]
  /** Dashboards with the most cards on them. */
  biggestDashboards: StartingPoint[]
}

export function startingPoints(index: GraphIndex, limit = 6): StartingPoints {
  const rolled = rollUp(index, index.nodes, index.graph.edges)
  const cardsPerModel = new Map<string, number>()
  const cardsPerDashboard = new Map<string, number>()
  for (const edge of rolled.edges) {
    const from = index.nodesById.get(edge.from)
    const to = index.nodesById.get(edge.to)
    if (!from || !to) continue
    if (to.node_type === 'mb_card' && (from.node_type === 'model' || from.node_type === 'source')) {
      cardsPerModel.set(edge.from, (cardsPerModel.get(edge.from) ?? 0) + 1)
    } else if (to.node_type === 'mb_dashboard' && from.node_type === 'mb_card') {
      cardsPerDashboard.set(edge.to, (cardsPerDashboard.get(edge.to) ?? 0) + 1)
    }
  }

  const top = (counts: Map<string, number>): StartingPoint[] =>
    [...counts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, limit)
      .flatMap(([nodeId, count]) => {
        const node = index.nodesById.get(nodeId)
        return node ? [{ node, count }] : []
      })

  return {
    mostConsumedModels: top(cardsPerModel),
    biggestDashboards: top(cardsPerDashboard),
  }
}

// ---------------------------------------------------------------------------
// The lists behind the tiles

export interface CoverageList {
  kind: CoverageListKind
  title: string
  description: string
  /** Entries the graph has a node for; `nodeId` alone when it does not. */
  entries: Array<{ nodeId: string; node: GraphNode | null }>
}

const LIST_COPY: Record<CoverageListKind, { title: string; description: string }> = {
  'unbound-models': {
    title: 'Unbound models',
    description:
      'dbt models with no Metabase table at their database, schema and physical name. Usually a database mapping, a table_prefix, or simply a model nobody has built a question on.',
  },
  'untraced-columns': {
    title: 'Untraced columns',
    description:
      'Columns sqlglot could not resolve back to their upstreams — a star over an unknown relation, or SQL the parser could not walk. Lineage stops here.',
  },
  'unresolved-cards': {
    title: 'Unresolved cards',
    description:
      'Metabase cards whose query stitch could not walk to its source fields — native SQL, or a query shape the resolver does not handle yet.',
  },
}

export function coverageList(index: GraphIndex, kind: CoverageListKind): CoverageList {
  const cov = index.graph.coverage ?? {}
  let ids: string[]
  if (kind === 'unbound-models') ids = [...(cov.unbound_models ?? [])]
  else if (kind === 'untraced-columns') ids = [...(cov.untraced_columns ?? [])]
  else ids = (cov.unresolved_cards ?? []).map((id) => `mb_card::${id}`)

  return {
    kind,
    ...LIST_COPY[kind],
    entries: ids.map((nodeId) => ({ nodeId, node: index.nodesById.get(nodeId) ?? null })),
  }
}
