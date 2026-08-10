// Grouping for the model page's dependency lists (#82). Pure TS, unit-tested.
//
// A model's fan-in and fan-out used to render as flat chip walls — every upstream
// model, every card and every dashboard in one undifferentiated pile, in whatever
// order the graph walk produced. That is unreadable on a real project (nine
// upstream models across four layers, five cards on five dashboards).
//
// Two groupings fix it, and both come out of the graph rather than out of naming
// conventions:
//   * dependencies group by LAYER (a dbt schema, or "sources") and the groups sit
//     in pipeline order, which is what the hop distance already measures;
//   * BI usage groups by DASHBOARD, because that is the unit a reader recognises —
//     "Smitten Master Dashboard (2 cards)", not ten card chips and five dashboard
//     chips with nothing tying them together.

import type { GraphNode } from '../types'
import { type GraphIndex, type Reach, walk } from './graph'

export interface LayerGroup {
  /** A dbt schema, or `sources` for dbt sources (which have no layer of their own). */
  label: string
  /** Farthest-upstream first (or nearest-downstream first), then by name. */
  entries: Reach[]
}

const SOURCES = 'sources'

function layerOf(node: GraphNode): string {
  if (node.node_type === 'source') return SOURCES
  const schema = node.schema?.trim()
  return schema && schema.length > 0 ? schema : 'no schema'
}

/**
 * Group models by layer, ordered along the pipeline.
 *
 * `direction` is the walk that produced `reaches`: `up` reads sources → staging →
 * intermediate → marts (so the farthest hop leads, and dbt sources always lead,
 * being the origin even when a model reads one directly); `down` reads outwards
 * from the model, nearest consumer first.
 */
export function layerGroups(reaches: readonly Reach[], direction: 'up' | 'down'): LayerGroup[] {
  const groups = new Map<string, Reach[]>()
  for (const reach of reaches) {
    const label = layerOf(reach.node)
    const bucket = groups.get(label)
    if (bucket) bucket.push(reach)
    else groups.set(label, [reach])
  }

  const byDistance = (a: Reach, b: Reach) =>
    (direction === 'up' ? b.depth - a.depth : a.depth - b.depth) ||
    a.node.name.localeCompare(b.node.name)

  // a layer sits where its outermost member does: farthest hop upstream, nearest
  // hop downstream
  const reach = (group: LayerGroup) =>
    direction === 'up'
      ? Math.max(...group.entries.map((entry) => entry.depth))
      : Math.min(...group.entries.map((entry) => entry.depth))
  const sign = direction === 'up' ? -1 : 1

  return [...groups.entries()]
    .map(([label, entries]) => ({ label, entries: [...entries].sort(byDistance) }))
    .sort((a, b) => {
      if (a.label === SOURCES) return -1
      if (b.label === SOURCES) return 1
      return sign * (reach(a) - reach(b)) || a.label.localeCompare(b.label)
    })
}

/**
 * How far a layer sits from the model, as a collapsed row can say it: `direct`,
 * `3 hops`, or a range when the layer spans several. This is the hint that makes a
 * closed group worth closing — the count says how much, this says how far (#104).
 */
export function hopRange(entries: readonly Reach[]): string {
  if (entries.length === 0) return ''
  const depths = entries.map((entry) => entry.depth)
  const low = Math.min(...depths)
  const high = Math.max(...depths)
  if (low === high) return low === 1 ? 'direct' : `${low} hops`
  return `${low}–${high} hops`
}

export interface DashboardUsage {
  /** `null` collects cards that are on no dashboard at all. */
  dashboard: GraphNode | null
  cards: Reach[]
}

const APPEARS_ON = new Set(['appears_on'])

/**
 * The model's cards, grouped by the dashboard they appear on. A card on two
 * dashboards is listed under both — that is the truth, and the header counts
 * distinct cards so the total never double-counts. Busiest dashboard first;
 * cards with no dashboard come last, so the groups a reader recognises lead.
 */
export function dashboardGroups(index: GraphIndex, cards: readonly Reach[]): DashboardUsage[] {
  const byDashboard = new Map<string, { dashboard: GraphNode; cards: Reach[] }>()
  const orphans: Reach[] = []
  for (const card of cards) {
    const dashboards = [...walk(index, card.node.node_id, 'down', { edgeTypes: APPEARS_ON }).reached.values()]
      .map((reach) => reach.node)
      .filter((node) => node.node_type === 'mb_dashboard')
    if (dashboards.length === 0) {
      orphans.push(card)
      continue
    }
    for (const dashboard of dashboards) {
      const group = byDashboard.get(dashboard.node_id)
      if (group) group.cards.push(card)
      else byDashboard.set(dashboard.node_id, { dashboard, cards: [card] })
    }
  }
  const groups: DashboardUsage[] = [...byDashboard.values()]
    .map((group) => ({
      dashboard: group.dashboard,
      cards: [...group.cards].sort((a, b) => a.node.name.localeCompare(b.node.name)),
    }))
    .sort(
      (a, b) =>
        b.cards.length - a.cards.length ||
        (a.dashboard?.name ?? '').localeCompare(b.dashboard?.name ?? ''),
    )
  if (orphans.length > 0) {
    groups.push({
      dashboard: null,
      cards: [...orphans].sort((a, b) => a.node.name.localeCompare(b.node.name)),
    })
  }
  return groups
}

/** Distinct dashboards in a grouping (the `null` bucket is not a dashboard). */
export function dashboardCount(groups: readonly DashboardUsage[]): number {
  return groups.filter((group) => group.dashboard !== null).length
}
