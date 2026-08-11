// The ERD layout's HARD constraint (#101): no two cards overlap, and every pair
// clears a minimum gutter — using each card's real rendered size, so an expanded
// table claims the room it actually takes rather than the room a point would.
//
// Two stages, because "push things apart until it looks fine" is not a guarantee:
//
//   1. RELAXATION. Every penetrating pair is pushed apart along the axis it
//      penetrates least, half the distance each. This is the cheapest repair in
//      the least-displacement sense, so it preserves the shape the optimiser found.
//      It is what the old bounded pass did — and, like any relaxation, it can be
//      slow to settle and offers no termination bound.
//
//   2. CASCADE, which is where the guarantee comes from. Cards are taken in
//      reading order (top edge, then left edge, then id) and each is pushed DOWN
//      until it clears every card already fixed. A fixed card never moves again,
//      so once the last card is placed no pair can overlap: every pair was cleared
//      when its later member was placed, against a position that never changed.
//      It terminates because a card's y only ever increases and only ever to one of
//      finitely many "just below a fixed card" values.
//
// Stage 1 does the work, stage 2 makes it true. On a real scope stage 1 converges
// and stage 2 moves nothing, but the constraint does not depend on that.

import { TOLERANCE } from './layoutMetrics'

export interface SeparationBox {
  id: string
  /** Centre, because every constraint here is a centre-distance constraint. */
  cx: number
  cy: number
  w: number
  h: number
}

/**
 * How far two cards intrude on the space they owe each other, per axis. Both
 * positive means they overlap (or sit closer than the gutter); either at or below
 * zero means the pair is already satisfied, separated on that axis.
 */
function penetration(
  a: SeparationBox,
  b: SeparationBox,
  gutter: number,
): { x: number; y: number } {
  return {
    x: (a.w + b.w) / 2 + gutter - Math.abs(a.cx - b.cx),
    y: (a.h + b.h) / 2 + gutter - Math.abs(a.cy - b.cy),
  }
}

/** A pair is satisfied when it is clear on at least one axis. */
function satisfied(a: SeparationBox, b: SeparationBox, gutter: number): boolean {
  const p = penetration(a, b, gutter)
  return p.x <= TOLERANCE || p.y <= TOLERANCE
}

/**
 * One relaxation sweep over every pair, in the order given. Returns the worst
 * penetration it had to repair, so a caller can iterate until that is zero.
 *
 * `damping` below 1 makes the sweep a force rather than a correction, which is what
 * the stress iterations want: a card is nudged out of its neighbour while the
 * majorization still has a say in where it ends up.
 */
export function relaxOverlaps(
  boxes: readonly SeparationBox[],
  gutter: number,
  damping = 1,
): number {
  let worst = 0
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i]
      const b = boxes[j]
      const p = penetration(a, b, gutter)
      if (p.x <= TOLERANCE || p.y <= TOLERANCE) continue
      worst = Math.max(worst, Math.min(p.x, p.y))
      // the cheaper axis: moving 20px sideways beats moving 200px down
      if (p.x < p.y) {
        // a deterministic direction even when two cards share a centre line
        const push = ((p.x * damping) / 2) * (a.cx < b.cx || (a.cx === b.cx && a.id < b.id) ? -1 : 1)
        a.cx += push
        b.cx -= push
      } else {
        const push = ((p.y * damping) / 2) * (a.cy < b.cy || (a.cy === b.cy && a.id < b.id) ? -1 : 1)
        a.cy += push
        b.cy -= push
      }
    }
  }
  return worst
}

export interface SeparationResult {
  /** Relaxation sweeps spent before the pass was clean (or gave up). */
  sweeps: number
  /** Cards the cascade had to move — empty when relaxation was enough. */
  cascaded: string[]
}

/**
 * Guarantee the constraint. Afterwards every pair of boxes is clear on at least one
 * axis by at least `gutter` — which is exactly `clearance(a, b) >= gutter` for every
 * pair, and therefore zero overlaps.
 */
export function separateBoxes(
  boxes: SeparationBox[],
  gutter: number,
  sweepLimit = 400,
): SeparationResult {
  // one fixed visiting order, so the result cannot depend on input order
  const order = [...boxes].sort((a, b) => a.id.localeCompare(b.id))
  let sweeps = 0
  while (sweeps < sweepLimit) {
    sweeps += 1
    if (relaxOverlaps(order, gutter) <= TOLERANCE) break
  }
  return { sweeps, cascaded: cascade(boxes, gutter) }
}

/**
 * The finisher that makes the constraint true rather than likely. See the file
 * header for why it terminates and why the result is overlap-free.
 */
function cascade(boxes: readonly SeparationBox[], gutter: number): string[] {
  const order = [...boxes].sort(
    (a, b) => a.cy - a.h / 2 - (b.cy - b.h / 2) || a.cx - b.cx || a.id.localeCompare(b.id),
  )
  const fixed: SeparationBox[] = []
  const moved = new Set<string>()
  for (const box of order) {
    for (;;) {
      // the lowest bottom edge among the fixed cards this one still clashes with:
      // clearing that one clears all of them at once
      let below = -Infinity
      for (const other of fixed) {
        if (satisfied(box, other, gutter)) continue
        below = Math.max(below, other.cy + other.h / 2 + gutter + box.h / 2)
      }
      if (below === -Infinity) break
      box.cy = below
      moved.add(box.id)
    }
    fixed.push(box)
  }
  return [...moved].sort()
}
