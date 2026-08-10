// The ERD's relationship edge (#79). React Flow's own edge types draw handle to
// handle and know nothing about the cards in between, which is how a relationship
// ends up running underneath a table and out the other side. This one asks
// lib/edgeRouting for a path that stays in the corridors between the cards.

import { BaseEdge, Position, useStore, type EdgeProps } from '@xyflow/react'
import { useMemo } from 'react'
import { chooseAnchors, markerForSide } from '../lib/edgeAnchors'
import {
  polylineMidpoint,
  roundedPath,
  routeEdge,
  type AnchorSide,
  type RoutingRect,
} from '../lib/edgeRouting'

/** Corner radius of the routed path — enough to read as drawn, not as a diagram. */
const CORNER_PX = 9

const GLYPHS = [
  { name: 'many', text: '*' },
  { name: 'one', text: '1' },
] as const

/**
 * Where a glyph sits relative to the path end, per side: the reference point moves
 * to the marker border the card is on, which pushes the 14px box outward. A `1` on
 * a card's top edge belongs ABOVE the line's end, not beside it.
 */
const SIDE_REF: Record<AnchorSide, { refX: number; refY: number }> = {
  left: { refX: 14, refY: 7 },
  right: { refX: 0, refY: 7 },
  top: { refX: 7, refY: 14 },
  bottom: { refX: 7, refY: 0 },
}

/**
 * The `1` and `*` glyphs a model view puts on each end of a relationship. Defined
 * once per canvas and referenced by id, so every surface that draws relationships
 * (the ERD, the model page's mini star) renders identical ends. `orient="0"` keeps
 * them upright whatever direction the edge runs, and there is one variant per card
 * side (#100) because an edge may now leave from any of the four — `markerForSide`
 * picks the variant that clears the card this end is anchored to.
 */
export function ErdMarkers() {
  return (
    <svg className="erd-markers" aria-hidden="true" focusable="false">
      <defs>
        {GLYPHS.flatMap((glyph) =>
          (Object.keys(SIDE_REF) as AnchorSide[]).map((side) => (
            <marker
              key={`${glyph.name}-${side}`}
              id={`erd-card-${glyph.name}-${side}`}
              viewBox="0 0 14 14"
              markerWidth="14"
              markerHeight="14"
              refX={SIDE_REF[side].refX}
              refY={SIDE_REF[side].refY}
              orient="0"
            >
              <text className="erd-marker-glyph" x="7" y="11" textAnchor="middle">
                {glyph.text}
              </text>
            </marker>
          )),
        )}
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
  // Sides first, then the route: which border each end attaches to is decided for
  // this pair of cards (#100), and the router is handed the result. React Flow's own
  // handle positions stay left/right — they are where a relationship is DRAWN from,
  // not where a drawn one has to run.
  const { points, from, to } = useMemo(() => {
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
    return {
      points: routeEdge(anchors.from, anchors.to, obstacles, { soft: own }),
      from: anchors.from.side,
      to: anchors.to.side,
    }
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
      markerStart={markerForSide(markerStart, from)}
      markerEnd={markerForSide(markerEnd, to)}
      style={style}
      interactionWidth={interactionWidth}
    />
  )
}
