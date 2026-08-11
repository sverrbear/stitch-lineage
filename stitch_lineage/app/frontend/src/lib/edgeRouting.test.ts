import { describe, expect, it } from 'vitest'
import {
  pathHitsRects,
  polylineLength,
  polylineMidpoint,
  roundedPath,
  routeEdge,
  segmentHitsBox,
  simplify,
  type RoutingRect,
} from './edgeRouting'

const CARD_W = 300
const CARD_H = 200

function card(id: string, x: number, y: number, width = CARD_W, height = CARD_H): RoutingRect {
  return { id, x, y, width, height }
}

/** The right-edge handle of a card, a third of the way down. */
function rightOf(rect: RoutingRect, at = 0.33) {
  return { x: rect.x + rect.width, y: rect.y + rect.height * at, side: 'right' as const }
}

/** The left-edge handle of a card. */
function leftOf(rect: RoutingRect, at = 0.33) {
  return { x: rect.x, y: rect.y + rect.height * at, side: 'left' as const }
}

/** An anchor on the card's bottom border, a third of the way across (#100). */
function bottomOf(rect: RoutingRect, at = 0.33) {
  return { x: rect.x + rect.width * at, y: rect.y + rect.height, side: 'bottom' as const }
}

/** An anchor on the card's top border. */
function topOf(rect: RoutingRect, at = 0.33) {
  return { x: rect.x + rect.width * at, y: rect.y, side: 'top' as const }
}

function bends(points: Array<{ x: number; y: number }>): number {
  return Math.max(0, simplify(points).length - 2)
}

