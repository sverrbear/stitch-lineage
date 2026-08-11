import { describe, expect, it } from 'vitest'
import { CLICK_SLOP_PX, isClickNotDrag, mergeCanvasNodes } from './canvas'

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

const resized = (a: TestNode, b: TestNode) => a.data.expanded !== b.data.expanded
const merge = (current: TestNode[], next: TestNode[]) => mergeCanvasNodes(current, next, resized)

describe('mergeCanvasNodes', () => {
  it('never renders fewer cards than the drawing has — the #175 invariant', () => {
    // whatever recomputes, and however it recomputes, the array handed to React Flow
    // is the new drawing in full: there is no frame where the canvas holds nothing
    const current = [card('a'), card('b'), card('c')]
    for (const next of [
      [card('a', 50), card('b'), card('c')], // one card nudged
      [card('a'), card('b'), card('c')], // nothing changed
      [card('a'), card('b'), card('c'), card('d')], // a card arrived
      [card('a'), card('c')], // a card left
    ]) {
      const merged = merge(current, next)
      expect(merged).toHaveLength(next.length)
      expect(merged.map((n) => n.id)).toEqual(next.map((n) => n.id))
    }
  })

  it('keeps every card’s measurement when only one of them moved', () => {
    // the bug: nudging one card rebuilt all 41, so all 41 lost the `measured` bounds
    // React Flow keeps on them
    const current = [card('a'), card('b'), card('c')]
    const merged = merge(current, [card('a', 120), card('b'), card('c')])
    for (const [i, node] of merged.entries()) {
      expect(node.measured, `card ${node.id} lost its measurement`).toEqual(current[i].measured)
    }
  })

  it('carries React Flow’s own bookkeeping across, not just `measured`', () => {
    // the rebuilt array knows nothing about the fields React Flow hangs on a node
    // (`internals`, holding handle bounds, among them). A merge that dropped those
    // would be the same bug wearing a different field name, so anything on the
    // rendered node and absent from the rebuilt one has to survive.
    const withInternals = { ...card('a'), internals: { handleBounds: 'kept' } }
    const merged = mergeCanvasNodes([withInternals], [card('a', 90)], resized)
    expect((merged[0] as typeof withInternals).internals).toEqual({ handleBounds: 'kept' })
    expect(merged[0].position).toEqual({ x: 90, y: 0 })
  })

  it('moves the card that moved', () => {
    const merged = merge([card('a'), card('b')], [card('a', 240), card('b')])
    expect(merged[0].position).toEqual({ x: 240, y: 0 })
  })

  it('hands over a RESIZED card fresh, so React Flow measures it again', () => {
    // carrying a stale `measured` here would make React Flow skip the measurement,
    // and handle bounds go with it — every edge on that card disappears
    const current = [card('a'), card('b')]
    const expanded = { ...card('a'), data: { expanded: true }, measured: undefined }
    const merged = merge(current, [expanded, card('b')])
    expect(merged[0].measured).toBeUndefined()
    // and only that one: its neighbour keeps its own measurement
    expect(merged[1].measured).toEqual({ width: 300, height: 200 })
  })

  it('leaves a dragging card where React Flow is putting it', () => {
    // mid-drag React Flow owns the position; a rebuild that overwrote it would snap
    // the card out from under the pointer
    const dragging = card('a', 500, { dragging: true })
    const merged = merge([dragging, card('b')], [card('a', 0), card('b')])
    expect(merged[0].position).toEqual({ x: 500, y: 0 })
  })

  it('takes the incoming position once the drag has ended', () => {
    const settled = card('a', 500, { dragging: false })
    expect(merge([settled], [card('a', 640)])[0].position).toEqual({ x: 640, y: 0 })
  })

  it('carries new data onto the kept object', () => {
    const current = [card('a')]
    const next = [{ ...card('a'), data: { expanded: false, label: 'lit' } }]
    const merged = merge(current, next)
    expect(merged[0].data.label).toBe('lit')
    expect(merged[0].measured).toEqual({ width: 300, height: 200 })
  })

  it('takes the whole incoming array when there is nothing on screen yet', () => {
    const next = [card('a'), card('b')]
    expect(merge([], next)).toEqual(next)
  })

  it('drops a card the drawing no longer has, without disturbing the rest', () => {
    const current = [card('a'), card('b'), card('c')]
    const merged = merge(current, [card('a'), card('c')])
    expect(merged.map((n) => n.id)).toEqual(['a', 'c'])
    expect(merged[1].measured).toEqual({ width: 300, height: 200 })
  })
})
