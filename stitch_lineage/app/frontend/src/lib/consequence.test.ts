import { describe, expect, it } from 'vitest'
import { buildConsequenceIndex, consequenceLabel, type Consequence } from './consequence'
import { fixtureGraph } from './fixture'
import { buildIndex } from './graph'
import type { GraphEdge, GraphNode, StitchGraph } from '../types'

const index = buildIndex(fixtureGraph())
const consequence = buildConsequenceIndex(index)
const M = 'model.demo'
const S = 'source.demo'

const of = (nodeId: string) => consequence.of(nodeId)

describe('buildConsequenceIndex', () => {
  it('counts the cards and dashboards below a column', () => {
    // net_revenue -> mb_field::101 -> cards 412 and 418 -> both on dash 7
    expect(of(`${M}.fct_revenue::net_revenue`)).toEqual({ cards: 2, dashboards: 1 })
  })

  it('counts each dashboard once, however many cards reach it', () => {
    // the reason this needs set union rather than addition: two cards, one dashboard
    expect(of(`${M}.fct_revenue::net_revenue`).dashboards).toBe(1)
  })

  it('carries the count the whole way up the chain', () => {
    // the same two cards are the consequence of every column that feeds them
    expect(of(`${M}.stg_payments::amount`)).toEqual({ cards: 2, dashboards: 1 })
    expect(of(`${S}.app.events::amount`)).toEqual({ cards: 2, dashboards: 1 })
  })

  it('gives a model its columns’ fan-out, not its own (it has no flow edge)', () => {
    // a model node has no outgoing edge at all; counting only its own would report
    // "no cards" for every model in the graph
    expect(of(`${M}.fct_revenue`)).toEqual({ cards: 2, dashboards: 1 })
    expect(of(`${S}.app.events`)).toEqual({ cards: 2, dashboards: 1 })
  })

  it('answers zero for a column nothing consumes', () => {
    // user_id binds to a field no card reads
    expect(of(`${M}.fct_revenue::user_id`)).toEqual({ cards: 0, dashboards: 0 })
    expect(of(`${M}.mart_board::net_revenue`)).toEqual({ cards: 0, dashboards: 0 })
  })

  it('never counts a node as its own consequence', () => {
    // a card's answer is the dashboards it sits on, not "1 card"
    expect(of('mb_card::412')).toEqual({ cards: 0, dashboards: 1 })
    expect(of('mb_dash::7')).toEqual({ cards: 0, dashboards: 0 })
  })

  it('counts a field the same as the column bound to it', () => {
    expect(of('mb_field::101')).toEqual({ cards: 2, dashboards: 1 })
  })

  it('is zero, not a guess, for an id the graph does not have', () => {
    expect(of('model.demo.nope::gone')).toEqual({ cards: 0, dashboards: 0 })
  })

  it('ignores relates_to, which is a declaration and not data flow', () => {
    // fct_revenue.user_id relates_to dim_users.user_id in the fixture; if that edge
    // were walked, a relationship would forge a consequence chain that no query has
    expect(of(`${M}.dim_users::user_id`).cards).toBe(0)
  })
})

// --- shapes the real graph has and the fixture does not ----------------------

function graphOf(nodes: GraphNode[], edges: GraphEdge[]): StitchGraph {
  return { schema_version: 1, nodes, edges } as StitchGraph
}

const col = (id: string): GraphNode => ({
  node_id: id,
  node_type: 'column',
  name: id.split('::')[1] ?? id,
  properties: {},
})
const card = (id: string): GraphNode => ({
  node_id: id,
  node_type: 'mb_card',
  name: id,
  properties: {},
})
const dash = (id: string): GraphNode => ({
  node_id: id,
  node_type: 'mb_dashboard',
  name: id,
  properties: {},
})
const flow = (from: string, to: string): GraphEdge =>
  ({ from, to, edge_type: 'feeds', confidence: 'exact', evidence: {} }) as GraphEdge

