// Star-schema ERD placement (#65, #76), clustered by connectivity (#101). Pure TS,
// no DOM / React Flow imports.
//
// An ERD is a MODEL, not a flow: the reader is looking for "what does this fact
// join to", not "what runs before what". So this does not layer left-to-right
// (that is the lineage view's job) — it draws constellations, the way a model
// view does. At full scope the placement itself has to carry the structure, so the
// arrangement is built from the relationship graph and nothing else:
//
//   1. split the scope into connected components over its relationship edges;
//   2. split each component into COMMUNITIES — greedy modularity over the same
//      edges, so tables that join each other end up in one cluster and the cluster
//      boundary is the graph's, not a naming convention's;
//   3. inside a community the busiest table is the hub, and the rest sit on rings
//      by how many hops away they are, each beside the neighbour it hangs off;
//   4. **degree-1 tables ring the OUTSIDE.** A table with exactly one relationship
//      is the end of a branch and carries no structure, so it goes beyond the last
//      ring of the connective core, on its parent's side: the picture reads
//      hubs-in-the-middle, leaves-at-the-edge instead of "everything orbits";
//   5. a table whose relationships span several communities (a conformed dimension)
//      sits BETWEEN them rather than inside one of them;
//   6. communities are placed as blocks, each taking the free slot that leaves it
//      nearest the communities it shares edges with — total edge length is the
//      objective, not filling a grid;
//   7. a bounded separation pass guarantees no two cards overlap, using their real
//      measured sizes, and it moves leaves before hubs;
//   8. tables with NO relationship at all are not interleaved with any of it: they
//      get their own band, below everything connected, clearly separated.
//
// Every step is ordered by table id, so the same graph always gives the same map
// and the canvas does not reshuffle between opens.

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
  /** Breathing room between two cards. */
  gap?: number
  /** Space between two constellations. */
  componentGap?: number
  /** Columns in the trailing grid of unrelated tables; square by default. */
  isolatedColumns?: number
  /** Iterations of the overlap-separation pass. */
  separationPasses?: number
}

const DEFAULTS = {
  cardWidth: 300,
  gap: 52,
  componentGap: 120,
  separationPasses: 60,
} as const

/**
 * The gap in front of the band of unrelated tables, as a multiple of `componentGap`.
 * A constellation and a grid of tables that join nothing are different KINDS of
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

interface Box {
  id: string
  w: number
  h: number
  x: number
  y: number
  /** How far the separation pass may push it: hubs hold their ground. */
  mobility: number
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
  const { cardWidth, gap, componentGap, separationPasses } = { ...DEFAULTS, ...options }
  const positions = new Map<string, ErdPosition>()
  if (nodes.length === 0) return positions

  const size = new Map<string, { w: number; h: number }>()
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

  let cursorY = 0
  for (const component of connected) {
    const boxes = placeComponent(component, size, { cardWidth, gap, componentGap })
    separate(boxes, gap, separationPasses)
    const top = Math.min(...boxes.map((box) => box.y))
    const left = Math.min(...boxes.map((box) => box.x))
    for (const box of boxes) {
      positions.set(box.id, { x: box.x - left, y: box.y - top + cursorY })
    }
    cursorY += Math.max(...boxes.map((box) => box.y + box.h)) - top + componentGap
  }

  if (isolated.length > 0) {
    // the band's own gap: a grid of tables that join nothing must not read as the
    // next constellation down (#101)
    if (connected.length > 0) cursorY += componentGap * (ISOLATED_BAND_GAP - 1)
    // roughly square, so a schema of 40 unrelated tables stays a block you can
    // zoom-to-fit rather than a 40-storey tower
    const columns = Math.max(1, options.isolatedColumns ?? Math.ceil(Math.sqrt(isolated.length)))
    const pitchY = Math.max(...isolated.map((id) => size.get(id)?.h ?? 0)) + gap
    // the widest card sets the column pitch, not the fallback width: cards measure what
    // their content needs (a long model name buys a wider card, #80), and a fixed 300
    // pitch overlapped every card that came out wider than that
    const pitchX = Math.max(cardWidth, ...isolated.map((id) => size.get(id)?.w ?? 0)) + gap
    isolated.forEach((id, i) => {
      positions.set(id, {
        x: (i % columns) * pitchX,
        y: cursorY + Math.floor(i / columns) * pitchY,
      })
    })
  }

  return positions
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
function communitiesOf(component: Component, adjacency: Map<string, Set<string>>): string[][] {
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
  return [...members.values()].sort(
    (a, b) => b.length - a.length || a[0].localeCompare(b[0]),
  )
}

