// The lineage view's edge (#176).
//
// Two defects it exists to remove, both from drawing every edge as React Flow's
// default bezier between two handle centres:
//
//   * every edge of a handle attaches to the same pixel, so two relationships
//     arriving at one column row are drawn as one line. Measured on the view from the
//     report: three attachment points carrying two edges each;
//   * a bezier sweeps across the middle of the span, which is where the cards of the
//     intervening layers are. Measured on the same view: one edge of eleven crossing
//     a card body — `report_date -> daily_status`, passing under `dim_users`.
//
// So the ends are fanned (lib/lineage.edgeFans) to keep converging edges apart, and
// the path is routed around the cards with the ERD's own router (#146: chooseAnchors
// + routeEdge) whenever the drawing is small enough for that to mean anything.
//
// "Small enough" is a real limit, not a hedge. A hub column feeding 295 cards has no
// free corridor to route through — the cards ARE the space — and an A* per edge over
// 300 obstacles is not a frame budget. Past the cap the curve stays and the fan still
// separates the ends; making that view readable means fixing the fan-out layout,
// which is a different job.

import { BaseEdge, type EdgeProps } from '@xyflow/react'
import { useMemo } from 'react'
import { chooseAnchors } from '../lib/edgeAnchors'
import { roundedPath, routeEdge, type RoutingRect } from '../lib/edgeRouting'
import type { EdgeFan } from '../lib/lineage'
import { useCardRects } from './useCardRects'

/**
 * Cards on the canvas past which edges keep their curve.
 *
 * Sized to the drawings a reader actually reads edge by edge. The 305-card fan-out is
 * not one of them, and routing it would spend a search per edge to produce a bundle
 * nobody can follow anyway.
 */
export const ROUTE_MAX_CARDS = 80

/** Softer than the ERD's square turns: the lineage view reads as flow, not schema. */
const CORNER_PX = 8

export function LineageRoutedEdge({
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  style,
  interactionWidth,
}: EdgeProps) {
  const cards = useCardRects()
  const fan = (data as { fan?: EdgeFan } | undefined)?.fan
  const fromY = sourceY + (fan?.source ?? 0)
  const toY = targetY + (fan?.target ?? 0)

  const path = useMemo(() => {
    // A curve between the two fanned ends — the shape wherever the router declines.
    const bezier = () => {
      const reach = Math.max(40, Math.abs(targetX - sourceX) / 2)
      return `M${sourceX},${fromY} C${sourceX + reach},${fromY} ${targetX - reach},${toY} ${targetX},${toY}`
    }
    if (cards.length === 0 || cards.length > ROUTE_MAX_CARDS) return bezier()

    const obstacles: RoutingRect[] = []
    let sourceCard: RoutingRect | undefined
    let targetCard: RoutingRect | undefined
    for (const rect of cards) {
      if (rect.id === source) sourceCard = rect
      else if (rect.id === target) targetCard = rect
      else obstacles.push(rect)
    }
    // Before React Flow has measured both ends there is nothing to route between.
    if (!sourceCard || !targetCard) return bezier()

    // The fanned rows go INTO the choice, so a left or right anchor lands on the row
    // this edge is actually drawn at rather than on the handle's centre.
    const anchors = chooseAnchors(
      { rect: sourceCard, row: fromY },
      { rect: targetCard, row: toY },
      obstacles,
    )
    // `soft`: the edge's own two cards are passable at a price, so an edge whose
    // partner sits behind another card still gets a line instead of no line at all.
    const points = routeEdge(anchors.from, anchors.to, obstacles, {
      soft: [sourceCard, targetCard],
    })
    return roundedPath(points, CORNER_PX)
  }, [cards, source, target, sourceX, targetX, fromY, toY])

  return <BaseEdge path={path} style={style} interactionWidth={interactionWidth} />
}
