// Star-schema ERD placement (#65). Pure TS, no DOM / React Flow imports.
//
// An ERD is a MODEL, not a flow: the reader is looking for "what does this fact
// join to", not "what runs before what". So this does not layer left-to-right
// (that is the lineage view's job) — it draws constellations, the way a model
// view does:
//
//   1. split the scope into connected components over its relationship edges;
//   2. inside a component, the facts are the hubs — nodes with the most
//      relationships, and more than any of their neighbours;
//   3. their dimensions pack in rings AROUND them, so every edge is a short
//      spoke instead of a wire across the canvas;
//   4. a dimension shared by several facts (a conformed dim) sits BETWEEN them;
//   5. several facts become adjacent constellations, tiled;
//   6. a bounded separation pass guarantees no two cards overlap, using their
//      real measured sizes;
//   7. tables with no relationship at all end up in a grid underneath — present,
//      but peripheral.

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
    const boxes = placeConstellations(component, size, { cardWidth, gap, componentGap })
    separate(boxes, gap, separationPasses)
    const top = Math.min(...boxes.map((box) => box.y))
    const left = Math.min(...boxes.map((box) => box.x))
    for (const box of boxes) {
      positions.set(box.id, { x: box.x - left, y: box.y - top + cursorY })
    }
    cursorY += Math.max(...boxes.map((box) => box.y + box.h)) - top + componentGap
  }

  if (isolated.length > 0) {
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
 * The facts: nodes with at least two relationships, in the same league as the
 * most connected table here, and beaten by no neighbour. Two facts joined to
 * each other therefore BOTH lead (they are separate constellations sharing a
 * conformed dimension, not a fact orbiting a fact), while a dimension with a
 * couple of links stays a satellite. A component where nothing stands out falls
 * back to its most connected node, so there is always a centre to build around.
 */
function hubsOf(component: Component, adjacency: Map<string, Set<string>>): string[] {
  const degree = (id: string) => adjacency.get(id)?.size ?? 0
  const busiest = Math.max(...component.ids.map(degree))
  const hubs = component.ids.filter((id) => {
    if (degree(id) < 2 || degree(id) * 2 < busiest) return false
    for (const other of adjacency.get(id) ?? []) {
      if (degree(other) > degree(id)) return false
    }
    return true
  })
  if (hubs.length > 0) return hubs
  const best = [...component.ids].sort((a, b) => degree(b) - degree(a) || a.localeCompare(b))[0]
  return [best]
}

/** Lay one component out as one or more constellations; returns its boxes. */
function placeConstellations(
  component: Component,
  size: Map<string, { w: number; h: number }>,
  options: { cardWidth: number; gap: number; componentGap: number },
): Box[] {
  const { cardWidth, gap, componentGap } = options
  const adjacency = neighbours(component)
  const hubs = hubsOf(component, adjacency)
  const hubSet = new Set(hubs)
  const boxOf = (id: string, mobility: number): Box => {
    const { w, h } = size.get(id) ?? { w: cardWidth, h: 120 }
    return { id, w, h, x: 0, y: 0, mobility }
  }

  // every non-hub goes to the hub(s) it touches; a dimension several facts share
  // is "conformed" and belongs between them
  const owners = new Map<string, string[]>()
  for (const id of component.ids) {
    if (hubSet.has(id)) continue
    const touching = [...(adjacency.get(id) ?? [])].filter((other) => hubSet.has(other)).sort()
    owners.set(id, touching)
  }
  // a node hanging off a satellite still needs a home: give it its nearest hub
  for (const [id, touching] of owners) {
    if (touching.length > 0) continue
    const reached = nearestHub(id, adjacency, hubSet)
    owners.set(id, reached ? [reached] : [hubs[0]])
  }

  const exclusive = new Map<string, string[]>()
  for (const hub of hubs) exclusive.set(hub, [])
  const shared: string[] = []
  for (const [id, touching] of owners) {
    if (touching.length === 1) exclusive.get(touching[0])?.push(id)
    else shared.push(id)
  }
  for (const list of exclusive.values()) {
    list.sort(
      (a, b) => (adjacency.get(b)?.size ?? 0) - (adjacency.get(a)?.size ?? 0) || a.localeCompare(b),
    )
  }
  shared.sort()

  // Constellation extent: hub plus its rings. Tiling uses the widest one, so
  // neighbouring constellations never grow into each other.
  /**
   * A hub's rings. How many cards fit on a ring comes from that ring's actual
   * circumference, not a fixed count: a dozen dimensions crammed onto one orbit
   * is the "spacing cooked" complaint, and it is what the separation pass then
   * mashes into a blob.
   */
  const ringOf = (hub: string) => {
    const satellites = exclusive.get(hub) ?? []
    const tallest = Math.max(
      0,
      ...satellites.map((id) => size.get(id)?.h ?? 0),
      size.get(hub)?.h ?? 0,
    )
    const widest = Math.max(cardWidth, ...satellites.map((id) => size.get(id)?.w ?? 0))
    const rx = (size.get(hub)?.w ?? cardWidth) / 2 + widest / 2 + gap
    const ry = (size.get(hub)?.h ?? 0) / 2 + tallest / 2 + gap
    // Ramanujan's ellipse perimeter — close enough to count seats with
    const seats = (ring: number) => {
      const a = rx * ring
      const b = ry * ring
      const perimeter = Math.PI * (3 * (a + b) - Math.sqrt((3 * a + b) * (a + 3 * b)))
      return Math.max(3, Math.floor(perimeter / (widest + gap)))
    }
    const rings: string[][] = []
    let placed = 0
    while (placed < satellites.length) {
      const ring = rings.length + 1
      const take = Math.min(seats(ring), satellites.length - placed)
      rings.push(satellites.slice(placed, placed + take))
      placed += take
    }
    return { satellites, rings, rx, ry, tallest, widest }
  }

  const extents = hubs.map((hub) => {
    const { rings, rx, ry, tallest, widest } = ringOf(hub)
    const outer = Math.max(1, rings.length)
    return { hub, halfW: rx * outer + widest / 2, halfH: ry * outer + tallest / 2 }
  })
  const pitchX = Math.max(...extents.map((e) => e.halfW)) * 2 + componentGap
  const pitchY = Math.max(...extents.map((e) => e.halfH)) * 2 + componentGap
  const columns = Math.max(1, Math.ceil(Math.sqrt(hubs.length)))

  const boxes: Box[] = []
  const centre = new Map<string, ErdPosition>()
  hubs.forEach((hub, i) => {
    const cx = (i % columns) * pitchX
    const cy = Math.floor(i / columns) * pitchY
    centre.set(hub, { x: cx, y: cy })
    const box = boxOf(hub, 0.2)
    box.x = cx - box.w / 2
    box.y = cy - box.h / 2
    boxes.push(box)
  })

  for (const hub of hubs) {
    const { rings, rx, ry } = ringOf(hub)
    const hubCentre = centre.get(hub) as ErdPosition
    rings.forEach((occupants, index) => {
      const ring = index + 1
      occupants.forEach((id, slot) => {
        // Start to the RIGHT and go clockwise. Handles live on the left and
        // right edges of a card, so a dimension beside its fact gets a straight
        // short edge, while one directly above it gets a long detour — with a
        // single satellite that difference was 665px against 90px.
        const angle =
          (2 * Math.PI * slot) / occupants.length +
          (ring % 2 === 0 ? Math.PI / occupants.length : 0)
        const box = boxOf(id, 1)
        box.x = hubCentre.x + Math.cos(angle) * rx * ring - box.w / 2
        box.y = hubCentre.y + Math.sin(angle) * ry * ring - box.h / 2
        boxes.push(box)
      })
    })
  }

  for (const id of shared) {
    const touching = (owners.get(id) ?? []).filter((hub) => centre.has(hub))
    const box = boxOf(id, 1)
    const xs = touching.map((hub) => (centre.get(hub) as ErdPosition).x)
    const ys = touching.map((hub) => (centre.get(hub) as ErdPosition).y)
    box.x = xs.reduce((sum, x) => sum + x, 0) / Math.max(1, xs.length) - box.w / 2
    box.y = ys.reduce((sum, y) => sum + y, 0) / Math.max(1, ys.length) - box.h / 2
    boxes.push(box)
  }
  return boxes
}

/** The hub closest to `id` over relationship hops (breadth-first, ties by id). */
function nearestHub(
  id: string,
  adjacency: Map<string, Set<string>>,
  hubs: Set<string>,
): string | null {
  const seen = new Set([id])
  let frontier = [id]
  while (frontier.length > 0) {
    const next: string[] = []
    for (const current of frontier) {
      for (const other of [...(adjacency.get(current) ?? [])].sort()) {
        if (seen.has(other)) continue
        if (hubs.has(other)) return other
        seen.add(other)
        next.push(other)
      }
    }
    frontier = next
  }
  return null
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