/** One community, placed around its own origin. */
interface Cluster {
  boxes: Box[]
  hub: string
  halfW: number
  halfH: number
}

/**
 * Seats for one ring: the occupants keep the angular ORDER their preferences give
 * them (so a leaf stays on its parent's side of the hub) but are spaced evenly, and
 * the whole ring is then rotated to sit as close to those preferences as it can.
 * Even spacing is what stops a ring becoming the blob the separation pass used to
 * have to tease apart.
 */
function seatRing(
  occupants: readonly string[],
  preferred: Map<string, number>,
  angle: Map<string, number>,
): void {
  const count = occupants.length
  if (count === 0) return
  const even = (index: number) => (2 * Math.PI * index) / count
  // no preference is a preference for the even seat, which keeps the sort stable
  const byId = [...occupants].sort((a, b) => a.localeCompare(b))
  byId.forEach((id, index) => {
    if (!preferred.has(id)) preferred.set(id, even(index))
  })
  const sorted = [...occupants].sort(
    (a, b) => (preferred.get(a) as number) - (preferred.get(b) as number) || a.localeCompare(b),
  )
  let sin = 0
  let cos = 0
  sorted.forEach((id, index) => {
    const delta = (preferred.get(id) as number) - even(index)
    sin += Math.sin(delta)
    cos += Math.cos(delta)
  })
  const offset = sin === 0 && cos === 0 ? 0 : Math.atan2(sin, cos)
  sorted.forEach((id, index) => angle.set(id, even(index) + offset))
}

/**
 * Place one community: the busiest table in the middle, the rest on rings by how
 * many relationship hops away they are, and **every degree-1 table beyond the last
 * ring of the core** (#101). A leaf carries no structure — it is the end of a
 * branch — so putting it inside the rings only pushes the tables that DO carry
 * structure further apart. It goes outside, on its parent's side, which keeps its
 * one edge short while the middle of the picture stays the connective tissue.
 */
