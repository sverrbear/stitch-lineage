// What a hover is allowed to change (#186).
//
// The bug was never in what the canvas LOOKED like — it was in how much of the
// canvas had to be rebuilt to change two rows' colour. So these tests are mostly
// counting: after pointing at one relationship, how many of the 29 lines and how
// many of the 290 rows give a different answer than before. It has to be one and
// two. Anything more is the flicker coming back.

import { describe, expect, it, vi } from 'vitest'
import {
  createErdHighlight,
  EDGE_HOVERED,
  EDGE_HOVERED_PICKED,
  EDGE_PICKED,
  EDGE_PLAIN,
  type EdgeHighlight,
  type ErdEdgePick,
} from './erdHighlight'

/** A canvas the size of the real `visualisation` scope: 29 lines, 290 rows. */
function canvas() {
  const edges: ErdEdgePick[] = Array.from({ length: 29 }, (_, i) => ({
    id: `rel-${i}`,
    columns: [`model_${i}::id`, `model_${i + 1}::${i}_id`],
  }))
  const columns = Array.from({ length: 290 }, (_, i) => `model_${i % 42}::col_${i}`)
  // every column a relationship joins is a real row on some card
  for (const edge of edges) columns.push(...edge.columns)
  return { edges, columns }
}

/** Every answer the canvas currently reads out of the store. */
function snapshot(
  highlight: ReturnType<typeof createErdHighlight>,
  { edges, columns }: ReturnType<typeof canvas>,
) {
  return {
    edges: new Map<string, EdgeHighlight>(
      edges.map((edge) => [edge.id, highlight.edgeHighlight(edge.id)]),
    ),
    columns: new Map(columns.map((id) => [id, highlight.isLit(id)])),
  }
}

function changed(before: ReturnType<typeof snapshot>, after: ReturnType<typeof snapshot>) {
  const edges = [...before.edges].filter(([id, was]) => after.edges.get(id) !== was).map(([id]) => id)
  const columns = [...before.columns]
    .filter(([id, was]) => after.columns.get(id) !== was)
    .map(([id]) => id)
  return { edges, columns }
}

describe('pointing at a relationship changes only that relationship and its two rows', () => {
  it('leaves the other 28 lines reading exactly what they read before', () => {
    const board = canvas()
    const highlight = createErdHighlight()
    const before = snapshot(highlight, board)
    highlight.hover(board.edges[7])
    const diff = changed(before, snapshot(highlight, board))
    // the whole of the fix: one line, not twenty-nine
    expect(diff.edges).toEqual(['rel-7'])
  })

  it('lights the two rows the relationship joins, and no others', () => {
    const board = canvas()
    const highlight = createErdHighlight()
    const before = snapshot(highlight, board)
    highlight.hover(board.edges[7])
    const diff = changed(before, snapshot(highlight, board))
    expect(diff.columns.sort()).toEqual([...board.edges[7].columns].sort())
  })

  it('moves the light from one relationship to the next without touching the rest', () => {
    const board = canvas()
    const highlight = createErdHighlight()
    highlight.hover(board.edges[7])
    const before = snapshot(highlight, board)
    highlight.hover(board.edges[8])
    const diff = changed(before, snapshot(highlight, board))
    // two lines change (one goes out, one comes on) and four rows with them
    expect(diff.edges.sort()).toEqual(['rel-7', 'rel-8'])
    expect(diff.columns).toHaveLength(4)
  })
})

describe('an unchanged answer is the SAME value, so nothing re-renders on it', () => {
  it('hands back an interned class string rather than a fresh one each call', () => {
    const highlight = createErdHighlight()
    // `useSyncExternalStore` compares snapshots with Object.is: a template literal
    // built per call would be a new string every time and re-render every edge
    expect(highlight.edgeHighlight('rel-1')).toBe(highlight.edgeHighlight('rel-2'))
    highlight.hover({ id: 'rel-1', columns: ['a', 'b'] })
    expect(highlight.edgeHighlight('rel-1')).toBe(EDGE_HOVERED)
    expect(highlight.edgeHighlight('rel-2')).toBe(EDGE_PLAIN)
  })

  it('reads a relationship that is both pointed at and clicked as both', () => {
    const highlight = createErdHighlight()
    const pick = { id: 'rel-1', columns: ['a', 'b'] }
    highlight.togglePicked(pick)
    expect(highlight.edgeHighlight('rel-1')).toBe(EDGE_PICKED)
    highlight.hover(pick)
    expect(highlight.edgeHighlight('rel-1')).toBe(EDGE_HOVERED_PICKED)
  })
})

