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
 * Fold a freshly derived node array into the one React Flow is already rendering,
 * keeping each card's existing object (#175).
 *
 * The canvas kept a rebuilt array wholesale — `setNodes(baseNodes)` — so anything
 * that recomputed the array handed React Flow 41 brand-new objects. Dropping a card
 * one pixel rebuilds the array (the reader's manual positions are an input to it),
 * which means a position nudge threw away every card's identity along with the
 * `measured` bounds React Flow keeps on it. Nothing on screen needed to change but
 * the one card that moved.
 *
 * So: membership and order come from `next`, which is the source of truth for which
 * cards exist; but a card that exists in both keeps its CURRENT object, with only
 * `position` and `data` written onto it. React Flow's bookkeeping survives, and the
 * array is never emptied and refilled underneath it.
 *
 * Two things this must not break:
 *
 *   * a card whose SIZE changed (expanding a table) has to be handed over fresh, so
 *     React Flow re-measures it. Carrying a stale `measured` there makes it skip the
 *     measurement, and handle bounds go with it — which silently drops every edge on
 *     that card. `resized` is how the caller says which change that is; it is a
 *     required argument because getting it wrong is invisible until an edge vanishes.
 *   * a card mid-drag keeps the position React Flow is giving it. During a drag React
 *     Flow owns the position, and a rebuild that overwrote it would snap the card out
 *     from under the pointer.
 */
export function mergeCanvasNodes<T extends CanvasNode>(
  current: readonly T[],
  next: readonly T[],
  resized: (previous: T, incoming: T) => boolean,
): T[] {
  if (current.length === 0) return [...next]
  const byId = new Map(current.map((node) => [node.id, node]))
  return next.map((incoming) => {
    const previous = byId.get(incoming.id)
    if (!previous) return incoming
    if (resized(previous, incoming)) return incoming
    return {
      ...previous,
      ...incoming,
      // React Flow's, not ours: it belongs to the element, which has not changed
      measured: previous.measured,
      position: previous.dragging ? previous.position : incoming.position,
    }
  })
}