function placeCluster(
  ids: readonly string[],
  adjacency: Map<string, Set<string>>,
  degreeOf: (id: string) => number,
  size: Map<string, { w: number; h: number }>,
  options: { cardWidth: number; gap: number },
): Cluster {
  const { cardWidth, gap } = options
  const inside = new Set(ids)
  const measured = (id: string) => size.get(id) ?? { w: cardWidth, h: 120 }
  const boxOf = (id: string, mobility: number): Box => {
    const { w, h } = measured(id)
    return { id, w, h, x: 0, y: 0, mobility }
  }

  // The core is what has more than one relationship; a community of nothing but
  // leaves (a two-table component) still needs a centre, so its first table takes it.
  const core = ids.filter((id) => degreeOf(id) > 1)
  const centre = core.length > 0 ? core : [...ids].sort()[0] !== undefined ? [[...ids].sort()[0]] : []
  const hub = [...centre].sort((a, b) => degreeOf(b) - degreeOf(a) || a.localeCompare(b))[0]
  if (hub === undefined) return { boxes: [], hub: '', halfW: 0, halfH: 0 }
  const coreSet = new Set(centre)

  // hops from the hub, through the core only: a ring is a distance in relationships
  const hop = new Map<string, number>([[hub, 0]])
  let frontier = [hub]
  while (frontier.length > 0) {
    const next: string[] = []
    for (const id of frontier) {
      for (const other of [...(adjacency.get(id) ?? [])].sort()) {
        if (!inside.has(other) || !coreSet.has(other) || hop.has(other)) continue
        hop.set(other, (hop.get(id) as number) + 1)
        next.push(other)
      }
    }
    frontier = next
  }
  // a core table reachable only through a leaf still needs a ring: the next one out
  const reached = Math.max(0, ...hop.values())
  for (const id of centre) if (!hop.has(id)) hop.set(id, reached + 1)
  const lastCoreRing = Math.max(0, ...hop.values())

  const others = ids.filter((id) => id !== hub)
  const widest = Math.max(cardWidth, ...others.map((id) => measured(id).w))
  const tallest = Math.max(measured(hub).h, ...others.map((id) => measured(id).h))
  const rx = measured(hub).w / 2 + widest / 2 + gap
  const ry = measured(hub).h / 2 + tallest / 2 + gap
  // Ramanujan's ellipse perimeter — close enough to count seats with
  const seats = (ring: number) => {
    const a = rx * ring
    const b = ry * ring
    const perimeter = Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b)))
    return Math.max(3, Math.floor(perimeter / (widest + gap)))
  }

  const angle = new Map<string, number>()
  const preferred = new Map<string, number>()
  const ringOf = new Map<string, number>()

  // the core, ring by ring: each table wants the angle of the neighbour it hangs off
  for (let ring = 1; ring <= lastCoreRing; ring++) {
    const occupants = centre.filter((id) => hop.get(id) === ring).sort()
    if (occupants.length === 0) continue
    for (const id of occupants) {
      const parents = [...(adjacency.get(id) ?? [])].filter((other) => angle.has(other))
      if (parents.length === 0) continue
      let sin = 0
      let cos = 0
      for (const parent of parents) {
        sin += Math.sin(angle.get(parent) as number)
        cos += Math.cos(angle.get(parent) as number)
      }
      if (sin !== 0 || cos !== 0) preferred.set(id, Math.atan2(sin, cos))
    }
    seatRing(occupants, preferred, angle)
    for (const id of occupants) ringOf.set(id, ring)
  }

  // then the leaves, outside all of it, each one still beside its own parent
  const leaves = others.filter((id) => !coreSet.has(id))
  const parentAngle = (id: string): number | undefined => {
    const parents = [...(adjacency.get(id) ?? [])]
      .filter((other) => inside.has(other) && other !== id)
      .sort((a, b) => degreeOf(b) - degreeOf(a) || a.localeCompare(b))
    for (const parent of parents) {
      const known = angle.get(parent)
      if (known !== undefined) return known
    }
    return undefined
  }
  const ordered = [...leaves].sort((a, b) => {
    const pa = parentAngle(a)
    const pb = parentAngle(b)
    if (pa !== undefined && pb !== undefined && pa !== pb) return pa - pb
    if (pa !== undefined && pb === undefined) return -1
    if (pa === undefined && pb !== undefined) return 1
    return a.localeCompare(b)
  })
  let placed = 0
  let ring = lastCoreRing + 1
  while (placed < ordered.length) {
    const occupants = ordered.slice(placed, placed + Math.min(seats(ring), ordered.length - placed))
    for (const id of occupants) {
      const near = parentAngle(id)
      if (near !== undefined) preferred.set(id, near)
    }
    seatRing(occupants, preferred, angle)
    for (const id of occupants) ringOf.set(id, ring)
    placed += occupants.length
    ring++
  }

  const boxes: Box[] = []
  const hubBox = boxOf(hub, 0.15)
  hubBox.x = -hubBox.w / 2
  hubBox.y = -hubBox.h / 2
  boxes.push(hubBox)
  for (const id of others) {
    const at = ringOf.get(id) ?? lastCoreRing + 1
    const occupants = [...ringOf.entries()].filter(([, r]) => r === at).length
    // a ring the graph forces to be crowded grows instead of overlapping
    const scale = Math.max(1, occupants / seats(at))
    const theta = angle.get(id) ?? 0
    const box = boxOf(id, coreSet.has(id) ? 0.45 : 1)
    box.x = Math.cos(theta) * rx * at * scale - box.w / 2
    box.y = Math.sin(theta) * ry * at * scale - box.h / 2
    boxes.push(box)
  }

  const halfW = Math.max(...boxes.map((box) => Math.max(Math.abs(box.x), Math.abs(box.x + box.w))))
  const halfH = Math.max(...boxes.map((box) => Math.max(Math.abs(box.y), Math.abs(box.y + box.h))))
  return { boxes, hub, halfW, halfH }
}

