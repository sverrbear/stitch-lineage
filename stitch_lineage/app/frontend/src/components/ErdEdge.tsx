// The ERD's relationship edge (#79). React Flow's own edge types draw handle to
// handle and know nothing about the cards in between, which is how a relationship
// ends up running underneath a table and out the other side. This one asks
// lib/edgeRouting for a path that stays in the corridors between the cards.

import { BaseEdge, Position, useStore, type EdgeProps } from '@xyflow/react'
import { useMemo } from 'react'
import { chooseAnchors } from '../lib/edgeAnchors'
import {
  polylineMidpoint,
  roundedPath,
  routeEdge,
  type AnchorSide,
  type RoutingRect,
} from '../lib/edgeRouting'

/** Corner radius of the routed path — enough to read as drawn, not as a diagram. */
const CORNER_PX = 9

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
  label,
  labelStyle,
  labelShowBg,
  labelBgStyle,
  labelBgPadding,
  labelBgBorderRadius,
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
    return { points: routeEdge(anchors.from, anchors.to, obstacles, { soft: own }) }
  }, [cards, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition])

  const label_ = polylineMidpoint(points)

  return (
    <BaseEdge
      path={roundedPath(points, CORNER_PX)}
      label={label}
      labelX={label_.x}
      labelY={label_.y}
      labelStyle={labelStyle}
      labelShowBg={labelShowBg}
      labelBgStyle={labelBgStyle}
      labelBgPadding={labelBgPadding}
      labelBgBorderRadius={labelBgBorderRadius}
      style={style}
      interactionWidth={interactionWidth}
    />
  )
}
