// How much depends on a node: the distinct Metabase cards and dashboards
// downstream of it (#115). Pure TS, unit-tested.
//
// Search results have to carry consequence — "4 cards · 2 dashboards" — so the
// choice is made in the dropdown rather than by opening every hit (design
// principle 04). The obvious implementation is a `reach()` walk per visible hit,
// which is why #114 left this out: forty walks over a 6,300-node graph, redone on
// every keystroke.
//
// So it is one pass instead, over the whole graph, once at load. Each node gets a
// BITSET of the cards and dashboards below it, unioned up from its successors in
// reverse topological order; the counts are the popcounts. Two things fall out of
// that choice:
//
//   * the counts are EXACT. A capped walk would have to say "299+ cards" and mark
//     it amber (the #114 convention for a truncated count stated as fact); there is
//     no cap here, so there is nothing to qualify.
//   * the cost is one linear pass, not one per hit per keystroke. On the reference
//     Smitten graph (6,305 nodes / 10,698 edges) that is a few milliseconds at
//     load, and every keystroke afterwards is a map lookup.
//
// A bitset is what makes the exactness affordable: distinct counts need set union,
// and ~950 cards + ~60 dashboards fit in 32 words per node — under a megabyte for
// the whole graph, in one flat allocation that is dropped once the counts are in.

import { isFlowEdge, type GraphIndex } from './graph'
import type { GraphNode } from '../types'

export interface Consequence {
  /** Distinct Metabase cards downstream. Never counts the node itself. */
  cards: number
  /** Distinct dashboards those cards sit on. */
  dashboards: number
}

const NONE: Consequence = { cards: 0, dashboards: 0 }

export interface ConsequenceIndex {
  /** Exact downstream counts. Zeroes for an unknown id — never a guess. */
  of(nodeId: string): Consequence
}

/** 32 bits per word, the width of a Uint32Array lane. */
const WORD = 32

/**
 * One pass over the graph, bottom up: every node's downstream card and dashboard
 * counts.
 *
 * Iterative post-order DFS rather than recursion — a deep chain must not depend on
 * the JS stack — with an in-progress guard so a cycle cannot hang the load. dbt
 * lineage is a DAG, so the guard should never fire; if it ever did, the affected
 * node's count would be low rather than wrong-shaped, and no other node's would
 * change.
 */