/** Grid slots in a spiral out from the middle: the first cluster takes the centre. */
function spiralSlots(count: number): Array<{ col: number; row: number }> {
  const slots = [{ col: 0, row: 0 }]
  let col = 0
  let row = 0
  let run = 1
  while (slots.length < count) {
    for (let i = 0; i < run; i++) slots.push({ col: ++col, row })
    for (let i = 0; i < run; i++) slots.push({ col, row: ++row })
    run++
    for (let i = 0; i < run; i++) slots.push({ col: --col, row })
    for (let i = 0; i < run; i++) slots.push({ col, row: --row })
    run++
  }
  return slots.slice(0, count)
}

/**
 * Where each cluster goes. Clusters are offered the spiral's slots in turn and take
 * the free one that leaves them nearest the clusters they share relationships with,
 * weighted by how many — so the objective is total edge length rather than filling a
 * grid (#101). With nothing shared the cost is flat and the earliest slot wins, which
 * keeps unrelated clusters packed and the whole thing deterministic.
 */
function arrangeClusters(
  clusters: readonly Cluster[],
  shared: (a: number, b: number) => number,
  componentGap: number,
): Array<{ x: number; y: number }> {
  const pitchX = Math.max(...clusters.map((c) => c.halfW)) * 2 + componentGap
  const pitchY = Math.max(...clusters.map((c) => c.halfH)) * 2 + componentGap
  const slots = spiralSlots(clusters.length + 4)
  const taken = new Map<number, number>() // cluster index -> slot index
  const used = new Set<number>()
  clusters.forEach((_, index) => {
    if (index === 0) {
      taken.set(0, 0)
      used.add(0)
      return
    }
    let bestSlot = -1
    let bestCost = Infinity
    for (let slot = 0; slot < Math.min(slots.length, index + 4); slot++) {
      if (used.has(slot)) continue
      let cost = 0
      for (const [other, otherSlot] of taken) {
        const weight = shared(index, other)
        if (weight === 0) continue
        cost +=
          weight *
          Math.hypot(
            (slots[slot].col - slots[otherSlot].col) * pitchX,
            (slots[slot].row - slots[otherSlot].row) * pitchY,
          )
      }
      if (cost < bestCost - 1e-9) {
        bestCost = cost
        bestSlot = slot
      }
    }
    taken.set(index, bestSlot)
    used.add(bestSlot)
  })
  return clusters.map((_, index) => {
    const slot = slots[taken.get(index) ?? 0]
    return { x: slot.col * pitchX, y: slot.row * pitchY }
  })
}

