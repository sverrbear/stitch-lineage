// Obstacle-avoiding ERD edge routing (#79). Pure TS, no DOM / React Flow imports.
//
// A relationship that disappears under a table card and comes out the other side
// is untraceable, which is what the star layout still shipped: React Flow's
// smoothstep runs handle to handle and does not know cards exist.
//
// So the path is planned instead of drawn. Every card becomes a rectangle
// inflated by a clearance margin; the corridors left between them are the only
// place a line may go. The candidate turning points are the corridor walls
// themselves (a Hanan grid: every inflated card edge, plus the two anchors) —
// a few dozen lines rather than a pixel raster — and A* over that grid with a
// per-corner penalty gives the shortest route that bends as little as possible.
//
// Three properties matter as much as the avoidance itself:
//   * an unobstructed relationship stays a straight run — planning must not turn
//     neighbouring cards into detours, so the clear case never reaches the grid;
//   * an edge should not cross its OWN two cards either (a satellite placed left
//     of its hub used to double back through both), so those are `soft`: passable
//     at a stiff price, which routes around them whenever there is a way around;
//   * the search is bounded (region, grid size, expansions) and deterministic, so
//     a dense scope cannot stall the canvas and the same input always draws the
//     same line.

export interface Point {
  x: number
  y: number
}

/** A card the route must stay out of, in flow coordinates. */
export interface RoutingRect {
  id?: string
  x: number
  y: number
  width: number
  height: number
}

/** Which edge of its card a handle sits on — the route leaves along that axis. */
export type AnchorSide = 'left' | 'right'

export interface RoutingAnchor extends Point {
  side: AnchorSide
}

export interface RouteOptions {
  /** Clearance kept around every card. */
  margin?: number
  /** Straight run out of a handle before the route may turn; kept past `margin`. */
  stub?: number
  /** Cost of a corner in px-equivalents, so few bends beats slightly shorter. */
  turnPenalty?: number
  /** How far outside the two anchors' box the route may wander to find a way through. */
  detour?: number
  /**
   * The edge's own two cards. Crossing one is allowed but expensive, so a route
   * that can go around them does — and one with nowhere else to go still exists.
   */
  soft?: readonly RoutingRect[]
  /** Guard rails: give up (and fall back) rather than stall a dense canvas. */
  maxGridLines?: number
  maxExpansions?: number
}

const DEFAULTS = {
  margin: 12,
  stub: 18,
  turnPenalty: 34,
  detour: 280,
  maxGridLines: 56,
  maxExpansions: 40000,
} as const

/** What crossing one of the edge's own cards costs: 4x the distance, plus a fee. */
const SOFT_MULTIPLIER = 4
const SOFT_FEE = 240

/** Touching a corridor wall is legal; only entering a card is not. */
const SKIN = 1e-6

interface Box {
  left: number
  right: number
  top: number
  bottom: number
}

function inflate(rect: RoutingRect, margin: number): Box {
  return {
    left: rect.x - margin,
    right: rect.x + rect.width + margin,
    top: rect.y - margin,
    bottom: rect.y + rect.height + margin,
  }
}

/**
 * The card as a wall, with as much of `margin` as the route can afford: a card
 * 25px from the one an edge leaves would otherwise swallow that edge's own start
 * point in its clearance ring, and a wall you are already standing inside has to
 * be dropped — which is how a line ended up straight through the neighbour. So
 * the clearance shrinks to whatever keeps the anchors outside instead, and the
 * card itself stays off limits. `null` when even that is impossible.
 */
function wallOf(rect: RoutingRect, margin: number, anchors: readonly Point[]): Box | null {
  let allowed = margin
  for (const point of anchors) {
    const gap = Math.max(
      rect.x - point.x,
      point.x - (rect.x + rect.width),
      rect.y - point.y,
      point.y - (rect.y + rect.height),
    )
    if (gap <= 0) return null
    allowed = Math.min(allowed, gap)
  }
  return inflate(rect, Math.max(0, allowed))
}

/** Liang–Barsky: does the segment reach the inside of the box? */
export function segmentHitsBox(a: Point, b: Point, box: Box): boolean {
  const left = box.left + SKIN
  const right = box.right - SKIN
  const top = box.top + SKIN
  const bottom = box.bottom - SKIN
  if (left >= right || top >= bottom) return false
  const dx = b.x - a.x
  const dy = b.y - a.y
  let enter = 0
  let leave = 1
  const clip = (p: number, q: number): boolean => {
    if (p === 0) return q >= 0
    const t = q / p
    if (p < 0) {
      if (t > leave) return false
      if (t > enter) enter = t
    } else {
      if (t < enter) return false
      if (t < leave) leave = t
    }
    return true
  }
  return (
    clip(-dx, a.x - left) && clip(dx, right - a.x) && clip(-dy, a.y - top) && clip(dy, bottom - a.y)
  )
}

