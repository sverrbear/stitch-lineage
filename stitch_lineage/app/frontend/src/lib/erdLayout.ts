// ERD placement (#65, #76, #101). Pure TS, no DOM / React Flow imports.
//
// An ERD is a MODEL, not a flow: the reader is looking for "what does this fact join
// to", not "what runs before what". So this does not layer left-to-right (that is
// the lineage view's job) — it solves for coordinates.
//
// The arrangement is stated as an optimisation, not a set of seats (#101):
//
//   HARD CONSTRAINTS, guaranteed rather than aimed at —
//     * no two cards overlap, and every pair clears `gutter`, measured on each
//       card's REAL rendered width and height (an expanded table claims the room it
//       takes) — see `layoutSeparation`;
//     * tables with no relationship at all are not interleaved with the ones that
//       have them: they get their own band, three componentGaps below everything
//       connected, packed tight so it reads as an appendix rather than as the next
//       constellation down.
//
//   OBJECTIVES, minimised —
//     * total relationship length and the number of relationships that cross;
//     * tables that join each other sit together (graph communities);
//     * the busiest tables sit interior and degree-1 leaves peripheral. This one
//       pulls AGAINST edge length on a real schema — seating twenty leaves far enough
//       out to clear a hub's own satellites is what makes the relationships long — so
//       it is a weighted objective (`radialStrength`), not a constraint.
//
//   MACHINERY — greedy-modularity community detection (`communitiesOf`), stress
//   majorization seeded on degree-ordered annuli, with rectangular collision weighted
//   by degree (`layoutStress`), and guaranteed overlap removal (`layoutSeparation`).
//   Crossings are not optimised directly: majorization from that seed already brings
//   them to zero on the real scopes, and a swap-based local search over the result was
//   measured to change nothing, so it is not carried.
//
// Every step is ordered by table id and seeded by constants, so the same graph
// always gives the same map and the canvas does not reshuffle between opens.
// `layoutMetrics` is the independent scorer: what a change claims is what the suite
// asserts.

import { separateBoxes, type SeparationBox } from './layoutSeparation'
import { stressLayout } from './layoutStress'

export interface ErdLayoutNode {
  id: string
  /** Rendered height in px; drives spacing (see `erdNodeHeight`). */
  height: number
  /** Rendered width in px; defaults to `cardWidth`. */
  width?: number
}

/** A declared or staged relationship: `from` is the FK side, `to` what it references. */
export interface ErdLayoutEdge {
  from: string
  to: string
}

export interface ErdPosition {
  x: number
  y: number
}

export interface ErdLayoutOptions {
  /** Fallback card width when a node has not been measured. */
  cardWidth?: number
  /** Breathing room the optimiser AIMS for between two related cards. */
  gap?: number
  /** Minimum clearance the layout GUARANTEES between any two cards. */
  gutter?: number
  /** Space between two constellations. */
  componentGap?: number
  /** Columns in the trailing band of unrelated tables; square by default. */
  isolatedColumns?: number
  /** Majorization iterations per component. */
  iterations?: number
  /** What an edge leaving its community costs, in ideal lengths. */
  clusterSpread?: number
  /** Weight of the hubs-in / leaves-out term, relative to one relationship. */
  radialStrength?: number
}

const DEFAULTS = {
  cardWidth: 300,
  gap: 52,
  gutter: 28,
  componentGap: 120,
  iterations: 240,
  clusterSpread: 1.15,
  radialStrength: 0.55,
} as const

/**
 * The gap in front of the band of unrelated tables, as a multiple of `componentGap`.
 * A constellation and a block of tables that join nothing are different KINDS of
 * thing (#101): one componentGap between them reads as "the next cluster down",
 * which is the interleaving the band exists to prevent.
 */
const ISOLATED_BAND_GAP = 3

// The rendered box of an .erd-node, approximated from styles.css: title block +
// schema line, the padded column list, and the expand/collapse button. Real
// measurements replace this as soon as React Flow has them (#62).
const NODE_HEADER_PX = 52
const NODE_ROW_PX = 20
const NODE_LIST_PADDING_PX = 8
const NODE_EXPAND_PX = 26

/** Height of a table node showing `rows` columns (with the expand control when collapsible). */
export function erdNodeHeight(rows: number, collapsible: boolean): number {
  return (
    NODE_HEADER_PX +
    NODE_LIST_PADDING_PX +
    Math.max(0, rows) * NODE_ROW_PX +
    (collapsible ? NODE_EXPAND_PX : 0)
  )
}

interface Size {
  w: number
  h: number
}

interface Component {
  ids: string[]
  edges: ErdLayoutEdge[]
}

/**
 * Position every node. Deterministic: the same nodes and edges always give the
 * same map, whatever order they arrive in.
 */
