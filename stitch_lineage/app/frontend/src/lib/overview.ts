// The whole pipeline as one map (#46): every model and source at table grain,
// laid out left to right by dependency order, with the Metabase side aggregated
// on the right. Pure TS, unit-tested.
//
// The scale problem is real — 239 models, 953 cards, 6.3k nodes — and the answer
// is aggregation, not truncation. Models and sources always all fit (287 at
// worst). The Metabase side is the explosive one, so it is drawn per dashboard
// by default (61 nodes) and, when drawn per card, capped to the most-connected
// ones with the remainder counted, never silently dropped.

import type { GraphNode, NodeType } from '../types'
import { internalSchemas } from './erd'
import type { GraphIndex } from './graph'
import { layerEntities, rollUp, type RollupEdge } from './rollup'

export type MetabaseMode = 'none' | 'dashboards' | 'cards'

export interface OverviewScope {
  kind: 'schema' | 'tag'
  value: string
}

export interface OverviewOptions {
  /** null = every analytics schema. */
  scope?: OverviewScope | null
  metabase?: MetabaseMode
  /** Include package/warehouse-internal schemas (elementary, artifacts, …). */
  includeInternal?: boolean
  /** Cap on drawn Metabase nodes; the rest are counted in `omitted.metabase`. */
  maxMetabaseNodes?: number
}

export interface OverviewNode {
  node: GraphNode
  layer: number
  /** dbt columns rolled into this model (0 for a card or dashboard). */
  columnCount: number
  /** Metabase reach of a model, whether or not those nodes are drawn. */
  cardCount: number
  dashboardCount: number
}

export interface OverviewOmissions {
  /** Models hidden because their schema is a package's or the warehouse's own. */
  internal: number
  /** Models hidden by the scope filter. */
  outOfScope: number
  /** Every Metabase node not on the canvas: wrong grain for the mode, unconnected, or over the cap. */
  metabase: number
}

export interface OverviewData {
  nodes: OverviewNode[]
  edges: RollupEdge[]
  layers: Map<string, number>
  omitted: OverviewOmissions
  counts: Record<'model' | 'source' | 'card' | 'dashboard', number>
}

const DEFAULT_MAX_METABASE = 120

function isDbtEntity(type: NodeType): boolean {
  return type === 'model' || type === 'source'
}

function tagsOf(node: GraphNode): string[] {
  const tags = node.properties?.tags
  return Array.isArray(tags) ? tags.map(String) : []
}

function inScope(node: GraphNode, scope: OverviewScope | null | undefined): boolean {
  if (!scope) return true
  if (scope.kind === 'schema') return (node.schema ?? '') === scope.value
  return tagsOf(node).includes(scope.value)
}

/**
 * Table-grain map of the whole graph under the given controls.
 *
 * The rollup runs over the entire graph first — a model's card count is the
 * truth about that model, not an artefact of what is currently drawn — and the
 * controls then decide what makes it onto the canvas.
 */