/**
 * The cards a polyline passes through, ignoring `exempt` (its own endpoints).
 * This is the acceptance gate in code: it must come back empty for every edge.
 */
export function pathHitsRects(
  points: readonly Point[],
  rects: readonly RoutingRect[],
  options: { margin?: number; exempt?: readonly string[] } = {},
): string[] {
  const margin = options.margin ?? 0
  const exempt = new Set(options.exempt ?? [])
  const hit: string[] = []
  rects.forEach((rect, index) => {
    if (rect.id !== undefined && exempt.has(rect.id)) return
    const box = inflate(rect, margin)
    for (let i = 0; i < points.length - 1; i++) {
      if (segmentHitsBox(points[i], points[i + 1], box)) {
        hit.push(rect.id ?? String(index))
        return
      }
    }
  })
  return hit
}

export function polylineLength(points: readonly Point[]): number {
  let total = 0
  for (let i = 0; i < points.length - 1; i++) {
    total += Math.hypot(points[i + 1].x - points[i].x, points[i + 1].y - points[i].y)
  }
  return total
}

/** The point half way along a polyline — where an edge label belongs. */
export function polylineMidpoint(points: readonly Point[]): Point {
  if (points.length === 0) return { x: 0, y: 0 }
  const half = polylineLength(points) / 2
  let walked = 0
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i]
    const b = points[i + 1]
    const length = Math.hypot(b.x - a.x, b.y - a.y)
    if (walked + length >= half && length > 0) {
      const t = (half - walked) / length
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
    }
    walked += length
  }
  return points[points.length - 1]
}

/** Drop repeated and collinear points, so the drawn path has only real corners. */
export function simplify(points: readonly Point[]): Point[] {
  const out: Point[] = []
  for (const point of points) {
    const last = out[out.length - 1]
    if (last && Math.abs(last.x - point.x) < 0.01 && Math.abs(last.y - point.y) < 0.01) continue
    out.push({ x: point.x, y: point.y })
  }
  for (let i = 1; i < out.length - 1; ) {
    const a = out[i - 1]
    const b = out[i]
    const c = out[i + 1]
    const cross = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
    if (Math.abs(cross) < 0.01) out.splice(i, 1)
    else i++
  }
  return out
}

/** An SVG `d` with the corners rounded — a planned path still has to look drawn. */
export function roundedPath(points: readonly Point[], radius = 8): string {
  const path = simplify(points)
  if (path.length === 0) return ''
  if (path.length === 1) return `M ${path[0].x},${path[0].y}`
  let d = `M ${path[0].x},${path[0].y}`
  for (let i = 1; i < path.length - 1; i++) {
    const previous = path[i - 1]
    const corner = path[i]
    const next = path[i + 1]
    const inLength = Math.hypot(corner.x - previous.x, corner.y - previous.y)
    const outLength = Math.hypot(next.x - corner.x, next.y - corner.y)
    const cut = Math.min(radius, inLength / 2, outLength / 2)
    if (cut < 0.5) {
      d += ` L ${corner.x},${corner.y}`
      continue
    }
    const before = {
      x: corner.x + ((previous.x - corner.x) / inLength) * cut,
      y: corner.y + ((previous.y - corner.y) / inLength) * cut,
    }
    const after = {
      x: corner.x + ((next.x - corner.x) / outLength) * cut,
      y: corner.y + ((next.y - corner.y) / outLength) * cut,
    }
    d += ` L ${before.x},${before.y} Q ${corner.x},${corner.y} ${after.x},${after.y}`
  }
  const last = path[path.length - 1]
  return `${d} L ${last.x},${last.y}`
}

/** Grid lines: inside the region, sorted, deduped to half a pixel, count-capped. */
function lines(values: number[], low: number, high: number, cap: number): number[] {
  const inside = values.filter((value) => value >= low - 0.5 && value <= high + 0.5)
  inside.push(low, high)
  inside.sort((a, b) => a - b)
  const kept: number[] = []
  for (const value of inside) {
    if (kept.length === 0 || value - kept[kept.length - 1] > 0.5) kept.push(value)
  }
  if (kept.length <= cap) return kept
  // Too many corridors to search: thin them out evenly, keeping both ends. The
  // route gets coarser, never wrong — passability is still tested exactly.
  const step = (kept.length - 1) / (cap - 1)
  const thinned: number[] = []
  for (let i = 0; i < cap; i++) thinned.push(kept[Math.round(i * step)])
  return thinned.filter((value, i) => i === 0 || value - thinned[i - 1] > 0.5)
}

