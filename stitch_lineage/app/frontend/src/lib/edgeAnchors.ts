// Which side of each card a relationship attaches to (#100). Pure TS, no DOM /
// React Flow imports.
//
// The rule used to be fixed: an edge left its source card's RIGHT border and
// entered its target's LEFT one, whatever the two tables' actual positions. On a
// real scope two thirds of edge ends therefore anchored on a side facing AWAY
// from the table they were headed for, and the line had to come out of its own
// card, turn back, and sweep across the canvas to reach the mandated side.
//
// So each end picks its own side instead, from all four. Every one of the sixteen
// pairs is priced by what it would cost to draw — the Manhattan run between the
// two stub points, plus a fee for leaving a card backwards and for a direct run
// that a card already blocks — and the cheapest pair wins.
//
// Nothing on the line itself states which end is the FK any more: the `1`/`*` glyphs
// that used to were taken off the canvas as clutter (#110). Direction is read from the
// `col → col` chip an edge shows when you point at it, and from the relationship rows
// beside the canvas — on demand rather than always on, which is the trade that made.
//
// Two properties this has to keep:
//   * it feeds the router (lib/edgeRouting) rather than fighting it — a pair whose
//     stub-to-stub run is clear is exactly the pair the router draws straight, so
//     the cheap case stays cheap and the #79 invariant is untouched;
//   * the choice comes from card rectangles alone, so it re-runs when a card is
//     dragged, expanded or re-laid out, and it is deterministic — equal scores
//     fall back to a fixed order that keeps the classic right→left reading.

import {
  DEFAULT_MARGIN,
  DEFAULT_STUB,
  outwardOf,
  segmentHitsBox,
  stubPoint,
  type AnchorSide,
  type Point,
  type RoutingAnchor,
  type RoutingRect,
} from './edgeRouting'

/** One end of a relationship: the card it lands on, and the row it joins through. */
export interface AnchorEnd {
  rect: RoutingRect
  /**
   * Flow-space y of the joined column's row. A left or right anchor sits exactly
   * on it — the edge still points at its column — and a top or bottom anchor uses
   * it only to spread two relationships between the same pair of cards apart.
   */
  row: number
}

export interface AnchorPair {
  from: RoutingAnchor
  to: RoutingAnchor
}

export interface AnchorOptions {
  /** Straight run out of an anchor; must match what the router is given. */
  stub?: number
  /** Clearance the direct-run test keeps around an obstacle. */
  margin?: number
}

/**
 * Clearance the direct-run test keeps around a card. It IS the router's own, not a
 * number that resembles it: a smaller one here would price a pair as clear that the
 * router then has to detour around, and the whole point of the scoring is that the
 * pair it calls cheap is the pair the router draws straight.
 */
const DIRECT_RUN_MARGIN = DEFAULT_MARGIN

/** How far an anchor stays clear of its card's corners, so the stub has room to turn. */
const CORNER_INSET = 20

/** How far the joined row's position within a card shifts a top/bottom anchor. */
const ROW_SPREAD = 44

/**
 * Leaving a card on a side that faces away from the other end costs this much: it
 * is a U-turn, and a U-turn is the thing #100 is about. Priced above a couple of
 * corners so it never wins on a few pixels of length, below a card's width so a
 * genuinely much shorter route still can.
 */
const BACKWARDS_PRICE = 220

/** A direct run that a card blocks: the router will have to go around it. */
const BLOCKED_PRICE = 260

/**
 * Sides in the order they are tried. The first pair — the source's right to the
 * target's left — is the classic reading, so a tie leaves the diagram as it was.
 */
const FROM_ORDER: readonly AnchorSide[] = ['right', 'left', 'bottom', 'top']
const TO_ORDER: readonly AnchorSide[] = ['left', 'right', 'top', 'bottom']

function clamp(value: number, low: number, high: number): number {
  if (high < low) return (low + high) / 2
  return Math.min(high, Math.max(low, value))
}

function centreOf(rect: RoutingRect): Point {
  return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 }
}

/**
 * The point on `side` of the card. Left and right keep the joined row's y; top and
 * bottom aim at the other card and are nudged by where the row sits in this one.
 */
export function anchorOn(end: AnchorEnd, side: AnchorSide, towards: Point): RoutingAnchor {
  const { rect, row } = end
  if (side === 'left' || side === 'right') {
    return {
      x: side === 'left' ? rect.x : rect.x + rect.width,
      y: clamp(row, rect.y + CORNER_INSET, rect.y + rect.height - CORNER_INSET),
      side,
    }
  }
  const fraction = rect.height > 0 ? clamp((row - rect.y) / rect.height, 0, 1) : 0.5
  const spread = (fraction - 0.5) * ROW_SPREAD
  return {
    x: clamp(towards.x + spread, rect.x + CORNER_INSET, rect.x + rect.width - CORNER_INSET),
    y: side === 'top' ? rect.y : rect.y + rect.height,
    side,
  }
}

/** Does this anchor set off away from where it is going? */
function facesAway(anchor: RoutingAnchor, other: Point): boolean {
  const out = outwardOf(anchor.side)
  return out.x * (other.x - anchor.x) + out.y * (other.y - anchor.y) < 0
}

/**
 * Pick a side for each end of one relationship. `obstacles` are the other cards on
 * the canvas — the two the edge belongs to are read from `from`/`to` and count as
 * blockers too, so a side whose direct run cuts back through its own card loses.
 */
export function chooseAnchors(
  from: AnchorEnd,
  to: AnchorEnd,
  obstacles: readonly RoutingRect[] = [],
  options: AnchorOptions = {},
): AnchorPair {
  const stub = options.stub ?? DEFAULT_STUB
  const margin = options.margin ?? DIRECT_RUN_MARGIN
  const toCentre = centreOf(to.rect)
  const fromCentre = centreOf(from.rect)
  const walls = [...obstacles, from.rect, to.rect].map((rect) => ({
    left: rect.x - margin,
    right: rect.x + rect.width + margin,
    top: rect.y - margin,
    bottom: rect.y + rect.height + margin,
  }))

  // the four candidates per end, worked out once
  const fromEnds = FROM_ORDER.map((side) => {
    const anchor = anchorOn(from, side, toCentre)
    return { anchor, stub: stubPoint(anchor, stub) }
  })
  const toEnds = TO_ORDER.map((side) => {
    const anchor = anchorOn(to, side, fromCentre)
    return { anchor, stub: stubPoint(anchor, stub) }
  })

  let best: AnchorPair = { from: fromEnds[0].anchor, to: toEnds[0].anchor }
  let bestScore = Infinity
  for (const a of fromEnds) {
    for (const b of toEnds) {
      let score = Math.abs(a.stub.x - b.stub.x) + Math.abs(a.stub.y - b.stub.y)
      if (facesAway(a.anchor, b.anchor)) score += BACKWARDS_PRICE
      if (facesAway(b.anchor, a.anchor)) score += BACKWARDS_PRICE
      // Already beaten, and the only term left can only make it worse: skip the
      // segment tests. On a scope of a hundred cards this is most of the work.
      if (score >= bestScore) continue
      if (walls.some((box) => segmentHitsBox(a.stub, b.stub, box))) score += BLOCKED_PRICE
      // strictly better only: ties keep the earlier, more conventional pair
      if (score < bestScore) {
        bestScore = score
        best = { from: a.anchor, to: b.anchor }
      }
    }
  }
  return best
}