export function buildConsequenceIndex(index: GraphIndex): ConsequenceIndex {
  const nodes = index.nodes
  const row = new Map<string, number>()
  for (let i = 0; i < nodes.length; i++) row.set(nodes[i].node_id, i)

  // Bit slots: cards first, then dashboards, so one union loop covers both.
  const cardSlot = new Map<string, number>()
  const dashSlot = new Map<string, number>()
  for (const node of nodes) {
    if (node.node_type === 'mb_card') cardSlot.set(node.node_id, cardSlot.size)
    else if (node.node_type === 'mb_dashboard') dashSlot.set(node.node_id, dashSlot.size)
  }
  const cardWords = Math.ceil(cardSlot.size / WORD)
  const dashWords = Math.ceil(dashSlot.size / WORD)
  const stride = cardWords + dashWords

  const counts = new Map<string, Consequence>()
  if (stride === 0) {
    // no BI side in this graph at all: every answer is zero, and saying so is free
    return { of: () => NONE }
  }

  const bits = new Uint32Array(nodes.length * stride)
  const DONE = 2
  const OPEN = 1
  const state = new Uint8Array(nodes.length)

  /** The node's own slot, set on itself so a parent's union picks it up. */
  const markSelf = (node: GraphNode, at: number) => {
    const card = cardSlot.get(node.node_id)
    if (card !== undefined) bits[at + (card >>> 5)] |= 1 << (card & 31)
    const dash = dashSlot.get(node.node_id)
    if (dash !== undefined) {
      const bit = cardWords * WORD + dash
      bits[at + (bit >>> 5)] |= 1 << (bit & 31)
    }
  }

  const successors = (nodeId: string): number[] => {
    const out: number[] = []
    for (const edge of index.outEdges.get(nodeId) ?? []) {
      if (!isFlowEdge(edge)) continue
      const target = row.get(edge.to)
      if (target !== undefined) out.push(target)
    }
    return out
  }

  for (let start = 0; start < nodes.length; start++) {
    if (state[start] === DONE) continue
    const stack: number[] = [start]
    while (stack.length > 0) {
      const i = stack[stack.length - 1]
      if (state[i] === DONE) {
        stack.pop()
        continue
      }
      if (state[i] === OPEN) {
        // second visit: every successor is finished, so fold them in
        stack.pop()
        const at = i * stride
        for (const j of successors(nodes[i].node_id)) {
          if (state[j] !== DONE) continue // a cycle's back edge; see the note above
          const from = j * stride
          for (let w = 0; w < stride; w++) bits[at + w] |= bits[from + w]
        }
        markSelf(nodes[i], at)
        state[i] = DONE
        continue
      }
      state[i] = OPEN
      for (const j of successors(nodes[i].node_id)) {
        if (state[j] === 0) stack.push(j)
      }
    }
  }

  // A model has no flow edge of its own: the BI fan-out leaves through its COLUMNS
  // (lib/details makes the same point before walking them one by one). So a model
  // node's consequence is the union of its columns', or every model would report
  // "no cards" — a confident wrong answer about the node the reader searched for.
  // Safe as a second pass: every column is DONE by now.
  for (const [modelId, columns] of index.columnsByModel) {
    const at = (row.get(modelId) ?? -1) * stride
    if (at < 0) continue
    for (const column of columns) {
      const from = (row.get(column.node_id) ?? -1) * stride
      if (from < 0) continue
      for (let w = 0; w < stride; w++) bits[at + w] |= bits[from + w]
    }
  }

  // Popcount once per node; the bitsets are scratch and go out of scope with it.
  for (let i = 0; i < nodes.length; i++) {
    const at = i * stride
    let cards = 0
    let dashboards = 0
    for (let w = 0; w < cardWords; w++) cards += popcount(bits[at + w])
    for (let w = cardWords; w < stride; w++) dashboards += popcount(bits[at + w])
    // a node never counts as its own consequence: a card's answer is the dashboards
    // it is on, not "1 card"
    if (nodes[i].node_type === 'mb_card') cards -= 1
    else if (nodes[i].node_type === 'mb_dashboard') dashboards -= 1
    if (cards > 0 || dashboards > 0) counts.set(nodes[i].node_id, { cards, dashboards })
  }

  return { of: (nodeId) => counts.get(nodeId) ?? NONE }
}

/** Hamming weight of a 32-bit word (the standard SWAR sequence). */
function popcount(word: number): number {
  let n = word - ((word >>> 1) & 0x55555555)
  n = (n & 0x33333333) + ((n >>> 2) & 0x33333333)
  n = (n + (n >>> 4)) & 0x0f0f0f0f
  return (n * 0x01010101) >>> 24
}

/**
 * "4 cards · 2 dashboards" — what a search hit carries (#115).
 *
 * Null when there is nothing to say, which is not the same as zero: a dashboard has
 * no downstream at all, so a "0 cards" on one would be an answer to a question
 * nobody asked. A dbt node with no cards below it DOES get an answer, because
 * "nothing depends on this" is exactly what the reader is looking for.
 */
export function consequenceLabel(node: GraphNode, counts: Consequence): string | null {
  if (node.node_type === 'mb_dashboard') return null
  const parts: string[] = []
  if (node.node_type !== 'mb_card') {
    parts.push(counts.cards === 0 ? 'no cards' : plural(counts.cards, 'card'))
  }
  if (counts.dashboards > 0) parts.push(plural(counts.dashboards, 'dashboard'))
  return parts.length > 0 ? parts.join(' · ') : null
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}
