import { describe, expect, it } from 'vitest'
import { anchorOn, chooseAnchors, type AnchorEnd } from './edgeAnchors'
import { pathHitsRects, polylineMidpoint, routeEdge, type RoutingRect } from './edgeRouting'

const CARD_W = 300
const CARD_H = 200

function card(id: string, x: number, y: number, width = CARD_W, height = CARD_H): RoutingRect {
  return { id, x, y, width, height }
}

/** A card end. `at` is vestigial since #139 bundled the landing point per side —
 *  kept so the tests can still say "a different column of the same card" out loud. */
function end(rect: RoutingRect, _at = 0.33): AnchorEnd {
  return { rect }
}

function sides(from: AnchorEnd, to: AnchorEnd, obstacles: RoutingRect[] = []) {
  const chosen = chooseAnchors(from, to, obstacles)
  return `${chosen.from.side}→${chosen.to.side}`
}

describe('chooseAnchors — the side each end attaches to', () => {
  it('keeps the classic right→left when the target is simply to the right', () => {
    expect(sides(end(card('a', 0, 0)), end(card('b', 700, 40)))).toBe('right→left')
  })

  it('turns the pair around when the target is to the LEFT', () => {
    // #100: the old rule sent this edge out of the source's right side, back around
    // its own card and across the canvas to reach the target's left one
    expect(sides(end(card('a', 900, 0)), end(card('b', 0, 60)))).toBe('left→right')
  })

  it('uses top and bottom when one card is above the other', () => {
    expect(sides(end(card('a', 0, 0)), end(card('b', 20, 700)))).toBe('bottom→top')
    expect(sides(end(card('a', 0, 700)), end(card('b', 20, 0)))).toBe('top→bottom')
  })

  it('never leaves a card on a side facing away from the other end', () => {
    // a ring of targets around one source: every pair must set off towards its partner
    const source = card('hub', 1000, 1000)
    for (let i = 0; i < 16; i++) {
      const angle = (2 * Math.PI * i) / 16
      const target = card(
        `dim_${i}`,
        Math.round(1000 + Math.cos(angle) * 900),
        Math.round(1000 + Math.sin(angle) * 700),
      )
      const chosen = chooseAnchors(end(source), end(target))
      for (const [anchor, other] of [
        [chosen.from, chosen.to],
        [chosen.to, chosen.from],
      ]) {
        const out =
          anchor.side === 'left'
            ? { x: -1, y: 0 }
            : anchor.side === 'right'
              ? { x: 1, y: 0 }
              : anchor.side === 'top'
                ? { x: 0, y: -1 }
                : { x: 0, y: 1 }
        const facing = out.x * (other.x - anchor.x) + out.y * (other.y - anchor.y)
        expect(facing, `${anchor.side} faces away on dim_${i}`).toBeGreaterThan(0)
      }
    }
  })

  it('anchors on the middle of the card border it chose', () => {
    const source = card('a', 0, 0)
    const target = card('b', 40, 800)
    const chosen = chooseAnchors(end(source), end(target))
    expect(chosen.from).toEqual({
      x: source.x + source.width / 2,
      y: source.y + source.height,
      side: 'bottom',
    })
    expect(chosen.to).toEqual({ x: target.x + target.width / 2, y: target.y, side: 'top' })
  })

  it('lands two relationships between the same pair of cards on the same point', () => {
    // #139 replaces the spread that used to keep per-column anchors apart: Power BI
    // bundles, and which column a line joins is read off the row highlight (#123)
    const source = card('a', 0, 0)
    const target = card('b', 0, 700)
    expect(chooseAnchors(end(source), end(target))).toEqual(
      chooseAnchors(end(source, 0.9), end(target, 0.1)),
    )
  })

  it('prefers a side pair whose direct run is not already walled off', () => {
    // straight down is the shortest way between these two, and a card is parked in
    // that corridor: both ends take a border the run is clear from instead, which is
    // the pair the router can draw without a detour
    const source = card('a', 0, 0)
    const target = card('b', 0, 600)
    const wall = card('wall', 0, 320, CARD_W, 80)
    expect(sides(end(source), end(target))).toBe('bottom→top')
    const chosen = chooseAnchors(end(source), end(target), [wall])
    expect(`${chosen.from.side}→${chosen.to.side}`).not.toBe('bottom→top')
    const routed = routeEdge(chosen.from, chosen.to, [wall], { soft: [source, target] })
    expect(pathHitsRects(routed, [wall, source, target])).toEqual([])
  })

  it('re-chooses as a card is dragged past its partner', () => {
    // the choice is a function of the two rectangles and nothing else, which is what
    // makes it re-run on a drag (#100): the component re-reads the card boxes and
    // asks again, so the sides follow the cards rather than the direction of the join
    const source = card('a', 0, 0)
    const seen = [700, 400, -400, -900].map((x) => sides(end(source), end(card('b', x, 60))))
    expect(seen).toEqual(['right→left', 'right→left', 'left→right', 'left→right'])
  })

  it('re-measures when a card is expanded', () => {
    // Expanding a table changes its height, which is exactly the case #62 reflows.
    // Since #139 the anchor is the MIDDLE of its side, so it moves with the box: an
    // anchor computed from the collapsed card would land inside the expanded one.
    const target = card('b', 500, 500)
    const collapsed = card('a', 0, 0, CARD_W, 200)
    const expanded = card('a', 0, 0, CARD_W, 800)
    const before = chooseAnchors(end(collapsed), end(target))
    const after = chooseAnchors(end(expanded), end(target))
    expect(before.from.y).toBe(collapsed.y + collapsed.height / 2)
    expect(after.from.y).toBe(expanded.y + expanded.height / 2)
    expect(after.from.y).not.toBe(before.from.y)
  })

  it('is deterministic, whatever order the obstacles arrive in', () => {
    const source = card('a', 0, 0)
    const target = card('b', 640, 520)
    const walls = [card('w1', 340, 0), card('w2', 340, 300), card('w3', 0, 300)]
    const first = chooseAnchors(end(source), end(target), walls)
    const second = chooseAnchors(end(source), end(target), [...walls].reverse())
    expect(second).toEqual(first)
    expect(chooseAnchors(end(source), end(target), walls)).toEqual(first)
  })

  it('feeds the router: the chosen pair draws a shorter path than the fixed one did', () => {
    const source = card('a', 900, 0)
    const target = card('b', 0, 90)
    const fixed = routeEdge(
      { x: source.x + source.width, y: source.y + 60, side: 'right' },
      { x: target.x, y: target.y + 60, side: 'left' },
      [],
      { soft: [source, target] },
    )
    const chosen = chooseAnchors(end(source), end(target))
    const routed = routeEdge(chosen.from, chosen.to, [], { soft: [source, target] })
    const length = (points: Array<{ x: number; y: number }>) =>
      points.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - points[i].x, p.y - points[i].y), 0)
    expect(length(routed)).toBeLessThan(length(fixed) / 2)
    // and the #79 invariant still holds on the new anchors
    expect(pathHitsRects(routed, [source, target])).toEqual([])
  })
})

