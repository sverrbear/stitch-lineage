import { describe, expect, it } from 'vitest'
import { CLICK_SLOP_PX, canvasSettled, isClickNotDrag } from './canvas'

describe('isClickNotDrag', () => {
  it('treats a stationary press-release as a click', () => {
    expect(isClickNotDrag({ x: 100, y: 100 }, { x: 100, y: 100 })).toBe(true)
  })

  it('tolerates hand jitter within the slop', () => {
    expect(isClickNotDrag({ x: 100, y: 100 }, { x: 102, y: 101 })).toBe(true)
    expect(isClickNotDrag({ x: 100, y: 100 }, { x: 100 + CLICK_SLOP_PX, y: 100 })).toBe(true)
  })

  it('treats a press that travelled beyond the slop as a drag', () => {
    expect(isClickNotDrag({ x: 100, y: 100 }, { x: 100 + CLICK_SLOP_PX + 1, y: 100 })).toBe(false)
    expect(isClickNotDrag({ x: 100, y: 100 }, { x: 140, y: 260 })).toBe(false)
  })

  it('honours a caller-supplied slop', () => {
    expect(isClickNotDrag({ x: 0, y: 0 }, { x: 10, y: 0 }, 12)).toBe(true)
    expect(isClickNotDrag({ x: 0, y: 0 }, { x: 10, y: 0 }, 8)).toBe(false)
  })

  it('treats a click with no recorded press origin (keyboard) as a click', () => {
    expect(isClickNotDrag(null, { x: 0, y: 0 })).toBe(true)
  })
})

// --- the canvas must never empty itself to move one card (#175) --------------

interface TestNode {
  id: string
  position: { x: number; y: number }
  data: { expanded: boolean; label?: string }
  measured?: { width?: number; height?: number }
  dragging?: boolean
}

function card(id: string, x = 0, extra: Partial<TestNode> = {}): TestNode {
  return {
    id,
    position: { x, y: 0 },
    data: { expanded: false },
    measured: { width: 300, height: 200 },
    ...extra,
  }
}

// --- the viewport frames the arrangement that is on screen (#185) -------------

describe('canvasSettled', () => {
  it('is true when every card is measured and where the layout put it', () => {
    const target = [card('a'), card('b', 400)]
    expect(canvasSettled(target, target)).toBe(true)
  })

  it('is false while a card still sits at the previous arrangement’s coordinate', () => {
    // this is the bug: the layout is recomputed several times on entry (estimated card
    // heights, then measured ones, then staged/suggested relationships arriving), and a
    // fit fired now would frame the arrangement the reader is about to stop seeing
    const rendered = [card('a'), card('b', 400)]
    const target = [card('a'), card('b', 980)]
    expect(canvasSettled(rendered, target)).toBe(false)
  })

  it('is false until React Flow has measured the cards', () => {
    // React Flow fits the bounds it computes from `measured`, so fitting before it has
    // them frames a degenerate box — which is what a fixed timer could not know
    const target = [card('a'), card('b', 400)]
    for (const measured of [undefined, {}, { width: 300 }, { width: 300, height: 0 }]) {
      const rendered = [{ ...card('a'), measured }, card('b', 400)]
      expect(canvasSettled(rendered, target), `measured ${JSON.stringify(measured)}`).toBe(false)
    }
  })

  it('is false while the canvas holds a different set of cards', () => {
    // mid-scope-change the old scope's cards are still rendered, measured and settled:
    // agreeing on the count is not agreeing on the drawing
    expect(canvasSettled([card('a'), card('b')], [card('a'), card('c')])).toBe(false)
    expect(canvasSettled([card('a')], [card('a'), card('b')])).toBe(false)
    expect(canvasSettled([card('a'), card('b')], [card('a')])).toBe(false)
  })

  it('is false for an empty drawing — there is no arrangement to frame', () => {
    expect(canvasSettled([], [])).toBe(false)
  })

  it('does not care what order the canvas holds the cards in', () => {
    const rendered = [card('b', 400), card('a')]
    expect(canvasSettled(rendered, [card('a'), card('b', 400)])).toBe(true)
  })

  it('ignores everything about a card except where it is and whether it measured', () => {
    // a hovered relationship lights two rows, which rewrites every card's `data`; that
    // must not read as an arrangement change and refit the canvas under the pointer
    const rendered = [{ ...card('a'), data: { expanded: false, label: 'lit' } }]
    expect(canvasSettled(rendered, [card('a')])).toBe(true)
  })
})