describe('routeEdge — obstacle avoidance', () => {
  it('runs straight when nothing is in the way', () => {
    const source = card('a', 0, 0)
    const target = card('b', 600, 0)
    const path = routeEdge(rightOf(source), leftOf(target), [], { soft: [source, target] })
    expect(bends(path)).toBe(0)
    expect(polylineLength(path)).toBeCloseTo(300, 0)
  })

  it('keeps a neighbouring card a short direct hop, not a detour', () => {
    const source = card('a', 0, 0)
    const target = card('b', CARD_W + 52, 40)
    const from = rightOf(source)
    const to = leftOf(target)
    const path = routeEdge(from, to, [], { soft: [source, target] })
    // The bound is the MANHATTAN run, not the diagonal one: since #139 the path is
    // axis-aligned, and an axis-aligned path between two points offset on both axes
    // is Manhattan by definition -- up to a factor of root two longer than the
    // straight line, with nothing wrong with it. What "not a detour" still means is
    // that it costs the two offsets and the two stubs, and nothing else.
    const manhattan = Math.abs(to.x - from.x) + Math.abs(to.y - from.y)
    expect(polylineLength(path)).toBeLessThan(manhattan * 1.1)
    expect(bends(path)).toBeLessThanOrEqual(2)
  })

  it('routes around a card standing between the two ends', () => {
    const source = card('a', 0, 0)
    const target = card('b', 900, 0)
    const wall = card('wall', 450, -40)
    const path = routeEdge(rightOf(source), leftOf(target), [wall], { soft: [source, target] })
    expect(pathHitsRects(path, [wall])).toEqual([])
    expect(bends(path)).toBeGreaterThan(0)
  })

  it('leaves real clearance, not a line grazing the card', () => {
    const source = card('a', 0, 0)
    const target = card('b', 900, 0)
    const wall = card('wall', 450, -40)
    const path = routeEdge(rightOf(source), leftOf(target), [wall], {
      soft: [source, target],
      margin: 16,
    })
    // 8px of the 16px margin is left even after the corners are rounded off
    expect(pathHitsRects(roundedCorners(path), [wall], { margin: 8 })).toEqual([])
  })

  it('threads a corridor through a field of cards', () => {
    const source = card('a', 0, 500)
    const target = card('b', 1800, 500)
    const field: RoutingRect[] = []
    for (let column = 0; column < 3; column++) {
      for (let row = 0; row < 3; row++) {
        field.push(card(`f${column}${row}`, 420 + column * 400, 180 + row * 300))
      }
    }
    const path = routeEdge(rightOf(source), leftOf(target), field, { soft: [source, target] })
    expect(pathHitsRects(path, field)).toEqual([])
  })

  it('does not cross its own two cards when the target sits to the left', () => {
    // the star layout puts half of a hub's dimensions on its left, so the handles
    // face away from each other and a straight run would go through both cards
    const source = card('a', 800, 0)
    const target = card('b', 0, 260)
    const path = routeEdge(rightOf(source), leftOf(target), [], { soft: [source, target] })
    expect(pathHitsRects(path, [source, target])).toEqual([])
  })

  it('avoids a third card as well as its own on a backward relationship', () => {
    const source = card('a', 800, 0)
    const target = card('b', 0, 320)
    const wall = card('wall', 380, 120)
    const path = routeEdge(rightOf(source), leftOf(target), [wall], { soft: [source, target] })
    expect(pathHitsRects(path, [wall, source, target])).toEqual([])
  })

  it('avoids every card of a real-shaped constellation', () => {
    // the star layout's shape: a fact in the middle, its dimensions on an orbit
    const hub = card('hub', 900, 600)
    const ring: RoutingRect[] = []
    for (let i = 0; i < 10; i++) {
      const angle = (2 * Math.PI * i) / 10
      ring.push(
        card(
          `dim_${i}`,
          Math.round(900 + Math.cos(angle) * 820),
          Math.round(600 + Math.sin(angle) * 620),
        ),
      )
    }
    const all = [hub, ...ring]
    // the layout never overlaps two cards, and routing may rely on that
    for (const [i, a] of all.entries()) {
      for (const b of all.slice(i + 1)) {
        const apart =
          a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y
        expect(apart, `${a.id} overlaps ${b.id}`).toBe(true)
      }
    }
    for (const [index, dim] of ring.entries()) {
      const others = all.filter((rect) => rect.id !== hub.id && rect.id !== dim.id)
      const path = routeEdge(rightOf(dim, 0.2 + index * 0.05), leftOf(hub, 0.3), others, {
        soft: [dim, hub],
      })
      expect(pathHitsRects(path, others)).toEqual([])
    }
  })

  it('still avoids a card standing only a stub-length away', () => {
    // the case that survived the first cut: the neighbour is 25px off, so its
    // clearance ring swallows the point the route starts from. Dropping it there
    // is what drew a line straight through it; the clearance shrinks instead.
    const source = card('a', 376, 1276, 247, 266)
    const wall = card('wall', 648, 1116, 300, 266)
    const target = card('b', 976, 928, 236, 266)
    const path = routeEdge(
      { x: source.x + source.width, y: 1348, side: 'right' },
      { x: target.x, y: 998, side: 'left' },
      [wall],
      { soft: [source, target] },
    )
    expect(pathHitsRects(path, [wall])).toEqual([])
  })

  it('still returns a drawable path when an end is walled in', () => {
    const source = card('a', 0, 0)
    const target = card('b', 900, 0)
    const cage = [
      card('north', 320, -300, 260, 260),
      card('south', 320, 220, 260, 260),
      card('east', 360, -40, 60, 280),
    ]
    const path = routeEdge(rightOf(source), leftOf(target), cage, { soft: [source, target] })
    expect(path.length).toBeGreaterThan(1)
    for (const point of path) expect(Number.isFinite(point.x) && Number.isFinite(point.y)).toBe(true)
  })

  it('is deterministic', () => {
    const source = card('a', 0, 0)
    const target = card('b', 1200, 300)
    const walls = [card('w1', 400, -100), card('w2', 760, 220)]
    const first = routeEdge(rightOf(source), leftOf(target), walls, { soft: [source, target] })
    const second = routeEdge(rightOf(source), leftOf(target), walls, { soft: [source, target] })
    expect(second).toEqual(first)
  })

  it('runs straight down between two stacked cards', () => {
    const source = card('a', 0, 0)
    const target = card('b', 0, 700)
    const path = routeEdge(bottomOf(source), topOf(target), [], { soft: [source, target] })
    expect(bends(path)).toBe(0)
    expect(polylineLength(path)).toBeCloseTo(500, 0)
  })

  it('leaves a vertical anchor along its own normal, never back into its card', () => {
    // the guard the horizontal sides always had, now for top and bottom (#100)
    const source = card('a', 0, 700)
    const target = card('b', 0, 0)
    const wall = card('wall', -40, 380, 380, 120)
    const path = routeEdge(topOf(source), bottomOf(target), [wall], { soft: [source, target] })
    expect(pathHitsRects(path, [wall, source, target])).toEqual([])
    // the first step out of the card goes UP, away from the card it left
    expect(path[1].y).toBeLessThan(path[0].y)
  })

  it('routes a mixed pair — one end sideways, the other downwards', () => {
    const source = card('a', 0, 0)
    const target = card('b', 700, 600)
    const wall = card('wall', 360, 260)
    const path = routeEdge(rightOf(source), topOf(target), [wall], { soft: [source, target] })
    expect(pathHitsRects(path, [wall, source, target])).toEqual([])
    // ...and arrives at the top border from above
    expect(path[path.length - 2].y).toBeLessThan(path[path.length - 1].y)
  })

  it('avoids a field of cards on a vertical pair too', () => {
    const source = card('a', 500, 0)
    const target = card('b', 500, 1800)
    const field: RoutingRect[] = []
    for (let column = 0; column < 3; column++) {
      for (let row = 0; row < 3; row++) {
        field.push(card(`f${column}${row}`, 180 + column * 400, 420 + row * 300))
      }
    }
    const path = routeEdge(bottomOf(source), topOf(target), field, { soft: [source, target] })
    expect(pathHitsRects(path, field)).toEqual([])
  })

  it('honours a bounded search rather than stalling on a dense scope', () => {
    const source = card('a', 0, 0)
    const target = card('b', 4000, 2000)
    const field: RoutingRect[] = []
    for (let column = 0; column < 8; column++) {
      for (let row = 0; row < 8; row++) {
        field.push(card(`f${column}${row}`, 400 + column * 420, 120 + row * 260))
      }
    }
    const path = routeEdge(rightOf(source), leftOf(target), field, { soft: [source, target] })
    expect(path.length).toBeGreaterThan(1)
  })
})

