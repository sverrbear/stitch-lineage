// The ERD's relationship edge: orthogonal elbows, the shape Power BI actually
// draws (#139).
//
// #130 read "straight, Power BI style" as one dead-straight point-to-point segment.
// Seen live against Power BI that is the wrong shape: its relationship lines are
// axis-aligned runs joined by SQUARE turns. A diagonal is what a force layout
// produces; an elbow is what a diagram draws, and the right angles are what make a
// line readable as it passes a card rather than through it.
//
// So the planned polyline from #79 comes back — the A* router that keeps a line in
// the corridors BETWEEN the cards, rather than letting it disappear under a table
// and come out the other side. The only difference from what #130 removed is the
// corner radius: 9px of rounding then, square now.
//
// Unchanged through all three issues:
//   * which BORDER each end leaves from is chosen per pair (#100/#106), so the first
//     run sets off towards the card it is headed for, not out of the back of its own;
//   * every stroke, dash and colour. None of this touches styling, and none of the
//     indicators in Power BI's own reference come with it — no 1/* glyphs (#117
//     removed those), no arrow boxes, no direction markers. Just the elbowed line.
//
// And now nothing at all is drawn ON a relationship (#164): the ✓ that marked a
// validated one is gone, and validated-ness is carried by the stroke itself — see
// `.erd-edge.validated` in styles.css. A glyph pinned to a midpoint is a caption by
// another name, and at fit zoom over 30 relationships it was a field of ticks.

import { BaseEdge, Position, type EdgeProps } from '@xyflow/react'
import { useMemo } from 'react'
import { chooseAnchors } from '../lib/edgeAnchors'
import { useCardRects } from './useCardRects'
import { roundedPath, routeEdge, type AnchorSide, type RoutingRect } from '../lib/edgeRouting'

/**
 * Corner radius of the routed path. Zero: Power BI turns square, and a rounded
 * corner reads as a curve at the zoom levels a big scope is actually viewed at.
 */
const CORNER_PX = 0

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
  style,
  interactionWidth,
}: EdgeProps) {
  const cards = useCardRects()
  // Sides first, then the route: which border each end attaches to is decided for
  // this pair of cards (#100), and the router is handed the result. React Flow's own
  // handle positions stay left/right — they are where a relationship is DRAWN from,
  // not where a drawn one has to run.
  const { points } = useMemo(() => {
    const own: RoutingRect[] = []
    const obstacles: RoutingRect[] = []
    let sourceCard: RoutingRect | undefined
    let targetCard: RoutingRect | undefined
    for (const rect of cards) {
      if (rect.id === source) sourceCard = rect
      else if (rect.id === target) targetCard = rect
      else obstacles.push(rect)
    }
    if (sourceCard) own.push(sourceCard)
    if (targetCard) own.push(targetCard)
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
    // `soft`: the edge's own two cards are passable at a stiff price, so a satellite
    // placed behind its hub still gets a line instead of no line at all.
    return { points: routeEdge(anchors.from, anchors.to, obstacles, { soft: own }) }
  }, [cards, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition])

  // Nothing is drawn on the line any more: the column pair is read off the two rows
  // the edge lights in their cards (#118), and validated-ness off the stroke (#164).
  return (
    <BaseEdge
      path={roundedPath(points, CORNER_PX)}
      style={style}
      interactionWidth={interactionWidth}
    />
  )
}