/** Lay one connected component out as clustered constellations; returns its boxes. */
function placeComponent(
  component: Component,
  size: Map<string, { w: number; h: number }>,
  options: { cardWidth: number; gap: number; componentGap: number },
): Box[] {
  const { cardWidth, gap, componentGap } = options
  const adjacency = neighbours(component)
  const degreeOf = (id: string) => adjacency.get(id)?.size ?? 0
  const communities = communitiesOf(component, adjacency)
  const communityOf = new Map<string, number>()
  communities.forEach((list, index) => list.forEach((id) => communityOf.set(id, index)))

  // Each community's own busiest table, worked out BEFORE anything is pulled out of
  // it: a hub must never be treated as a bridge, or its cluster loses its centre.
  const localHub = communities.map(
    (list) => [...list].sort((a, b) => degreeOf(b) - degreeOf(a) || a.localeCompare(b))[0],
  )

  /**
   * A conformed dimension: its relationships reach more than one community, so it
   * belongs BETWEEN them rather than inside either. Left out of the rings and placed
   * afterwards, at the weighted middle of the hubs it joins.
   */
  const bridges = component.ids
    .filter((id) => {
      const own = communityOf.get(id)
      if (own === undefined || localHub[own] === id) return false
      if ((communities[own]?.length ?? 0) < 2) return false
      const reach = new Set(
        [...(adjacency.get(id) ?? [])].map((other) => communityOf.get(other)).filter((c) => c !== undefined),
      )
      return reach.size > 1
    })
    .sort()
  const isBridge = new Set(bridges)

  const clusters: Cluster[] = []
  const clusterOf = new Map<number, number>() // community index -> cluster index
  communities.forEach((list, index) => {
    const members = list.filter((id) => !isBridge.has(id))
    if (members.length === 0) return
    clusterOf.set(index, clusters.length)
    clusters.push(placeCluster(members, adjacency, degreeOf, size, { cardWidth, gap }))
  })
  if (clusters.length === 0) return []

  // relationships between two clusters, bridges included: a bridge's two facts
  // should still be pulled towards each other
  const sharedCount = new Map<string, number>()
  for (const edge of component.edges) {
    const a = clusterOf.get(communityOf.get(edge.from) ?? -1)
    const b = clusterOf.get(communityOf.get(edge.to) ?? -1)
    if (a === undefined || b === undefined || a === b) continue
    const key = a < b ? `${a} ${b}` : `${b} ${a}`
    sharedCount.set(key, (sharedCount.get(key) ?? 0) + 1)
  }
  const shared = (a: number, b: number) => sharedCount.get(a < b ? `${a} ${b}` : `${b} ${a}`) ?? 0

  const offsets = arrangeClusters(clusters, shared, componentGap)
  const boxes: Box[] = []
  const byId = new Map<string, Box>()
  clusters.forEach((cluster, index) => {
    for (const box of cluster.boxes) {
      const moved = { ...box, x: box.x + offsets[index].x, y: box.y + offsets[index].y }
      boxes.push(moved)
      byId.set(moved.id, moved)
    }
  })

  for (const id of bridges) {
    const weight = new Map<number, number>()
    for (const other of [...(adjacency.get(id) ?? [])].sort()) {
      const cluster = clusterOf.get(communityOf.get(other) ?? -1)
      if (cluster === undefined) continue
      weight.set(cluster, (weight.get(cluster) ?? 0) + 1)
    }
    const { w, h } = size.get(id) ?? { w: cardWidth, h: 120 }
    const box: Box = { id, w, h, x: 0, y: 0, mobility: 1 }
    let sumX = 0
    let sumY = 0
    let total = 0
    for (const [cluster, count] of [...weight].sort((a, b) => a[0] - b[0])) {
      const hubBox = byId.get(clusters[cluster].hub)
      if (!hubBox) continue
      sumX += (hubBox.x + hubBox.w / 2) * count
      sumY += (hubBox.y + hubBox.h / 2) * count
      total += count
    }
    box.x = (total > 0 ? sumX / total : 0) - w / 2
    box.y = (total > 0 ? sumY / total : 0) - h / 2
    boxes.push(box)
    byId.set(id, box)
  }
  return boxes
}

/**
 * Push overlapping cards apart until none touch. Bounded passes and a fixed
 * visiting order keep it deterministic; hubs barely move, so the constellation
 * keeps its shape while its dimensions make room for each other.
 */
function separate(boxes: Box[], gap: number, passes: number): void {
  const margin = gap / 2
  const order = [...boxes].sort((a, b) => a.id.localeCompare(b.id))
  for (let pass = 0; pass < passes; pass++) {
    let moved = false
    for (let i = 0; i < order.length; i++) {
      for (let j = i + 1; j < order.length; j++) {
        const a = order[i]
        const b = order[j]
        const overlapX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x) + margin
        const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y) + margin
        if (overlapX <= 0 || overlapY <= 0) continue
        const share = a.mobility + b.mobility
        if (share === 0) continue
        moved = true
        if (overlapX < overlapY) {
          const push = overlapX * (a.x <= b.x ? -1 : 1)
          a.x += (push * a.mobility) / share
          b.x -= (push * b.mobility) / share
        } else {
          const push = overlapY * (a.y <= b.y ? -1 : 1)
          a.y += (push * a.mobility) / share
          b.y -= (push * b.mobility) / share
        }
      }
    }
    if (!moved) break
  }
}