// What the side choice is FOR, composed with the router that consumes it (#139).
// The anchors decide which border a relationship leaves from; the router then keeps
// the elbowed run in the corridors between the cards. These assert on the path that
// actually gets drawn, not on either half alone.
describe('chooseAnchors — the elbowed path it produces', () => {
  const drawn = (from: AnchorEnd, to: AnchorEnd, obstacles: RoutingRect[] = []) => {
    const chosen = chooseAnchors(from, to, obstacles)
    return routeEdge(chosen.from, chosen.to, obstacles, { soft: [from.rect, to.rect] })
  }

  it('never draws a relationship through either of its own two cards', () => {
    // a ring of partners all the way around one card: whichever way the line goes,
    // it leaves from a border that faces its partner and never re-enters either box
    const source = card('hub', 1000, 1000)
    for (let i = 0; i < 24; i++) {
      const angle = (2 * Math.PI * i) / 24
      const target = card(
        `dim_${i}`,
        Math.round(1000 + Math.cos(angle) * 900),
        Math.round(1000 + Math.sin(angle) * 700),
      )
      expect(pathHitsRects(drawn(end(source), end(target)), [source, target])).toEqual([])
    }
  })

  it('holds even when the two cards nearly touch', () => {
    const source = card('a', 0, 0)
    for (const target of [card('b', 320, 0), card('b', 0, 220), card('b', -320, 30)]) {
      expect(pathHitsRects(drawn(end(source), end(target)), [source, target])).toEqual([])
    }
  })

  it('turns square: every segment of the drawn path is axis-aligned', () => {
    const source = card('a', 0, 0)
    const target = card('b', 900, 520)
    const wall = card('wall', 420, 200)
    const points = drawn(end(source), end(target), [wall])
    expect(points.length).toBeGreaterThan(1)
    for (let i = 0; i < points.length - 1; i++) {
      const dx = Math.abs(points[i + 1].x - points[i].x)
      const dy = Math.abs(points[i + 1].y - points[i].y)
      expect(dx < 0.01 || dy < 0.01, `segment ${i} is diagonal`).toBe(true)
    }
  })

  it('goes around a card parked in the corridor rather than through it', () => {
    // the elbow earns its keep here: a straight line between these two cuts the wall
    const source = card('a', 0, 0)
    const target = card('b', 0, 600)
    const wall = card('wall', 0, 320, CARD_W, 80)
    expect(pathHitsRects(drawn(end(source), end(target), [wall]), [wall])).toEqual([])
  })

  it('puts the ✓ on the line, measured along it rather than across a corner', () => {
    const points = drawn(end(card('a', 0, 0)), end(card('b', 700, 400)))
    const mid = polylineMidpoint(points)
    // the midpoint lies on one of the segments, not in the empty space a corner spans
    const onPath = points.slice(0, -1).some((a, i) => {
      const b = points[i + 1]
      const cross = (b.x - a.x) * (mid.y - a.y) - (b.y - a.y) * (mid.x - a.x)
      const within =
        mid.x >= Math.min(a.x, b.x) - 0.01 &&
        mid.x <= Math.max(a.x, b.x) + 0.01 &&
        mid.y >= Math.min(a.y, b.y) - 0.01 &&
        mid.y <= Math.max(a.y, b.y) + 0.01
      return Math.abs(cross) < 0.01 && within
    })
    expect(onPath).toBe(true)
  })
})