/** The polyline the rounded `d` actually draws, sampled back as points. */
function roundedCorners(points: Array<{ x: number; y: number }>) {
  const d = roundedPath(points)
  const numbers = d.match(/-?\d+(\.\d+)?/g) ?? []
  const out: Array<{ x: number; y: number }> = []
  for (let i = 0; i < numbers.length - 1; i += 2) {
    out.push({ x: Number(numbers[i]), y: Number(numbers[i + 1]) })
  }
  return out
}

describe('pathHitsRects', () => {
  it('reports the card a path runs through', () => {
    const wall = card('wall', 100, -50)
    expect(pathHitsRects([{ x: 0, y: 0 }, { x: 600, y: 0 }], [wall])).toEqual(['wall'])
  })

  it('exempts the endpoints it is told to', () => {
    const wall = card('wall', 100, -50)
    expect(
      pathHitsRects([{ x: 0, y: 0 }, { x: 600, y: 0 }], [wall], { exempt: ['wall'] }),
    ).toEqual([])
  })

  it('treats a path along a card edge as clear, not as a hit', () => {
    const wall = card('wall', 100, 0)
    expect(pathHitsRects([{ x: 0, y: 0 }, { x: 600, y: 0 }], [wall])).toEqual([])
  })
})

describe('segmentHitsBox', () => {
  const box = { left: 0, right: 100, top: 0, bottom: 100 }

  it('sees a segment crossing the box', () => {
    expect(segmentHitsBox({ x: -50, y: 50 }, { x: 150, y: 50 }, box)).toBe(true)
  })

  it('ignores one passing outside it', () => {
    expect(segmentHitsBox({ x: -50, y: 150 }, { x: 150, y: 150 }, box)).toBe(false)
  })

  it('ignores one that only touches the edge', () => {
    expect(segmentHitsBox({ x: -50, y: 0 }, { x: 150, y: 0 }, box)).toBe(false)
  })

  it('sees a segment that ends inside the box', () => {
    expect(segmentHitsBox({ x: -50, y: 50 }, { x: 50, y: 50 }, box)).toBe(true)
  })
})

