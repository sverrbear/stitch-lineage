// What the home page needs to be a way in rather than a dashboard (#48, #108):
// the coverage block as rows that link to their own gaps, graph stats with a
// staleness hint, the build stamp every page carries, and real identifiers out
// of this graph to start from. Pure TS, unit-tested.

import type { Coverage, GraphNode, NodeType, StitchGraph } from '../types'
import type { GraphIndex } from './graph'
import { displayName, ownerName } from './present'
import { rollUp } from './rollup'

export type CoverageListKind = 'unbound-models' | 'untraced-columns' | 'unresolved-cards'

export interface CoverageRow {
  key: string
  label: string
  value: number
  total: number | null
  /** What this build did NOT get — the number the row exists to admit (principle 03). */
  gap: number
  /** The gap as an in-app list — null when there is nothing to list. */
  list: CoverageListKind | null
  gapLabel: string | null
  hint: string
  /**
   * A qualifier on the number itself, not on what is missing from it: the share
   * of the traced columns that is only inferred, the models config took out of
   * the denominator. A ratio with an unstated caveat oversells (principle 03).
   */
  note: string | null
  noteHint: string | null
}

function ratio(value: number | undefined, total: number | undefined): [number, number | null] {
  return [value ?? 0, total ?? null]
}

/**
 * The headline coverage figure: how much of the warehouse this build can trace.
 * Null when the graph carries no column total to be a fraction of.
 */
export function coveragePercent(coverage: Coverage | undefined): number | null {
  const total = coverage?.columns_total
  if (!total) return null
  return Math.round(((coverage?.columns_traced ?? 0) / total) * 100)
}

/**
 * The three numbers that qualify every answer this tool gives, each next to the
 * gap it leaves: "2,551 / 3,293" is only actionable beside the 742 it is missing,
 * and the 742 is a link to the list of them.
 */
export function coverageRows(coverage: Coverage | undefined): CoverageRow[] {
  const cov = coverage ?? {}
  const [traced, columns] = ratio(cov.columns_traced, cov.columns_total)
  const [bound, models] = ratio(cov.models_bound, cov.models_total)
  const resolved = (cov.mbql_cards_resolved ?? 0) + (cov.native_cards_resolved ?? 0)
  const cards = (cov.mbql_cards_total ?? 0) + (cov.native_cards_total ?? 0)

  const untraced = cov.untraced_columns?.length ?? 0
  const unbound = cov.unbound_models?.length ?? 0
  const unresolved = cov.unresolved_cards?.length ?? 0

  const inferred = cov.columns_inferred ?? 0
  const excluded = cov.models_excluded ?? 0

  return [
    {
      key: 'columns',
      label: 'Columns traced',
      value: traced,
      total: columns,
      gap: untraced,
      list: untraced > 0 ? 'untraced-columns' : null,
      gapLabel: untraced > 0 ? `${untraced.toLocaleString()} untraced` : null,
      hint: 'Traced means sqlglot resolved the column back to its upstreams. Untraced columns break lineage chains.',
      // "2,551 traced" reads as one grade of evidence until you learn most of it
      // was matched by name rather than parsed out of the SQL (#119).
      note: inferred > 0 ? `${inferred.toLocaleString()} of those inferred${sharePart(inferred, traced)}` : null,
      noteHint:
        inferred > 0
          ? 'Inferred columns were matched by star-expansion or name, not parsed from the SQL — weaker evidence than an exact or parsed trace.'
          : null,
    },
    {
      key: 'models',
      label: 'Models bound to Metabase',
      value: bound,
      total: models,
      gap: unbound,
      list: unbound > 0 ? 'unbound-models' : null,
      gapLabel: unbound > 0 ? `${unbound.toLocaleString()} unbound` : null,
      hint: 'A model is bound when Metabase has a table at its database, schema and physical name.',
      note: excluded > 0 ? `${excluded.toLocaleString()} excluded by config` : null,
      noteHint:
        excluded > 0
          ? 'Models metabase.exclude_packages / exclude_models in stitch.yml keeps out of this ratio — nobody expects them in Metabase. They keep their lineage.'
          : null,
    },
    {
      key: 'cards',
      label: 'Cards resolved',
      value: resolved,
      total: cards || null,
      gap: unresolved,
      list: unresolved > 0 ? 'unresolved-cards' : null,
      gapLabel: unresolved > 0 ? `${unresolved.toLocaleString()} unresolved` : null,
      hint: 'An unresolved card is one whose query stitch could not walk — its dependencies are missing from the graph.',
      note: null,
      noteHint: null,
    },
  ]
}