describe('anchorOn — one landing point per side (#139)', () => {
  it('puts every side\'s anchor at the middle of that side', () => {
    const rect = card('a', 0, 0)
    expect(anchorOn({ rect }, 'left')).toEqual({ x: rect.x, y: rect.y + rect.height / 2, side: 'left' })
    expect(anchorOn({ rect }, 'right')).toEqual({
      x: rect.x + rect.width,
      y: rect.y + rect.height / 2,
      side: 'right',
    })
    expect(anchorOn({ rect }, 'top')).toEqual({ x: rect.x + rect.width / 2, y: rect.y, side: 'top' })
    expect(anchorOn({ rect }, 'bottom')).toEqual({
      x: rect.x + rect.width / 2,
      y: rect.y + rect.height,
      side: 'bottom',
    })
  })

  it('is the same point however many relationships use the side', () => {
    // the whole point of the addendum: a hub is entered ONCE per side, not once per
    // column, so the border reads as structure instead of a row of arrival points
    const rect = card('hub', 0, 0)
    const seen = new Set(
      [0, 40, 120, 199].map(() => JSON.stringify(anchorOn({ rect }, 'right'))),
    )
    expect(seen.size).toBe(1)
  })

  it('survives a card with no measured height', () => {
    const anchor = anchorOn({ rect: card('a', 0, 0, 300, 0) }, 'top')
    expect(Number.isFinite(anchor.x)).toBe(true)
    expect(Number.isFinite(anchor.y)).toBe(true)
  })
})