export function overviewFor(index: GraphIndex, options: OverviewOptions = {}): OverviewData {
  const { scope = null, metabase = 'dashboards', includeInternal = false } = options
  const maxMetabase = options.maxMetabaseNodes ?? DEFAULT_MAX_METABASE
  const rolled = rollUp(index, index.nodes, index.graph.edges)
  const internal = includeInternal ? new Set<string>() : internalSchemas(index)

  const omitted: OverviewOmissions = { internal: 0, outOfScope: 0, metabase: 0 }
  const dbtNodes: OverviewNode[] = []
  const keptDbt = new Set<string>()
  for (const entry of rolled.nodes) {
    if (!isDbtEntity(entry.node.node_type)) continue
    if (internal.has(entry.node.schema ?? '')) {
      omitted.internal += 1
      continue
    }
    if (!inScope(entry.node, scope)) {
      omitted.outOfScope += 1
      continue
    }
    keptDbt.add(entry.node.node_id)
    dbtNodes.push({
      node: entry.node,
      layer: 0,
      columnCount: entry.memberCount,
      cardCount: 0,
      dashboardCount: 0,
    })
  }

  const { cardsOf, dashboardsOf } = metabaseReach(rolled.edges, index)
  for (const entry of dbtNodes) {
    entry.cardCount = cardsOf.get(entry.node.node_id)?.size ?? 0
    entry.dashboardCount = dashboardsOf.get(entry.node.node_id)?.size ?? 0
  }

  const wanted: NodeType | null =
    metabase === 'cards' ? 'mb_card' : metabase === 'dashboards' ? 'mb_dashboard' : null
  const reach = metabase === 'cards' ? cardsOf : dashboardsOf
  const connected = new Map<string, number>()
  if (wanted) {
    for (const [modelId, targets] of reach) {
      if (!keptDbt.has(modelId)) continue
      for (const target of targets) connected.set(target, (connected.get(target) ?? 0) + 1)
    }
  }
  for (const entry of rolled.nodes) {
    if (entry.node.node_type.startsWith('mb_') && !connected.has(entry.node.node_id)) {
      omitted.metabase += 1
    }
  }

  const ranked = [...connected.entries()].sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
  )
  const drawnMetabase = ranked.slice(0, maxMetabase)
  omitted.metabase += ranked.length - drawnMetabase.length

  const nodes = [...dbtNodes]
  const kept = new Set(keptDbt)
  for (const [nodeId] of drawnMetabase) {
    const node = index.nodesById.get(nodeId)
    if (!node) continue
    kept.add(nodeId)
    nodes.push({ node, layer: 0, columnCount: 0, cardCount: 0, dashboardCount: 0 })
  }

  // model -> dashboard is a two-hop path through cards, so when dashboards are
  // the drawn grain the map needs those shortcut edges to have any lines at all.
  const edges =
    metabase === 'dashboards'
      ? [...rolled.edges.filter((e) => kept.has(e.from) && kept.has(e.to)), ...dashboardEdges(rolled.edges, keptDbt, kept, dashboardsOf, index)]
      : rolled.edges.filter((e) => kept.has(e.from) && kept.has(e.to))

  const layers = layerEntities(
    nodes.map((entry) => entry.node.node_id),
    edges,
  )
  for (const entry of nodes) entry.layer = layers.get(entry.node.node_id) ?? 0

  const counts = { model: 0, source: 0, card: 0, dashboard: 0 }
  for (const entry of nodes) {
    if (entry.node.node_type === 'model') counts.model += 1
    else if (entry.node.node_type === 'source') counts.source += 1
    else if (entry.node.node_type === 'mb_card') counts.card += 1
    else if (entry.node.node_type === 'mb_dashboard') counts.dashboard += 1
  }

  return { nodes, edges, layers, omitted, counts }
}

/** Per dbt entity: the cards it reaches, and the dashboards those cards sit on. */
function metabaseReach(edges: RollupEdge[], index: GraphIndex) {
  const cardsOf = new Map<string, Set<string>>()
  const dashboardsOf = new Map<string, Set<string>>()
  const dashboardsOfCard = new Map<string, Set<string>>()
  for (const edge of edges) {
    const to = index.nodesById.get(edge.to)
    if (to?.node_type !== 'mb_dashboard') continue
    const set = dashboardsOfCard.get(edge.from)
    if (set) set.add(edge.to)
    else dashboardsOfCard.set(edge.from, new Set([edge.to]))
  }
  for (const edge of edges) {
    const from = index.nodesById.get(edge.from)
    const to = index.nodesById.get(edge.to)
    if (!from || to?.node_type !== 'mb_card') continue
    if (!isDbtEntity(from.node_type)) continue
    const cards = cardsOf.get(edge.from) ?? new Set<string>()
    cards.add(edge.to)
    cardsOf.set(edge.from, cards)
    const dashboards = dashboardsOf.get(edge.from) ?? new Set<string>()
    for (const dashboard of dashboardsOfCard.get(edge.to) ?? []) dashboards.add(dashboard)
    dashboardsOf.set(edge.from, dashboards)
  }
  return { cardsOf, dashboardsOf }
}

/** Shortcut model -> dashboard edges, weighted by the cards behind them. */
function dashboardEdges(
  edges: RollupEdge[],
  models: Set<string>,
  kept: Set<string>,
  dashboardsOf: Map<string, Set<string>>,
  index: GraphIndex,
): RollupEdge[] {
  const cardWeight = new Map<string, RollupEdge[]>()
  for (const edge of edges) {
    if (!models.has(edge.from)) continue
    if (index.nodesById.get(edge.to)?.node_type !== 'mb_card') continue
    const list = cardWeight.get(edge.from)
    if (list) list.push(edge)
    else cardWeight.set(edge.from, [edge])
  }
  const shortcuts: RollupEdge[] = []
  for (const [modelId, dashboards] of dashboardsOf) {
    if (!models.has(modelId)) continue
    const contributing = cardWeight.get(modelId) ?? []
    const weight = Math.max(...contributing.map((e) => e.weight), 0)
    const confidence = contributing.reduce<RollupEdge['confidence']>(
      (best, edge) => (best === 'exact' ? best : edge.confidence),
      contributing[0]?.confidence ?? 'exact',
    )
    for (const dashboard of dashboards) {
      if (!kept.has(dashboard)) continue
      shortcuts.push({ from: modelId, to: dashboard, weight, confidence, declared: false })
    }
  }
  return shortcuts
}