/** " (52%)" — omitted rather than divided by zero when there is no base to be a share of. */
function sharePart(value: number, base: number): string {
  return base > 0 ? ` (${Math.round((value / base) * 100)}%)` : ''
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

// ---------------------------------------------------------------------------
// The build stamp every page carries

const STALE_DAYS = 7

export interface BuildStamp {
  /** "Built today, 16:07" — the whole line, ready to render. */
  text: string
  /** Whole calendar days since the build. */
  ageDays: number
  /** Old enough that the answers on screen may be wrong (principle 05). */
  stale: boolean
}

function twoDigits(value: number): string {
  return value < 10 ? `0${value}` : String(value)
}

/** Calendar days between two instants, in local time — not elapsed hours. */
function calendarDaysBetween(then: Date, now: Date): number {
  const a = new Date(then.getFullYear(), then.getMonth(), then.getDate()).getTime()
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  return Math.round((b - a) / 86_400_000)
}

/**
 * When this graph was built, as a reader thinks of it. A stale graph is a wrong
 * answer, so this sits in the header of every page — and says so past a week.
 * Null when the graph carries no parseable timestamp.
 */
export function buildStamp(generatedAt: string | null | undefined, now: Date): BuildStamp | null {
  if (!generatedAt) return null
  const parsed = Date.parse(generatedAt)
  if (!Number.isFinite(parsed)) return null
  const built = new Date(parsed)
  const ageDays = Math.max(0, calendarDaysBetween(built, now))
  const clock = `${twoDigits(built.getHours())}:${twoDigits(built.getMinutes())}`
  let when: string
  if (ageDays === 0) when = 'today'
  else if (ageDays === 1) when = 'yesterday'
  else if (ageDays < STALE_DAYS) when = `${ageDays} days ago`
  else {
    when = `${built.getFullYear()}-${twoDigits(built.getMonth() + 1)}-${twoDigits(built.getDate())}`
  }
  return { text: `Built ${when}, ${clock}`, ageDays, stale: ageDays >= STALE_DAYS }
}

// ---------------------------------------------------------------------------
// Where to start

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

export interface HomeExample {
  node: GraphNode
  /** Fully qualified, the way it would be pasted into a PR: `dim_users.user_id`. */
  label: string
}

/**
 * The identifiers under the home search field. They are real names out of THIS
 * graph, not illustrations (principle 05): the columns the BI layer leans on
 * hardest, then the dashboard with the most on it — the things most likely to be
 * what the reader came to type.
 */
export function homeExamples(index: GraphIndex, limit = 3): HomeExample[] {
  // A column binds to ONE Metabase field, so counting its edges ranks nothing —
  // every column ties at 1. What separates them is what the field feeds, so the
  // score is the cards a column reaches, which is also the consequence of
  // changing it (principle 01).
  const cardsPerField = new Map<string, number>()
  const dashboardCards = new Map<string, number>()
  for (const edge of index.graph.edges) {
    const from = index.nodesById.get(edge.from)
    const to = index.nodesById.get(edge.to)
    if (!from || !to) continue
    if (from.node_type === 'mb_field' && to.node_type === 'mb_card') {
      cardsPerField.set(edge.from, (cardsPerField.get(edge.from) ?? 0) + 1)
    } else if (from.node_type === 'mb_card' && to.node_type === 'mb_dashboard') {
      dashboardCards.set(edge.to, (dashboardCards.get(edge.to) ?? 0) + 1)
    }
  }

  const columnCards = new Map<string, number>()
  for (const edge of index.graph.edges) {
    const from = index.nodesById.get(edge.from)
    const to = index.nodesById.get(edge.to)
    if (!from || !to || from.node_type !== 'column') continue
    if (to.node_type === 'mb_field') {
      columnCards.set(edge.from, (columnCards.get(edge.from) ?? 0) + (cardsPerField.get(edge.to) ?? 0))
    } else if (to.node_type === 'mb_card') {
      columnCards.set(edge.from, (columnCards.get(edge.from) ?? 0) + 1)
    }
  }

  const busiest = (counts: Map<string, number>, take: number): GraphNode[] =>
    [...counts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .flatMap(([nodeId]) => {
        const node = index.nodesById.get(nodeId)
        return node ? [node] : []
      })
      .slice(0, take)

  const columnSlots = Math.max(1, limit - 1)
  const picked = [...busiest(columnCards, columnSlots), ...busiest(dashboardCards, limit)]
  // A graph with no BI edges yet still deserves somewhere to start.
  if (picked.length < limit) {
    for (const node of index.nodes) {
      if (picked.length >= limit) break
      if (node.node_type !== 'column' && node.node_type !== 'model') continue
      if (!picked.some((seen) => seen.node_id === node.node_id)) picked.push(node)
    }
  }

  return picked.slice(0, limit).map((node) => {
    const owner = node.node_type === 'column' ? ownerName(index, node) : null
    return { node, label: owner ? `${owner}.${displayName(node)}` : displayName(node) }
  })
}

// ---------------------------------------------------------------------------
// The lists behind the rows

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