describe('the elbowed path the ERD draws (#139)', () => {
  const elbow = (points: Array<{ x: number; y: number }>) => roundedPath(points, 0)

  it('turns square: no curve command anywhere in it', () => {
    // Power BI's relationship lines turn at right angles; a rounded corner reads as
    // a curve at the zoom a big scope is actually viewed at
    const d = elbow([
      { x: 0, y: 0 },
      { x: 100, y: 0 },
      { x: 100, y: 100 },
    ])
    expect(d).not.toMatch(/[QCAqca]/)
    expect(d).toBe('M 0,0 L 100,0 L 100,100')
  })

  it('keeps every segment axis-aligned when the route is', () => {
    const points = routeEdge(
      rightOf(card('a', 0, 0)),
      leftOf(card('b', 900, 400)),
      [card('wall', 420, -40)],
      { soft: [card('a', 0, 0), card('b', 900, 400)] },
    )
    for (let i = 0; i < points.length - 1; i++) {
      const dx = Math.abs(points[i + 1].x - points[i].x)
      const dy = Math.abs(points[i + 1].y - points[i].y)
      expect(dx < 0.01 || dy < 0.01, `segment ${i} is diagonal`).toBe(true)
    }
  })

  it('still lands exactly on both anchors', () => {
    const from = rightOf(card('a', 0, 0))
    const to = leftOf(card('b', 900, 0))
    const d = elbow(routeEdge(from, to, [], { soft: [card('a', 0, 0), card('b', 900, 0)] }))
    expect(d.startsWith(`M ${from.x},${from.y}`)).toBe(true)
    expect(d.endsWith(`L ${to.x},${to.y}`)).toBe(true)
  })
})

describe('roundedPath', () => {
  it('keeps the first and last point', () => {
    const d = roundedPath([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }])
    expect(d.startsWith('M 0,0')).toBe(true)
    expect(d.endsWith('L 100,100')).toBe(true)
  })

  it('rounds a corner with a curve', () => {
    const d = roundedPath([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }])
    expect(d).toContain('Q 100,0')
  })

  it('draws a straight run as one line', () => {
    expect(roundedPath([{ x: 0, y: 0 }, { x: 50, y: 0 }, { x: 100, y: 0 }])).toBe('M 0,0 L 100,0')
  })

  it('survives a degenerate path', () => {
    expect(roundedPath([])).toBe('')
    expect(roundedPath([{ x: 3, y: 4 }])).toBe('M 3,4')
  })
})

describe('polyline helpers', () => {
  it('measures length along the corners', () => {
    expect(polylineLength([{ x: 0, y: 0 }, { x: 30, y: 0 }, { x: 30, y: 40 }])).toBe(70)
  })

  it('finds the half-way point for a label', () => {
    expect(polylineMidpoint([{ x: 0, y: 0 }, { x: 100, y: 0 }])).toEqual({ x: 50, y: 0 })
    expect(polylineMidpoint([{ x: 0, y: 0 }, { x: 40, y: 0 }, { x: 40, y: 40 }])).toEqual({
      x: 40,
      y: 0,
    })
  })

  it('drops duplicate and collinear points', () => {
    expect(
      simplify([{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 50, y: 0 }, { x: 100, y: 0 }]),
    ).toEqual([{ x: 0, y: 0 }, { x: 100, y: 0 }])
  })
})
