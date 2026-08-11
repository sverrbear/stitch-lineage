// Every card on a React Flow canvas, in flow coordinates — the obstacle list the
// edge routers work against (#79/#146, and #176 for the lineage view).
//
// Lives beside the components rather than in lib/ because it reads React Flow's
// store; lib/ stays free of React Flow imports so it can be unit-tested.

import { useStore } from '@xyflow/react'
import type { RoutingRect } from '../lib/edgeRouting'

/**
 * Positions are quantised to 4px so a drag re-routes on real movement rather than on
 * every sub-pixel frame, and the comparator keeps the array identity stable while
 * nothing has actually moved — a router that re-ran every frame would be the whole
 * canvas's frame budget.
 *
 * A card React Flow has not measured yet is left out: its size is not known, and
 * guessing one would route edges around a rectangle that is not there.
 */
export function useCardRects(): RoutingRect[] {
  return useStore(
    (state) => {
      const rects: RoutingRect[] = []
      for (const [id, node] of state.nodeLookup) {
        const width = node.measured?.width
        const height = node.measured?.height
        if (!width || !height) continue
        const { x, y } = node.internals.positionAbsolute
        rects.push({
          id,
          x: Math.round(x / 4) * 4,
          y: Math.round(y / 4) * 4,
          width: Math.round(width),
          height: Math.round(height),
        })
      }
      return rects
    },
    (a, b) =>
      a.length === b.length &&
      a.every((rect, i) => {
        const other = b[i]
        return (
          rect.id === other.id &&
          rect.x === other.x &&
          rect.y === other.y &&
          rect.width === other.width &&
          rect.height === other.height
        )
      }),
  )
}
