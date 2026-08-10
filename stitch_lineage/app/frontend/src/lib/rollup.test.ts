import { describe, expect, it } from 'vitest'
import { fixtureGraph } from './fixture'
import { buildIndex } from './graph'
import { lineageFor } from './lineage'
import { entityIdOf, layerEntities, rollUp } from './rollup'

const graph = fixtureGraph()
const index = buildIndex(graph)
const all = rollUp(index, index.nodes, graph.edges)
const edge = (from: string, to: string) => all.edges.find((e) => e.from === from && e.to === to)

describe('entityIdOf', () => {
  it('sends a column to the model that owns it', () => {
    expect(entityIdOf(index.nodesById.get('model.demo.fct_revenue::net_revenue')!)).toBe(
      'model.demo.fct_revenue',
    )
  })

  it('keeps models, sources, cards and dashboards as themselves', () => {
    for (const id of ['model.demo.fct_revenue', 'source.demo.app.events', 'mb_card::412', 'mb_dash::7']) {
      expect(entityIdOf(index.nodesById.get(id)!)).toBe(id)
    }
  })

  it('makes an mb_field transparent — a field is a waypoint, not a place', () => {
    expect(entityIdOf(index.nodesById.get('mb_field::101')!)).toBeNull()
  })
})

describe('rollUp', () => {
  it('keeps exactly the entity nodes, with their column counts', () => {
    const ids = all.nodes.map((n) => n.node.node_id)
    expect(ids).toContain('model.demo.fct_revenue')
    expect(ids).toContain('mb_card::412')
    expect(ids.every((id) => index.nodesById.get(id)?.node_type !== 'column')).toBe(true)
    expect(ids).not.toContain('mb_field::101')
    const fct = all.nodes.find((n) => n.node.node_id === 'model.demo.fct_revenue')!
    expect(fct.memberCount).toBe(2) // net_revenue + user_id
  })

  it('contracts column -> field -> card into one model -> card edge', () => {
    const rolled = edge('model.demo.fct_revenue', 'mb_card::412')
    expect(rolled).toBeDefined()
    expect(rolled!.weight).toBe(1) // one contributing column: net_revenue
    expect(rolled!.confidence).toBe('exact')
  })

  it('carries the weakest hop of the contracted path, not the first hop', () => {
    // net_revenue -binds_to(exact)-> field -consumed_by(parsed)-> card 418
    expect(edge('model.demo.fct_revenue', 'mb_card::418')!.confidence).toBe('parsed')
  })

  it('takes the best confidence across contributing paths', () => {
    // stg_payments -> fct_revenue has an exact reference edge and a parsed column edge
    const rolled = edge('model.demo.stg_payments', 'model.demo.fct_revenue')!
    expect(rolled.confidence).toBe('exact')
    expect(rolled.declared).toBe(true)
    expect(rolled.weight).toBe(2) // amount and the star pseudo-column both feed across
  })

  it('never emits a self-loop for columns inside one model', () => {
    expect(all.edges.every((e) => e.from !== e.to)).toBe(true)
  })

  it('drops relates_to, which is a declaration and not flow', () => {
    // fct_revenue.user_id relates_to dim_users.user_id — the only link between them
    expect(edge('model.demo.fct_revenue', 'model.demo.dim_users')).toBeUndefined()
  })

  it('only follows edges inside the slice it is given', () => {
    const lineage = lineageFor(index, 'model.demo.fct_revenue::net_revenue')
    const rolled = rollUp(index, lineage.nodes, lineage.edges)
    const ids = new Set(rolled.nodes.map((n) => n.node.node_id))
    expect(ids.has('model.demo.fct_revenue')).toBe(true)
    expect(ids.has('mb_dash::7')).toBe(true)
    // dim_users is only reachable over relates_to, which lineage never walks
    expect(ids.has('model.demo.dim_users')).toBe(false)
    expect(rolled.edges.every((e) => ids.has(e.from) && ids.has(e.to))).toBe(true)
  })
})

describe('layerEntities', () => {
  it('lays a chain out strictly left to right', () => {
    const layers = layerEntities(
      all.nodes.map((n) => n.node.node_id),
      all.edges,
    )
    expect(layers.get('source.demo.app.events')).toBe(0)
    const stg = layers.get('model.demo.stg_payments') as number
    const fct = layers.get('model.demo.fct_revenue') as number
    const card = layers.get('mb_card::412') as number
    const dash = layers.get('mb_dash::7') as number
    expect(stg).toBeLessThan(fct)
    expect(fct).toBeLessThan(card)
    expect(card).toBeLessThan(dash)
  })

  it('terminates on a cycle instead of spinning', () => {
    const layers = layerEntities(
      ['a', 'b'],
      [
        { from: 'a', to: 'b' },
        { from: 'b', to: 'a' },
      ],
    )
    expect(layers.size).toBe(2)
  })
})
