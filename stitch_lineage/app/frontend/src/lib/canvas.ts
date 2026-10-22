// Click-vs-drag disambiguation for the lineage canvas. Pure TS.

export interface Point {
  x: number
  y: number
}

/**
 * Pointer travel (px) still counted as a click rather than a drag.
 *
 * React Flow drags nodes from anywhere on their body, and its `nodeClickDistance`
 * defaults to 0 — d3-drag then swallows the click after a *single* pixel of hand
 * jitter, so node clicks silently do nothing. The canvas passes this as
 * `nodeClickDistance` and re-check it on their own handlers.
 */
export const CLICK_SLOP_PX = 4

/** A missing press origin (keyboard-triggered click) counts as a click. */
export function isClickNotDrag(from: Point | null, to: Point, slop: number = CLICK_SLOP_PX): boolean {
  if (!from) return true
  return Math.hypot(to.x - from.x, to.y - from.y) <= slop
}

/** The shape of a React Flow node this module needs to reason about. */
export interface CanvasNode {
  id: string
  position: Point
  /** React Flow's own measurement bookkeeping — the reason identity matters. */
  measured?: { width?: number; height?: number }
  /** Set by React Flow for the duration of a drag. */
  dragging?: boolean
}

/**
 * True when React Flow is rendering exactly `target` — every card measured and at the
 * coordinate the layout gave it (#185).
 *
 * This is the precondition for fitting the viewport, and it is a fact about the canvas
 * rather than an amount of time to wait. React Flow computes the bounds it fits from
 * each card's `measured` box, so a fit fired before it has measured them frames a
 * degenerate box; and a fit fired while the cards still sit at the PREVIOUS
 * arrangement's coordinates frames the previous arrangement. Wait for measured
 * nodes at their target positions rather than fitting on a fixed timer.
 */
export function canvasSettled<T extends CanvasNode>(
  rendered: readonly T[],
  target: readonly T[],
): boolean {
  // an empty canvas has no arrangement to frame
  if (target.length === 0 || rendered.length !== target.length) return false
  const byId = new Map(rendered.map((node) => [node.id, node]))
  return target.every((want) => {
    const node = byId.get(want.id)
    if (!node?.measured?.width || !node.measured.height) return false
    return node.position.x === want.position.x && node.position.y === want.position.y
  })
}