export function layoutErd(
  nodes: ErdLayoutNode[],
  edges: ErdLayoutEdge[],
  options: ErdLayoutOptions = {},
): Map<string, ErdPosition> {
  const settings = { ...DEFAULTS, ...options }
  const { cardWidth, gap, gutter, componentGap } = settings
  const positions = new Map<string, ErdPosition>()
  if (nodes.length === 0) return positions

  const size = new Map<string, Size>()
  for (const node of nodes) {
    size.set(node.id, {
      w: Math.max(40, node.width ?? cardWidth),
      h: Math.max(24, node.height),
    })
  }

  const clean = usableEdges(nodes, edges)
  const components = connectedComponents(nodes, clean)
  const connected = components.filter((component) => component.edges.length > 0)
  const isolated = components
    .filter((component) => component.edges.length === 0)
    .flatMap((component) => component.ids)
    .sort()

  // What one relationship should span, from what the cards actually measure: the
  // typical card's larger dimension plus its breathing room, so two related tables
  // that end up adjacent are adjacent rather than merely near. A fixed 300 was what
  // made a scope of wide cards (#80) place as if they were narrow.
  const typical = (pick: (s: Size) => number) =>
    median(nodes.map((node) => pick(size.get(node.id) as Size)))
  const idealLength = Math.max(typical((s) => s.w), typical((s) => s.h)) + gap

  let cursorY = 0
  for (const component of connected) {
    const boxes = placeComponent(component, size, { ...settings, idealLength })
    const left = Math.min(...boxes.map((box) => box.cx - box.w / 2))
    const top = Math.min(...boxes.map((box) => box.cy - box.h / 2))
    for (const box of boxes) {
      positions.set(box.id, {
        x: box.cx - box.w / 2 - left,
        y: box.cy - box.h / 2 - top + cursorY,
      })
    }
    cursorY += Math.max(...boxes.map((box) => box.cy + box.h / 2)) - top + componentGap
  }

  if (isolated.length > 0) {
    // the band's own gap: a block of tables that join nothing must not read as the
    // next constellation down (#101)
    if (connected.length > 0) cursorY += componentGap * (ISOLATED_BAND_GAP - 1)
    placeIsolatedBand(isolated, size, cursorY, positions, {
      cardWidth,
      gutter,
      columns: options.isolatedColumns,
    })
  }

  return positions
}

/**
 * One constellation: community detection, then majorization for the coordinates,
 * then the overlap guarantee. Returns centre-based boxes.
 */
function placeComponent(
  component: Component,
  size: Map<string, Size>,
  options: {
    gutter: number
    iterations: number
    clusterSpread: number
    radialStrength: number
    idealLength: number
  },
): SeparationBox[] {
  const { gutter } = options
  const adjacency = neighbours(component)
  const communities = communitiesOf(component, adjacency)
  const nodes = component.ids.map((id) => {
    const measured = size.get(id) as Size
    return { id, w: measured.w, h: measured.h }
  })

  const centres = stressLayout(nodes, component.edges, communities, options)
  const boxes: SeparationBox[] = nodes.map((node) => {
    const at = centres.get(node.id) ?? { x: 0, y: 0 }
    // No mass here, deliberately. Degree-weighted mass belongs in the sweeps
    // INTERLEAVED with majorization, where it biases which card yields while there is
    // still freedom to move; carrying it into the pass that merely enforces the gutter
    // measured slightly worse on both counts, so this last repair stays even-handed.
    return { id: node.id, cx: at.x, cy: at.y, w: node.w, h: node.h }
  })

  separateBoxes(boxes, gutter)
  return boxes
}

/**
 * The band of tables that join nothing. Alphabetical, because the only thing a
 * reader can do with an unrelated table is look it up by name; packed at the
 * minimum gutter with each row only as tall as what is in it, so the band reads as
 * a dense appendix rather than competing with the constellations above it. Every
 * pair is clear on an axis by construction, so this needs no separation pass.
 */
function placeIsolatedBand(
  ids: readonly string[],
  size: Map<string, Size>,
  top: number,
  into: Map<string, ErdPosition>,
  options: { cardWidth: number; gutter: number; columns?: number },
): void {
  const { cardWidth, gutter } = options
  // roughly square, so a schema of 130 unrelated tables stays a block you can
  // zoom-to-fit rather than a 130-storey tower
  const columns = Math.max(1, options.columns ?? Math.ceil(Math.sqrt(ids.length)))
  // the widest card sets the column pitch, not the fallback width: cards measure
  // what their content needs (#80), and a fixed pitch overlapped every card wider
  const pitchX = Math.max(cardWidth, ...ids.map((id) => (size.get(id) as Size).w)) + gutter
  let y = top
  for (let start = 0; start < ids.length; start += columns) {
    const row = ids.slice(start, start + columns)
    row.forEach((id, column) => into.set(id, { x: column * pitchX, y }))
    y += Math.max(...row.map((id) => (size.get(id) as Size).h)) + gutter
  }
}

function median(values: number[]): number {
  if (values.length === 0) return 0
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.floor((sorted.length - 1) / 2)]
}

