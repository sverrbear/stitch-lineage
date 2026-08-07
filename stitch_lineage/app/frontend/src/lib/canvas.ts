// Click-vs-drag disambiguation shared by the two React Flow canvases. Pure TS.

export interface Point {
  x: number
  y: number
}

/**
 * Pointer travel (px) still counted as a click rather than a drag.
 *
 * React Flow drags nodes from anywhere on their body, and its `nodeClickDistance`
 * defaults to 0 — d3-drag then swallows the click after a *single* pixel of hand
 * jitter, so node clicks silently do nothing. Both canvases pass this as
 * `nodeClickDistance` and re-check it on their own handlers.
 */
export const CLICK_SLOP_PX = 4

/** A missing press origin (keyboard-triggered click) counts as a click. */
export function isClickNotDrag(from: Point | null, to: Point, slop: number = CLICK_SLOP_PX): boolean {
  if (!from) return true
  return Math.hypot(to.x - from.x, to.y - from.y) <= slop
}