function nearestIndex(values: readonly number[], target: number): number {
  let best = 0
  let bestGap = Infinity
  for (let i = 0; i < values.length; i++) {
    const gap = Math.abs(values[i] - target)
    if (gap < bestGap) {
      bestGap = gap
      best = i
    }
  }
  return best
}

/** A tiny binary heap — A* over a grid does not need more. */
class Heap {
  private items: Array<{ key: number; state: number }> = []

  push(key: number, state: number): void {
    const items = this.items
    items.push({ key, state })
    let i = items.length - 1
    while (i > 0) {
      const parent = (i - 1) >> 1
      if (items[parent].key <= items[i].key) break
      ;[items[parent], items[i]] = [items[i], items[parent]]
      i = parent
    }
  }

  pop(): { key: number; state: number } | undefined {
    const items = this.items
    const top = items[0]
    const last = items.pop()
    if (items.length > 0 && last) {
      items[0] = last
      let i = 0
      for (;;) {
        const left = i * 2 + 1
        const right = left + 1
        let small = i
        if (left < items.length && items[left].key < items[small].key) small = left
        if (right < items.length && items[right].key < items[small].key) small = right
        if (small === i) break
        ;[items[small], items[i]] = [items[i], items[small]]
        i = small
      }
    }
    return top
  }

  get size(): number {
    return this.items.length
  }
}

const HORIZONTAL = 0
const VERTICAL = 1
const UNSET = 2

function stubPoint(anchor: RoutingAnchor, stub: number): Point {
  return { x: anchor.x + (anchor.side === 'right' ? stub : -stub), y: anchor.y }
}

/**
 * Route one relationship. `obstacles` are the cards the edge must not enter —
 * every card in the scope except its own two, which belong in `soft`.
 *
 * Returns the polyline in flow coordinates, both anchors included.
 */
export function routeEdge(
  from: RoutingAnchor,
  to: RoutingAnchor,
  obstacles: readonly RoutingRect[],
  options: RouteOptions = {},
): Point[] {
  const { margin, turnPenalty, detour, maxGridLines, maxExpansions } = { ...DEFAULTS, ...options }
  // the stub has to clear the card's own margin, or the route starts inside a wall
  const stub = Math.max(options.stub ?? DEFAULTS.stub, margin + 4)
  const start = stubPoint(from, stub)
  const end = stubPoint(to, stub)

  // Both walls and own-cards are measured from the two stub points: the handles
  // themselves sit ON their cards, so judging by those would discard every card.
  const anchors = [start, end]
  const walls = (rects: readonly RoutingRect[]) =>
    rects
      .map((rect) => wallOf(rect, margin, anchors))
      .filter((box): box is Box => box !== null)
  const hard = walls(obstacles)
  const soft = walls(options.soft ?? [])

  const blocked = (a: Point, b: Point, boxes: readonly Box[]): boolean => {
    for (const box of boxes) if (segmentHitsBox(a, b, box)) return true
    return false
  }

  // The straight run first: a dimension beside its fact must not be planned into
  // a detour, and this is also what keeps a big scope cheap to draw.
  if (!blocked(start, end, hard) && !blocked(start, end, soft)) {
    return simplify([from, start, end, to])
  }

  for (const reach of [detour, detour * 3, detour * 9]) {
    const region = {
      left: Math.min(start.x, end.x) - reach,
      right: Math.max(start.x, end.x) + reach,
      top: Math.min(start.y, end.y) - reach,
      bottom: Math.max(start.y, end.y) + reach,
    }
    const inRegion = (box: Box) =>
      box.right > region.left &&
      box.left < region.right &&
      box.bottom > region.top &&
      box.top < region.bottom
    // Confining the search to the region makes every card outside it irrelevant,
    // which is what keeps the grid small on a hundred-table scope.
    const walls = hard.filter(inRegion)
    const own = soft.filter(inRegion)
    const edges = [...walls, ...own]
    const xs = lines(
      [start.x, end.x, ...edges.flatMap((box) => [box.left, box.right])],
      region.left,
      region.right,
      maxGridLines,
    )
    const ys = lines(
      [start.y, end.y, ...edges.flatMap((box) => [box.top, box.bottom])],
      region.top,
      region.bottom,
      maxGridLines,
    )
    const path = search(xs, ys, start, end, from.side, to.side, walls, own, {
      turnPenalty,
      maxExpansions,
    })
    if (path) return simplify([from, start, ...path, end, to])
  }

  // Nothing got through: draw the old orthogonal step rather than no edge at all.
  const middle = (start.x + end.x) / 2
  return simplify([from, start, { x: middle, y: start.y }, { x: middle, y: end.y }, end, to])
}

