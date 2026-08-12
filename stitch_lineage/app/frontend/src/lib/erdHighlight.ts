// Which relationship is being pointed at, which one was clicked, and the column
// rows those two light — held OUTSIDE React state (#186).
//
// Pointing at a relationship lights the two rows it joins, in their cards (#118).
// That was React state on the ERD page, and the page derives the whole canvas from
// its state: a `lit` set went into every card's `data`, and the hovered/picked id
// went into every edge's `className`. So moving the pointer onto ONE line rebuilt
// all 41 card objects and all 29 edge objects, React Flow re-adopted every node,
// and every card and every edge component re-rendered — measured at ~17ms of script
// per hover on/off cycle on the real graph, for two rows changing colour.
//
// A store fixes that at the root: hover state is not React state, so changing it
// re-renders nothing by itself. The rows and the lines subscribe to their OWN
// answer — `isLit(columnId)`, `edgeHighlight(edgeId)` — and React re-renders only
// the subscribers whose answer actually changed. Two rows and one line.
//
// Everything here is a plain value: no React, no React Flow, unit-testable.

/** A relationship the reader is pointing at or has clicked, and the rows it lights. */
export interface ErdEdgePick {
  id: string
  columns: string[]
}

/**
 * The class a relationship's own `<path>` carries.
 *
 * Interned rather than built per call, so a snapshot that has not changed is
 * `Object.is`-equal to the last one and `useSyncExternalStore` skips the render.
 * Building `` `${a} ${b}` `` here would hand every edge a fresh string and re-render
 * all of them — which is the bug this module exists to remove.
 */
export const EDGE_PLAIN = ''
export const EDGE_HOVERED = 'hovered'
export const EDGE_PICKED = 'picked'
export const EDGE_HOVERED_PICKED = 'hovered picked'

export type EdgeHighlight =
  | typeof EDGE_PLAIN
  | typeof EDGE_HOVERED
  | typeof EDGE_PICKED
  | typeof EDGE_HOVERED_PICKED

export interface ErdHighlight {
  /** Register for "something changed"; the returned function unregisters. */
  subscribe: (listener: () => void) => () => void
  /** Is this column one of the (at most four) rows currently lit? */
  isLit: (columnNodeId: string) => boolean
  /** What this relationship's path is drawn as, right now. */
  edgeHighlight: (edgeId: string) => EdgeHighlight
  /** The relationship under the pointer, and the one that was clicked. */
  hovered: () => ErdEdgePick | null
  picked: () => ErdEdgePick | null
  /** Point at a relationship. Pointing at the same one again changes nothing. */
  hover: (pick: ErdEdgePick) => void
  /** Nothing is being pointed at. */
  clearHover: () => void
  /** Click keeps a relationship lit; the same one again puts it out. */
  togglePicked: (pick: ErdEdgePick) => void
  clearPicked: () => void
  /** A new scope, or a new focus: nothing from the old canvas is lit on this one. */
  clear: () => void
}

const NOTHING_LIT: ReadonlySet<string> = new Set()

function sameEdge(a: ErdEdgePick | null, b: ErdEdgePick | null): boolean {
  return a === b || (!!a && !!b && a.id === b.id)
}

export function createErdHighlight(): ErdHighlight {
  const listeners = new Set<() => void>()
  let hovered: ErdEdgePick | null = null
  let picked: ErdEdgePick | null = null
  let lit: ReadonlySet<string> = NOTHING_LIT

  /**
   * Recompute what is lit and tell the subscribers. Called only when something
   * actually changed — a pointer moving across the background asks to clear the
   * hover on every single move, and none of those may wake the canvas.
   */
  const relight = (): void => {
    lit =
      hovered || picked
        ? new Set([...(picked?.columns ?? []), ...(hovered?.columns ?? [])])
        : NOTHING_LIT
    for (const listener of [...listeners]) listener()
  }

  return {
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    isLit: (columnNodeId) => lit.has(columnNodeId),
    edgeHighlight: (edgeId) => {
      const isHovered = hovered?.id === edgeId
      const isPicked = picked?.id === edgeId
      if (isHovered && isPicked) return EDGE_HOVERED_PICKED
      if (isHovered) return EDGE_HOVERED
      if (isPicked) return EDGE_PICKED
      return EDGE_PLAIN
    },
    hovered: () => hovered,
    picked: () => picked,
    hover: (pick) => {
      if (sameEdge(hovered, pick)) return
      hovered = pick
      relight()
    },
    clearHover: () => {
      if (!hovered) return
      hovered = null
      relight()
    },
    togglePicked: (pick) => {
      picked = sameEdge(picked, pick) ? null : pick
      relight()
    },
    clearPicked: () => {
      if (!picked) return
      picked = null
      relight()
    },
    clear: () => {
      if (!hovered && !picked) return
      hovered = null
      picked = null
      relight()
    },
  }
}
