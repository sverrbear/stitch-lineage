// The geometry an ERD layout is JUDGED by (#101).
//
// Kept out of the layout itself on purpose: the numbers quoted for a layout change
// and the numbers the suite asserts should be the SAME numbers, computed by code
// that knows nothing about how the placement was reached. Two hard constraints —
// no two cards overlap, every pair clears a minimum gutter — and two objectives:
// total edge length and edge crossings.
//
// Pure functions, no DOM.

export interface MetricRect {
  id: string
  /** Top-left, matching what `layoutErd` returns. */
  x: number
  y: number
  w: number
  h: number
}

export interface MetricEdge {
  from: string
  to: string
}

export interface Point {
  x: number
  y: number
}

export function centreOf(rect: MetricRect): Point {
  return { x: rect.x + rect.w / 2, y: rect.y + rect.h / 2 }
}

/** Area two cards share; 0 when they merely touch along an edge. */
export function overlapArea(a: MetricRect, b: MetricRect): number {
  const x = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
  const y = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
  return x > 0 && y > 0 ? x * y : 0
}

/**
 * Shortest distance between two cards: 0 when they overlap, the axis gap when they
 * face each other, the corner distance when they sit diagonally. This is the
 * "gutter" the layout guarantees a minimum of.
 */
export function clearance(a: MetricRect, b: MetricRect): number {
  const x = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
  const y = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
  if (x > 0 && y > 0) return 0
  if (x > 0) return -y
  if (y > 0) return -x
  return Math.hypot(x, y)
}

/**
 * Do the open segments a→b and c→d cross? Endpoint contact and collinear overlap
 * are NOT crossings: two relationships that meet at a shared table are drawn to the
 * same card and the reader never reads them as crossing.
 */
export function segmentsCross(a: Point, b: Point, c: Point, d: Point): boolean {
  const side = (p: Point, q: Point, r: Point) =>
    (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)
  const d1 = side(c, d, a)
  const d2 = side(c, d, b)
  const d3 = side(a, b, c)
  const d4 = side(a, b, d)
  return d1 > 0 !== d2 > 0 && d3 > 0 !== d4 > 0
}

export interface LayoutMeasurement {
  cards: number
  edges: number
  /** Pairs of cards whose rectangles intersect. The layout guarantees 0. */
  overlaps: number
  worstOverlapArea: number
  /** Smallest clearance between any two cards; the layout guarantees `>= gutter`. */
  minClearance: number
  /** Pairs closer than the gutter. The layout guarantees 0. */
  pairsUnderGutter: number
  /** Sum of centre-to-centre relationship lengths — the objective to minimise. */
  totalEdgeLength: number
  meanEdgeLength: number
  longestEdge: number
  /** Relationship lines that cross without sharing a table — the other objective. */
  crossings: number
  width: number
  height: number
}

/**
 * Everything above in one pass over the pairs. O(cards²) and O(edges²), which is
 * why it lives here and not in the render path: scopes are hundreds of cards, so
 * this is a measurement and test tool, not something the canvas runs per frame.
 */
export function measureLayout(
  rects: readonly MetricRect[],
  edges: readonly MetricEdge[],
  gutter: number,
): LayoutMeasurement {
  let overlaps = 0
  let worstOverlapArea = 0
  let minClearance = Infinity
  let pairsUnderGutter = 0
  for (let i = 0; i < rects.length; i++) {
    for (let j = i + 1; j < rects.length; j++) {
      const area = overlapArea(rects[i], rects[j])
      if (area > TOLERANCE) {
        overlaps += 1
        worstOverlapArea = Math.max(worstOverlapArea, area)
      }
      const gap = clearance(rects[i], rects[j])
      minClearance = Math.min(minClearance, gap)
      if (gap < gutter - TOLERANCE) pairsUnderGutter += 1
    }
  }

  const centre = new Map(rects.map((rect) => [rect.id, centreOf(rect)]))
  const lines: Array<{ a: Point; b: Point; from: string; to: string }> = []
  for (const edge of edges) {
    const a = centre.get(edge.from)
    const b = centre.get(edge.to)
    if (!a || !b || edge.from === edge.to) continue
    lines.push({ a, b, from: edge.from, to: edge.to })
  }
  let totalEdgeLength = 0
  let longestEdge = 0
  for (const line of lines) {
    const length = Math.hypot(line.b.x - line.a.x, line.b.y - line.a.y)
    totalEdgeLength += length
    longestEdge = Math.max(longestEdge, length)
  }
  let crossings = 0
  for (let i = 0; i < lines.length; i++) {
    for (let j = i + 1; j < lines.length; j++) {
      const one = lines[i]
      const other = lines[j]
      if (
        one.from === other.from ||
        one.from === other.to ||
        one.to === other.from ||
        one.to === other.to
      ) {
        continue
      }
      if (segmentsCross(one.a, one.b, other.a, other.b)) crossings += 1
    }
  }

  return {
    cards: rects.length,
    edges: lines.length,
    overlaps,
    worstOverlapArea,
    minClearance: minClearance === Infinity ? 0 : minClearance,
    pairsUnderGutter,
    totalEdgeLength,
    meanEdgeLength: lines.length === 0 ? 0 : totalEdgeLength / lines.length,
    longestEdge,
    crossings,
    width: rects.length === 0 ? 0 : span(rects, (r) => [r.x, r.x + r.w]),
    height: rects.length === 0 ? 0 : span(rects, (r) => [r.y, r.y + r.h]),
  }
}

function span(rects: readonly MetricRect[], extent: (r: MetricRect) => [number, number]): number {
  let low = Infinity
  let high = -Infinity
  for (const rect of rects) {
    const [a, b] = extent(rect)
    low = Math.min(low, a)
    high = Math.max(high, b)
  }
  return high - low
}

/** Floating-point slack: below this an overlap is a rounding artefact, not an overlap. */
export const TOLERANCE = 1e-6