describe('nothing wakes the canvas that did not change something', () => {
  it('does not notify when the pointer is still on the same relationship', () => {
    const highlight = createErdHighlight()
    const listener = vi.fn()
    highlight.subscribe(listener)
    highlight.hover({ id: 'rel-1', columns: ['a', 'b'] })
    expect(listener).toHaveBeenCalledTimes(1)
    // React Flow re-fires mouseenter freely; the same line again is not news
    highlight.hover({ id: 'rel-1', columns: ['a', 'b'] })
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('does not notify when the pointer crosses empty canvas with nothing lit', () => {
    const highlight = createErdHighlight()
    const listener = vi.fn()
    highlight.subscribe(listener)
    // the pane asks for this on EVERY pointer move — hundreds a second
    for (let i = 0; i < 200; i++) highlight.clearHover()
    expect(listener).not.toHaveBeenCalled()
  })

  it('does not notify when a new scope arrives with nothing lit on the old one', () => {
    const highlight = createErdHighlight()
    const listener = vi.fn()
    highlight.subscribe(listener)
    highlight.clear()
    highlight.clearPicked()
    expect(listener).not.toHaveBeenCalled()
  })

  it('stops notifying a subscriber that has gone away', () => {
    const highlight = createErdHighlight()
    const listener = vi.fn()
    const unsubscribe = highlight.subscribe(listener)
    unsubscribe()
    highlight.hover({ id: 'rel-1', columns: ['a'] })
    expect(listener).not.toHaveBeenCalled()
  })
})

describe('the behaviour on the canvas is unchanged (#118/#164)', () => {
  const pick = (id: string, columns: string[]) => ({ id, columns })

  it('puts the rows out when the pointer leaves', () => {
    const highlight = createErdHighlight()
    highlight.hover(pick('rel-1', ['a', 'b']))
    highlight.clearHover()
    expect(highlight.isLit('a')).toBe(false)
    expect(highlight.edgeHighlight('rel-1')).toBe(EDGE_PLAIN)
  })

  it('keeps a clicked relationship lit after the pointer has moved on', () => {
    const highlight = createErdHighlight()
    highlight.togglePicked(pick('rel-1', ['a', 'b']))
    highlight.clearHover()
    expect(highlight.isLit('a')).toBe(true)
    expect(highlight.isLit('b')).toBe(true)
  })

  it('puts a clicked relationship out when it is clicked again', () => {
    const highlight = createErdHighlight()
    highlight.togglePicked(pick('rel-1', ['a', 'b']))
    highlight.togglePicked(pick('rel-1', ['a', 'b']))
    expect(highlight.isLit('a')).toBe(false)
    expect(highlight.picked()).toBeNull()
  })

  it('shows both when one is clicked and another is pointed at', () => {
    const highlight = createErdHighlight()
    highlight.togglePicked(pick('rel-1', ['a', 'b']))
    highlight.hover(pick('rel-2', ['c', 'd']))
    expect(['a', 'b', 'c', 'd'].every((id) => highlight.isLit(id))).toBe(true)
  })

  it('counts a shared column once, the way the two lit sets overlap on a hub', () => {
    const highlight = createErdHighlight()
    highlight.togglePicked(pick('rel-1', ['hub::id', 'a::hub_id']))
    highlight.hover(pick('rel-2', ['hub::id', 'b::hub_id']))
    const lit = ['hub::id', 'a::hub_id', 'b::hub_id'].filter((id) => highlight.isLit(id))
    expect(lit).toHaveLength(3)
  })

  it('clears both when the scope or the focus changes', () => {
    const highlight = createErdHighlight()
    highlight.togglePicked(pick('rel-1', ['a', 'b']))
    highlight.hover(pick('rel-2', ['c', 'd']))
    highlight.clear()
    expect(highlight.hovered()).toBeNull()
    expect(highlight.picked()).toBeNull()
    expect(['a', 'b', 'c', 'd'].some((id) => highlight.isLit(id))).toBe(false)
  })

  it('leaves the pointed-at relationship alone when the canvas is clicked', () => {
    // onPaneClick drops the pick only; the pointer is still where it is
    const highlight = createErdHighlight()
    highlight.hover(pick('rel-1', ['a', 'b']))
    highlight.togglePicked(pick('rel-1', ['a', 'b']))
    highlight.clearPicked()
    expect(highlight.edgeHighlight('rel-1')).toBe(EDGE_HOVERED)
  })

  it('survives a subscriber unsubscribing from inside its own notification', () => {
    // a row unmounting because its card left the scope, mid-relight
    const highlight = createErdHighlight()
    const seen: string[] = []
    const off = highlight.subscribe(() => {
      seen.push('first')
      off()
    })
    highlight.subscribe(() => seen.push('second'))
    highlight.hover(pick('rel-1', ['a']))
    expect(seen).toEqual(['first', 'second'])
  })
})
