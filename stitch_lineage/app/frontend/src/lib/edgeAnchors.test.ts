import { describe, expect, it } from 'vitest'
import { anchorOn, chooseAnchors, type AnchorEnd } from './edgeAnchors'
import { pathHitsRects, routeEdge, type RoutingRect } from './edgeRouting'

const CARD_W = 300
const CARD_H = 200

function card(id: string, x: number, y: number, width = CARD_W, height = CARD_H): RoutingRect {
  return { id, x, y, width, height }
}

/** A card end joining through a row a third of the way down it. */
function end(rect: RoutingRect, at = 0.33): AnchorEnd {
  return { rect, row: rect.y + rect.height * at }
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

  it('anchors on the card border, never in a corner', () => {
    const source = card('a', 0, 0)
    const target = card('b', 40, 800)
    const chosen = chooseAnchors(end(source), end(target))
    expect(chosen.from.y).toBe(source.y + source.height)
    expect(chosen.from.x).toBeGreaterThan(source.x + 8)
    expect(chosen.from.x).toBeLessThan(source.x + source.width - 8)
    expect(chosen.to.y).toBe(target.y)
  })

  it('keeps a left or right anchor on the row it joins through', () => {
    const source = card('a', 0, 0)
    const target = card('b', 800, 0)
    const chosen = chooseAnchors({ rect: source, row: 120 }, { rect: target, row: 64 })
    expect(chosen.from).toEqual({ x: source.x + source.width, y: 120, side: 'right' })
    expect(chosen.to).toEqual({ x: target.x, y: 64, side: 'left' })
  })

  it('spreads two relationships between the same pair of cards apart', () => {
    // stacked cards put both edges on the same border; landing them on the same
    // point would draw one line where there are two
    const source = card('a', 0, 0)
    const target = card('b', 0, 700)
    const first = chooseAnchors({ rect: source, row: 40 }, { rect: target, row: 740 })
    const second = chooseAnchors({ rect: source, row: 170 }, { rect: target, row: 860 })
    expect(first.from.side).toBe('bottom')
    expect(second.from.side).toBe('bottom')
    expect(Math.abs(first.from.x - second.from.x)).toBeGreaterThan(8)
    expect(Math.abs(first.to.x - second.to.x)).toBeGreaterThan(8)
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

  it('re-chooses when a card is expanded and grows past its partner', () => {
    // expanding a table changes its height, which is exactly the case #62 reflows;
    // the side pair has to be recomputed from the new box, not kept from the old one
    const target = card('b', 500, 500)
    const collapsed = card('a', 0, 0, CARD_W, 200)
    const expanded = card('a', 0, 0, CARD_W, 800)
    expect(sides(end(collapsed), end(target))).toBe('bottom→top')
    // grown down past the target's row, leaving from the bottom would be a U-turn
    expect(sides(end(expanded), end(target))).toBe('right→top')
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
})

describe('anchorOn', () => {
  it('puts a top or bottom anchor on the side of the card the other end is on', () => {
    const rect = card('a', 0, 0)
    const left = anchorOn({ rect, row: 100 }, 'bottom', { x: -900, y: 400 })
    const right = anchorOn({ rect, row: 100 }, 'bottom', { x: 900, y: 400 })
    expect(left.x).toBeLessThan(rect.x + rect.width / 2)
    expect(right.x).toBeGreaterThan(rect.x + rect.width / 2)
    expect(left.y).toBe(rect.y + rect.height)
  })

  it('survives a card with no measured height', () => {
    const anchor = anchorOn({ rect: card('a', 0, 0, 300, 0), row: 0 }, 'top', { x: 0, y: 500 })
    expect(Number.isFinite(anchor.x)).toBe(true)
    expect(Number.isFinite(anchor.y)).toBe(true)
  })
})
