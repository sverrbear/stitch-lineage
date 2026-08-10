// The ERD's relationship edge (#79). React Flow's own edge types draw handle to
// handle and know nothing about the cards in between, which is how a relationship
// ends up running underneath a table and out the other side. This one asks
// lib/edgeRouting for a path that stays in the corridors between the cards.

import { BaseEdge, Position, useStore, type EdgeProps } from '@xyflow/react'
import { useMemo } from 'react'
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
 * The `1` and `*` glyphs a model view puts on each end of a relationship. Defined
 * once per canvas and referenced by id, so every surface that draws relationships
 * (the ERD, the model page's mini star) renders identical ends. `orient="0"` keeps
 * them upright whatever direction the edge runs, and the two `refX` values nudge
 * each glyph clear of the card it belongs to (sources leave from the right edge,
 * targets arrive at the left).
 */
export function ErdMarkers() {
  return (
    <svg className="erd-markers" aria-hidden="true" focusable="false">
      <defs>
        <marker
          id="erd-card-many"
          viewBox="0 0 14 14"
          markerWidth="14"
          markerHeight="14"
          refX="0"
          refY="7"
          orient="0"
        >
          <text className="erd-marker-glyph" x="7" y="11" textAnchor="middle">
            *
          </text>
        </marker>
        <marker
          id="erd-card-one"
          viewBox="0 0 14 14"
          markerWidth="14"
          markerHeight="14"
          refX="14"
          refY="7"
          orient="0"
        >
          <text className="erd-marker-glyph" x="7" y="11" textAnchor="middle">
            1
          </text>
        </marker>
      </defs>
    </svg>
  )
}

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
  markerStart,
  markerEnd,
  style,
  interactionWidth,
}: EdgeProps) {
  const cards = useCardRects()
  const points = useMemo(() => {
    const own: RoutingRect[] = []
    const obstacles: RoutingRect[] = []
    for (const rect of cards) {
      if (rect.id === source || rect.id === target) own.push(rect)
      else obstacles.push(rect)
    }
    return routeEdge(
      { x: sourceX, y: sourceY, side: sideOf(sourcePosition, 'right') },
      { x: targetX, y: targetY, side: sideOf(targetPosition, 'left') },
      obstacles,
      { soft: own },
    )
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
      markerStart={markerStart}
      markerEnd={markerEnd}
      style={style}
      interactionWidth={interactionWidth}
    />
  )
}
