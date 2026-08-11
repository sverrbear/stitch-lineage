// The ERD layout's second objective (#101): edge CROSSINGS, which stress
// majorization does not model. Stress only knows distances, so it will happily
// settle two satellites into each other's seats when swapping them would uncross
// two relationships at no cost in length.
//
// So after the coordinates are found and the hard constraint is satisfied, a local
// search takes over: consider exchanging the positions of two cards, keep the
// exchange when it lowers `crossings·CROSSING_COST + totalLength/idealLength`, and
// repeat until a full pass finds no improvement. Bounded rounds and a fixed pair
// order make it deterministic.
//
// The reason this can run AFTER separation without putting the guarantee back at
// risk: an exchange is only considered between two cards of identical measured size.
// Swapping equal rectangles leaves the SET of occupied rectangles untouched, so
// every clearance in the drawing is exactly what it was — provably, not
// approximately. On a real scope most tables are the same collapsed size, so this
// is nearly all of them; an expanded table simply sits the search out.

import { segmentsCross } from './layoutMetrics'
import type { SeparationBox } from './layoutSeparation'

/** What one crossing is worth, in ideal edge lengths. */
export const CROSSING_COST = 3

export interface SwapEdge {
  from: string
  to: string
}

export interface SwapResult {
  rounds: number
  swaps: number
  before: { crossings: number; totalLength: number }
  after: { crossings: number; totalLength: number }
}

/**
 * Exchange equal-sized cards while it improves the objective. Mutates `boxes`.
 */
export function reduceCrossings(
  boxes: SeparationBox[],
  edges: readonly SwapEdge[],
  idealLength: number,
  rounds = 6,
): SwapResult {
  const order = [...boxes].sort((a, b) => a.id.localeCompare(b.id))
  const at = new Map(order.map((box, i) => [box.id, i]))
  const lines: Array<[number, number]> = []
  for (const edge of edges) {
    const a = at.get(edge.from)
    const b = at.get(edge.to)
    if (a === undefined || b === undefined || a === b) continue
    lines.push([a, b])
  }

  const score = () => objective(order, lines, idealLength)
  const before = tally(order, lines)
  let current = score()
  let swaps = 0
  let round = 0
  for (; round < rounds; round++) {
    let improved = false
    for (let i = 0; i < order.length; i++) {
      for (let j = i + 1; j < order.length; j++) {
        const a = order[i]
        const b = order[j]
        // equal rectangles only — this is what keeps the no-overlap guarantee exact
        if (a.w !== b.w || a.h !== b.h) continue
        exchange(a, b)
        const candidate = score()
        if (candidate < current - 1e-9) {
          current = candidate
          swaps += 1
          improved = true
        } else {
          exchange(a, b)
        }
      }
    }
    if (!improved) break
  }
  return { rounds: round, swaps, before, after: tally(order, lines) }
}

function exchange(a: SeparationBox, b: SeparationBox): void {
  const x = a.cx
  const y = a.cy
  a.cx = b.cx
  a.cy = b.cy
  b.cx = x
  b.cy = y
}

function tally(
  boxes: readonly SeparationBox[],
  lines: ReadonlyArray<[number, number]>,
): { crossings: number; totalLength: number } {
  let totalLength = 0
  for (const [a, b] of lines) {
    totalLength += Math.hypot(boxes[a].cx - boxes[b].cx, boxes[a].cy - boxes[b].cy)
  }
  let crossings = 0
  for (let i = 0; i < lines.length; i++) {
    for (let j = i + 1; j < lines.length; j++) {
      const [a1, a2] = lines[i]
      const [b1, b2] = lines[j]
      if (a1 === b1 || a1 === b2 || a2 === b1 || a2 === b2) continue
      if (
        segmentsCross(
          { x: boxes[a1].cx, y: boxes[a1].cy },
          { x: boxes[a2].cx, y: boxes[a2].cy },
          { x: boxes[b1].cx, y: boxes[b1].cy },
          { x: boxes[b2].cx, y: boxes[b2].cy },
        )
      ) {
        crossings += 1
      }
    }
  }
  return { crossings, totalLength }
}

function objective(
  boxes: readonly SeparationBox[],
  lines: ReadonlyArray<[number, number]>,
  idealLength: number,
): number {
  const { crossings, totalLength } = tally(boxes, lines)
  return crossings * CROSSING_COST + totalLength / Math.max(1, idealLength)
}
