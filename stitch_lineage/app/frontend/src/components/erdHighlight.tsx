// React's view of the ERD highlight store (lib/erdHighlight) — #186.
//
// Lives beside the components rather than in lib/ for the same reason
// `useCardRects` does: it is React, and lib/ stays plain TS so it can be tested
// without a renderer.
//
// The point of every hook here is the SUBSCRIBER, not the value. A column row asks
// only "am I lit", a relationship asks only "how am I drawn" — both plain values —
// so `useSyncExternalStore` re-renders a row or a line only when ITS answer changed.
// Hovering a relationship therefore re-renders two rows and one line, and leaves the
// other thirty-nine cards and twenty-eight relationships untouched.

import { createContext, useContext, useMemo, useSyncExternalStore, type ReactNode } from 'react'
import { createErdHighlight, type EdgeHighlight, type ErdHighlight } from '../lib/erdHighlight'

/**
 * The fallback is a real (permanently empty) store rather than null, so the hooks
 * stay unconditional and a component rendered outside the canvas — the lineage
 * page's model star shares this edge component — simply never lights up.
 */
const ErdHighlightContext = createContext<ErdHighlight>(createErdHighlight())

export function ErdHighlightProvider({
  highlight,
  children,
}: {
  highlight: ErdHighlight
  children: ReactNode
}) {
  return <ErdHighlightContext.Provider value={highlight}>{children}</ErdHighlightContext.Provider>
}

/** One store per mounted canvas, kept for its lifetime. */
export function useErdHighlight(): ErdHighlight {
  return useMemo(() => createErdHighlight(), [])
}

/** Is this column one of the rows the lit relationship joins? */
export function useLitColumn(columnNodeId: string): boolean {
  const highlight = useContext(ErdHighlightContext)
  return useSyncExternalStore(highlight.subscribe, () => highlight.isLit(columnNodeId))
}

/** How this relationship's own path is drawn: plain, hovered, picked, or both. */
export function useEdgeHighlight(edgeId: string): EdgeHighlight {
  const highlight = useContext(ErdHighlightContext)
  return useSyncExternalStore(highlight.subscribe, () => highlight.edgeHighlight(edgeId))
}
