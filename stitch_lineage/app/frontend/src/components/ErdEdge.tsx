// The ERD's relationship edge. One dead-straight segment, point to point, the way
// Power BI draws a relationship (#130).
//
// It used to be a planned polyline through the corridors between the cards (#79):
// React Flow's own edge types run handle to handle and know nothing about the
// cards in between, so a relationship could disappear under a table and come out
// the other side, and the router existed to stop that. A routed line reads as a
// diagram of itself though — it bends, so the eye follows the bends instead of the
// join. A straight one is a single unambiguous statement that these two columns are
// the same thing, and it stays legible at any zoom.
//
// The two things that survive the change:
//   * which BORDER each end leaves from is still chosen per pair (#100/#106), so a
//     straight segment sets off towards the card it is headed for instead of out of
//     the back of its own;
//   * cards are kept out of the line's way by the LAYOUT (#129) rather than by
//     bending the line around them — which is the Power BI bargain, and the reason
//     the two issues are worth doing together.

import { BaseEdge, Position, useStore, type EdgeProps } from '@xyflow/react'
import { useMemo } from 'react'
import { chooseAnchors } from '../lib/edgeAnchors'
import {
  polylineMidpoint,
  straightPath,
  type AnchorSide,
  type RoutingRect,
} from '../lib/edgeRouting'

/**
 * Every card on the canvas, in flow coordinates. Positions are quantised so a
 * drag re-routes on real movement rather than on every sub-pixel frame.
 */
function useCardRects(): RoutingRect[] {
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

function sideOf(position: Position | undefined, fallback: AnchorSide): AnchorSide {
  if (position === Position.Left) return 'left'
  if (position === Position.Right) return 'right'
  return fallback
}

export function ErdRoutedEdge({
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  style,
  interactionWidth,
}: EdgeProps) {
  const cards = useCardRects()
  // Which border each end attaches to is decided for this pair of cards (#100), and
  // the segment is drawn between the two. React Flow's own handle positions stay
  // left/right — they are where a relationship is DRAWN from, not where a drawn one
  // has to leave.
  const { points } = useMemo(() => {
    const obstacles: RoutingRect[] = []
    let sourceCard: RoutingRect | undefined
    let targetCard: RoutingRect | undefined
    for (const rect of cards) {
      if (rect.id === source) sourceCard = rect
      else if (rect.id === target) targetCard = rect
      else obstacles.push(rect)
    }
    // Before React Flow has measured both cards there is nothing to choose between:
    // fall back to the handles' own sides so the edge still draws.
    const anchors =
      sourceCard && targetCard
        ? chooseAnchors(
            { rect: sourceCard, row: sourceY },
            { rect: targetCard, row: targetY },
            obstacles,
          )
        : {
            from: { x: sourceX, y: sourceY, side: sideOf(sourcePosition, 'right') },
            to: { x: targetX, y: targetY, side: sideOf(targetPosition, 'left') },
          }
    return { points: [anchors.from, anchors.to] }
  }, [cards, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition])

  // The ✓ is the only thing still drawn ON a relationship (#118). The
  // `from_column → to_column` pill that used to sit at this midpoint is gone: at
  // real density it was a wall of text over the canvas, and the pair it named is
  // read off the two column rows the edge lights up in their cards instead.
  const validated = (data as { validated?: boolean } | undefined)?.validated === true
  const mid = polylineMidpoint(points)

  return (
    <>
      <BaseEdge
        path={straightPath(points[0], points[1])}
        style={style}
        interactionWidth={interactionWidth}
      />
      {validated && (
        <text
          className="erd-edge-validated"
          x={mid.x}
          y={mid.y}
          textAnchor="middle"
          dominantBaseline="central"
        >
          ✓
        </text>
      )}
    </>
  )
}