/** Edges the layout can use: both ends present, no self-loops, no duplicates. */
function usableEdges(nodes: ErdLayoutNode[], edges: ErdLayoutEdge[]): ErdLayoutEdge[] {
  const known = new Set(nodes.map((node) => node.id))
  const seen = new Set<string>()
  const usable: ErdLayoutEdge[] = []
  for (const edge of edges) {
    if (edge.from === edge.to) continue
    if (!known.has(edge.from) || !known.has(edge.to)) continue
    const key = `${edge.from} ${edge.to}`
    if (seen.has(key)) continue
    seen.add(key)
    usable.push(edge)
  }
  return usable
}

function connectedComponents(nodes: ErdLayoutNode[], edges: ErdLayoutEdge[]): Component[] {
  const parent = new Map<string, string>()
  const find = (id: string): string => {
    let root = id
    while (parent.get(root) !== root) root = parent.get(root) as string
    let walk = id
    while (walk !== root) {
      const next = parent.get(walk) as string
      parent.set(walk, root)
      walk = next
    }
    return root
  }
  for (const node of nodes) parent.set(node.id, node.id)
  for (const edge of edges) {
    const a = find(edge.from)
    const b = find(edge.to)
    if (a !== b) parent.set(a, b)
  }

  const byRoot = new Map<string, Component>()
  for (const node of nodes) {
    const root = find(node.id)
    const component = byRoot.get(root)
    if (component) component.ids.push(node.id)
    else byRoot.set(root, { ids: [node.id], edges: [] })
  }
  for (const edge of edges) byRoot.get(find(edge.from))?.edges.push(edge)

  const components = [...byRoot.values()]
  for (const component of components) component.ids.sort()
  // biggest, most connected first — the main constellation leads
  components.sort(
    (a, b) =>
      b.ids.length - a.ids.length ||
      b.edges.length - a.edges.length ||
      a.ids[0].localeCompare(b.ids[0]),
  )
  return components
}

function neighbours(component: Component): Map<string, Set<string>> {
  const map = new Map<string, Set<string>>()
  for (const id of component.ids) map.set(id, new Set())
  for (const edge of component.edges) {
    map.get(edge.from)?.add(edge.to)
    map.get(edge.to)?.add(edge.from)
  }
  return map
}

/**
 * The communities of one component: greedy modularity (Clauset–Newman–Moore).
 * Every table starts in a community of its own and the merge that gains the most
 * modularity is taken, until no merge gains anything. Only a pair with an edge
 * between them can gain — a pair with none always scores negative — so a pass
 * costs O(edges), and a scope's relationship graph is tens of edges.
 *
 * A pure star comes back as ONE community (folding a leaf into the hub always
 * gains), which is what a real analytics schema mostly looks like; two facts that
 * share a conformed dimension come back as two, which is the case clustering
 * exists for. Ties go to the lexicographically smallest pair, so the same graph
 * always clusters the same way.
 */
export function communitiesOf(
  component: Component,
  adjacency: Map<string, Set<string>>,
): string[][] {
  const m = component.edges.length
  if (m === 0) return component.ids.map((id) => [id])

  const members = new Map<string, string[]>()
  const degreeSum = new Map<string, number>()
  for (const id of component.ids) {
    members.set(id, [id])
    degreeSum.set(id, adjacency.get(id)?.size ?? 0)
  }
  // edge counts between communities, held both ways round
  const between = new Map<string, Map<string, number>>()
  const link = (a: string, b: string, count: number) => {
    if (a === b) return
    const row = between.get(a) ?? new Map<string, number>()
    row.set(b, (row.get(b) ?? 0) + count)
    between.set(a, row)
  }
  for (const edge of component.edges) {
    link(edge.from, edge.to, 1)
    link(edge.to, edge.from, 1)
  }

  for (;;) {
    let bestGain = 0
    let best: [string, string] | null = null
    for (const a of [...between.keys()].sort()) {
      for (const b of [...(between.get(a) ?? new Map()).keys()].sort()) {
        if (a >= b) continue
        const shared = between.get(a)?.get(b) ?? 0
        if (shared === 0) continue
        const gain = shared / m - ((degreeSum.get(a) ?? 0) * (degreeSum.get(b) ?? 0)) / (2 * m * m)
        if (gain > bestGain + 1e-12) {
          bestGain = gain
          best = [a, b]
        }
      }
    }
    if (!best) break
    const [keep, drop] = best
    members.set(keep, [...(members.get(keep) ?? []), ...(members.get(drop) ?? [])].sort())
    members.delete(drop)
    degreeSum.set(keep, (degreeSum.get(keep) ?? 0) + (degreeSum.get(drop) ?? 0))
    degreeSum.delete(drop)
    for (const [other, count] of between.get(drop) ?? new Map<string, number>()) {
      between.get(other)?.delete(drop)
      if (other === keep) continue
      link(keep, other, count)
      link(other, keep, count)
    }
    between.delete(drop)
    between.get(keep)?.delete(keep)
  }

  // biggest cluster first, so it takes the middle of the arrangement
  return [...members.values()].sort((a, b) => b.length - a.length || a[0].localeCompare(b[0]))
}