describe('buildConsequenceIndex — graph shapes', () => {
  it('counts a diamond once, not twice', () => {
    // a -> b, a -> c, both -> the same card: one card, not two
    const built = buildConsequenceIndex(
      buildIndex(
        graphOf([col('m::a'), col('m::b'), col('m::c'), card('mb_card::1')], [
          flow('m::a', 'm::b'),
          flow('m::a', 'm::c'),
          flow('m::b', 'mb_card::1'),
          flow('m::c', 'mb_card::1'),
        ]),
      ),
    )
    expect(built.of('m::a')).toEqual({ cards: 1, dashboards: 0 })
  })

  it('survives a cycle instead of hanging the page load', () => {
    // dbt lineage is a DAG, so this is a guard rather than a case: what matters is
    // that a malformed graph still loads, and the card below the cycle is still found
    const built = buildConsequenceIndex(
      buildIndex(
        graphOf([col('m::a'), col('m::b'), card('mb_card::1')], [
          flow('m::a', 'm::b'),
          flow('m::b', 'm::a'),
          flow('m::b', 'mb_card::1'),
        ]),
      ),
    )
    expect(built.of('m::b')).toEqual({ cards: 1, dashboards: 0 })
  })

  it('handles a long chain without recursing over it', () => {
    // 5,000 deep: a recursive walk would overflow the stack here
    const nodes: GraphNode[] = [card('mb_card::1')]
    const edges: GraphEdge[] = []
    for (let i = 0; i < 5000; i++) nodes.push(col(`m::c${i}`))
    for (let i = 0; i < 4999; i++) edges.push(flow(`m::c${i}`, `m::c${i + 1}`))
    edges.push(flow('m::c4999', 'mb_card::1'))
    const built = buildConsequenceIndex(buildIndex(graphOf(nodes, edges)))
    expect(built.of('m::c0')).toEqual({ cards: 1, dashboards: 0 })
  })

  it('keeps dashboards in their own bits, past however many card words there are', () => {
    // 40 cards spans two card words, so the dashboard bits start at an offset that
    // is not a round word — the one piece of arithmetic in here worth pinning
    const nodes: GraphNode[] = [col('m::a')]
    const edges: GraphEdge[] = []
    for (let i = 0; i < 40; i++) {
      nodes.push(card(`mb_card::${i}`), dash(`mb_dash::${i}`))
      edges.push(flow('m::a', `mb_card::${i}`), flow(`mb_card::${i}`, `mb_dash::${i}`))
    }
    const built = buildConsequenceIndex(buildIndex(graphOf(nodes, edges)))
    expect(built.of('m::a')).toEqual({ cards: 40, dashboards: 40 })
    expect(built.of('mb_card::39')).toEqual({ cards: 0, dashboards: 1 })
  })

  it('counts past 32 cards, where one bitset word runs out', () => {
    const nodes: GraphNode[] = [col('m::a')]
    const edges: GraphEdge[] = []
    for (let i = 0; i < 100; i++) {
      nodes.push(card(`mb_card::${i}`))
      edges.push(flow('m::a', `mb_card::${i}`))
    }
    const built = buildConsequenceIndex(buildIndex(graphOf(nodes, edges)))
    expect(built.of('m::a')).toEqual({ cards: 100, dashboards: 0 })
  })

  it('answers zero everywhere in a graph with no BI side at all', () => {
    const built = buildConsequenceIndex(
      buildIndex(graphOf([col('m::a'), col('m::b')], [flow('m::a', 'm::b')])),
    )
    expect(built.of('m::a')).toEqual({ cards: 0, dashboards: 0 })
  })
})

describe('consequenceLabel', () => {
  const counts = (cards: number, dashboards: number): Consequence => ({ cards, dashboards })
  const node = (node_type: GraphNode['node_type']): GraphNode => ({
    node_id: 'x',
    node_type,
    name: 'x',
    properties: {},
  })

  it('reads as the counts, cards first', () => {
    expect(consequenceLabel(node('column'), counts(4, 2))).toBe('4 cards · 2 dashboards')
    expect(consequenceLabel(node('model'), counts(1, 1))).toBe('1 card · 1 dashboard')
  })

  it('says so when nothing depends on a dbt node', () => {
    // "nothing depends on this" is an answer the reader came for, not a blank
    expect(consequenceLabel(node('column'), counts(0, 0))).toBe('no cards')
  })

  it('drops the dashboards when there are none', () => {
    expect(consequenceLabel(node('column'), counts(3, 0))).toBe('3 cards')
  })

  it('gives a card its dashboards and never "1 card"', () => {
    expect(consequenceLabel(node('mb_card'), counts(0, 2))).toBe('2 dashboards')
    // a card on no dashboard has nothing to report rather than a zero
    expect(consequenceLabel(node('mb_card'), counts(0, 0))).toBeNull()
  })

  it('says nothing at all on a dashboard, which has no downstream', () => {
    expect(consequenceLabel(node('mb_dashboard'), counts(0, 0))).toBeNull()
  })
})