function search(
  xs: readonly number[],
  ys: readonly number[],
  start: Point,
  end: Point,
  fromSide: AnchorSide,
  toSide: AnchorSide,
  walls: readonly Box[],
  soft: readonly Box[],
  limits: { turnPenalty: number; maxExpansions: number },
): Point[] | null {
  const width = xs.length
  const height = ys.length
  if (width < 2 || height < 2) return null
  const startX = nearestIndex(xs, start.x)
  const startY = nearestIndex(ys, start.y)
  const goalX = nearestIndex(xs, end.x)
  const goalY = nearestIndex(ys, end.y)
  const cell = (ix: number, iy: number) => iy * width + ix
  const stateOf = (ix: number, iy: number, dir: number) => cell(ix, iy) * 3 + dir
  const goal = cell(goalX, goalY)
  const point = (ix: number, iy: number): Point => ({ x: xs[ix], y: ys[iy] })
  const heuristic = (ix: number, iy: number) =>
    Math.abs(xs[ix] - xs[goalX]) + Math.abs(ys[iy] - ys[goalY])

  // Segment cost, cached per grid segment: Infinity through a card, dear through
  // one of the edge's own two, plain distance in a corridor.
  const priced = new Map<number, number>()
  const price = (a: Point, b: Point, key: number): number => {
    const known = priced.get(key)
    if (known !== undefined) return known
    const span = Math.abs(b.x - a.x) + Math.abs(b.y - a.y)
    let cost = span
    for (const box of walls) {
      if (segmentHitsBox(a, b, box)) {
        cost = Infinity
        break
      }
    }
    if (cost !== Infinity) {
      for (const box of soft) {
        if (segmentHitsBox(a, b, box)) {
          cost = span * SOFT_MULTIPLIER + SOFT_FEE
          break
        }
      }
    }
    priced.set(key, cost)
    return cost
  }

  const best = new Map<number, number>()
  const cameFrom = new Map<number, number>()
  const heap = new Heap()
  const startState = stateOf(startX, startY, UNSET)
  best.set(startState, 0)
  heap.push(heuristic(startX, startY), startState)

  let expansions = 0
  let found: number | null = null
  const seen = new Set<number>()

  while (heap.size > 0) {
    const top = heap.pop()
    if (!top) break
    const state = top.state
    if (seen.has(state)) continue
    seen.add(state)
    const cost = best.get(state)
    if (cost === undefined) continue
    const dir = state % 3
    const index = (state - dir) / 3
    const iy = Math.floor(index / width)
    const ix = index - iy * width
    if (index === goal) {
      found = state
      break
    }
    if (++expansions > limits.maxExpansions) return null

    const here = point(ix, iy)
    for (const [dx, dy] of [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ] as const) {
      const nx = ix + dx
      const ny = iy + dy
      if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue
      const moveDir = dx === 0 ? VERTICAL : HORIZONTAL
      // The stub already committed to leaving sideways: turning straight back
      // would run the line through its own card.
      if (dir === UNSET && moveDir === HORIZONTAL) {
        if (fromSide === 'right' && dx < 0) continue
        if (fromSide === 'left' && dx > 0) continue
      }
      // ...and the far end has to be approached from the side its handle faces.
      if (cell(nx, ny) === goal && moveDir === HORIZONTAL) {
        if (toSide === 'left' && dx < 0) continue
        if (toSide === 'right' && dx > 0) continue
      }
      const next = point(nx, ny)
      const key =
        moveDir === HORIZONTAL
          ? cell(Math.min(ix, nx), iy) * 2
          : cell(ix, Math.min(iy, ny)) * 2 + 1
      const step = price(here, next, key)
      if (step === Infinity) continue
      const turn = dir !== UNSET && dir !== moveDir ? limits.turnPenalty : 0
      const nextState = stateOf(nx, ny, moveDir)
      const candidate = cost + step + turn
      const known = best.get(nextState)
      if (known !== undefined && known <= candidate) continue
      best.set(nextState, candidate)
      cameFrom.set(nextState, state)
      heap.push(candidate + heuristic(nx, ny), nextState)
    }
  }

  if (found === null) return null
  const points: Point[] = []
  let state: number | undefined = found
  while (state !== undefined) {
    const dir = state % 3
    const index = (state - dir) / 3
    const iy = Math.floor(index / width)
    points.push(point(index - iy * width, iy))
    state = cameFrom.get(state)
  }
  return points.reverse()
}
