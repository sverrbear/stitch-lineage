import { afterEach, describe, expect, it } from 'vitest'
import { buildIndex } from './graph'
import { fixtureGraph } from './fixture'
import { GraphSearch, groupHits } from './search'
import { setStripModelPrefixes } from './present'

const index = buildIndex(fixtureGraph())
const search = new GraphSearch(index)

afterEach(() => setStripModelPrefixes([]))

describe('qualified column search', () => {
  it.each([
    'fct_revenue.net_revenue',
    ' FCT_REVENUE.NET_REVENUE ',
    'model.demo.fct_revenue.net_revenue',
    'model.demo.fct_revenue::net_revenue',
  ])('finds the exact column before fuzzy model matches for %s', (query) => {
    const hits = search.search(query)
    expect(hits[0].node.node_id).toBe('model.demo.fct_revenue::net_revenue')
    expect(hits[0].tier).toBe(6)
    // This is the order used by both Home and the keyboard command palette.
    expect(groupHits(hits).flatMap((group) => group.hits)[0]).toBe(hits[0])
  })

  it('supports typing the column after a model name', () => {
    const hits = search.search('fct_revenue.net_')
    expect(hits[0].node.node_id).toBe('model.demo.fct_revenue::net_revenue')
    expect(hits[0].tier).toBe(4)
  })

  it('moves the exact column group ahead of an actual fuzzy model result', () => {
    const graph = fixtureGraph()
    graph.nodes.push(
      { node_id: 'model.demo.viz_customer_profiles', node_type: 'model', name: 'viz_customer_profiles', properties: {} },
      { node_id: 'model.demo.viz_customer_profiles::region', node_type: 'column', name: 'region', properties: {} },
    )
    const hits = new GraphSearch(buildIndex(graph)).search('viz_customer_profiles.region')
    expect(hits.some((hit) => hit.node.node_type === 'model' && hit.tier === 1)).toBe(true)
    expect(groupHits(hits)[0].hits[0].node.node_id).toBe('model.demo.viz_customer_profiles::region')
  })

  it('accepts canonical and displayed model names when a prefix is hidden', () => {
    setStripModelPrefixes(['fct_'])
    const prefixed = new GraphSearch(index)
    for (const query of ['fct_revenue.net_revenue', 'revenue.net_revenue']) {
      expect(prefixed.search(query)[0].node.node_id).toBe('model.demo.fct_revenue::net_revenue')
      expect(prefixed.search(query)[0].tier).toBe(6)
    }
  })

  it('retains ambiguous matches from different packages and allows full qualification', () => {
    const graph = fixtureGraph()
    graph.nodes.push({ node_id: 'model.other.fct_revenue::net_revenue', node_type: 'column', name: 'net_revenue', properties: {} })
    const ambiguous = new GraphSearch(buildIndex(graph))
    expect(ambiguous.search('fct_revenue.net_revenue').filter((hit) => hit.tier === 6).map((hit) => hit.node.node_id)).toEqual([
      'model.demo.fct_revenue::net_revenue', 'model.other.fct_revenue::net_revenue',
    ])
    // The second column has no parent model node: its ID still supplies the owner.
    expect(ambiguous.search('model.other.fct_revenue::net_revenue')[0].node.node_id).toBe('model.other.fct_revenue::net_revenue')
  })

  it('finds source columns without inventing matches for nonexistent columns', () => {
    expect(search.search('events.amount')[0].node.node_id).toBe('source.demo.app.events::amount')
    expect(search.search('fct_revenue.missing').some((hit) => hit.tier === 6)).toBe(false)
  })
})

describe('GraphSearch ranking', () => {
  it('ranks exact name above prefix above word-boundary', () => {
    const hits = search.search('net_revenue')
    expect(hits.length).toBeGreaterThan(0)
    // Exact name matches first…
    expect(hits[0].node.name).toBe('net_revenue')
    expect(hits[0].tier).toBe(5)
    // …and every hit is at least as strong as the one after it.
    for (let i = 1; i < hits.length; i++) {
      expect(hits[i - 1].tier).toBeGreaterThanOrEqual(hits[i].tier)
    }
  })

  it('prefix beats substring', () => {
    const hits = search.search('rev')
    const prefixHit = hits.find((h) => h.node.name === 'Revenue by country')
    const substringHit = hits.find((h) => h.node.name === 'fct_revenue')
    expect(prefixHit).toBeDefined()
    expect(substringHit).toBeDefined()
    expect(hits.indexOf(prefixHit!)).toBeLessThan(hits.indexOf(substringHit!))
    expect(prefixHit!.tier).toBe(4)
    // 'revenue' starts at a word boundary in 'fct_revenue'
    expect(substringHit!.tier).toBe(3)
  })

  it('matches descriptions and tags as weaker substring hits', () => {
    const byDescription = search.search('refunds')
    expect(byDescription.some((h) => h.node.node_id.endsWith('::net_revenue'))).toBe(true)
    expect(byDescription[0].matchedField).toBe('description')

    const byTag = search.search('finance')
    expect(byTag.some((h) => h.node.name === 'fct_revenue')).toBe(true)
    expect(byTag[0].matchedField).toBe('properties.tags')
  })

  it('matches collection titles for cards', () => {
    const hits = search.search('board')
    const card = hits.find((h) => h.node.node_id === 'mb_card::412')
    expect(card).toBeDefined()
    // The dashboard named "Board dashboard" must outrank the collection-substring card hit.
    const dash = hits.find((h) => h.node.node_id === 'mb_dash::7')
    expect(dash).toBeDefined()
    expect(hits.indexOf(dash!)).toBeLessThan(hits.indexOf(card!))
  })

  it('falls back to fuzzy for misspellings', () => {
    const hits = search.search('revenu by cuntry')
    expect(hits.some((h) => h.node.name === 'Revenue by country')).toBe(true)
    expect(hits[0].tier).toBe(1)
  })

  it('provides context: model name for columns, collection for cards', () => {
    const column = search.search('net_revenue').find((h) => h.node.node_id === 'model.demo.fct_revenue::net_revenue')
    expect(column?.context).toBe('fct_revenue')
    const card = search.search('weekly').find((h) => h.node.node_type === 'mb_card')
    expect(card?.context).toBe('Board')
  })

  it('does not surface synthesized placeholder nodes', () => {
    // 'stg_payments::*' exists only as a dangling edge endpoint.
    const hits = search.search('*')
    expect(hits.every((h) => h.node.properties?.synthetic !== true)).toBe(true)
  })

  it('groups results by node type in fixed order, preserving rank inside groups', () => {
    const groups = groupHits(search.search('revenue'))
    const types = groups.map((g) => g.type)
    const sorted = [...types].sort(
      (a, b) =>
        ['model', 'column', 'mb_card', 'mb_dashboard', 'source', 'mb_field'].indexOf(a) -
        ['model', 'column', 'mb_card', 'mb_dashboard', 'source', 'mb_field'].indexOf(b),
    )
    expect(types).toEqual(sorted)
    for (const group of groups) {
      for (let i = 1; i < group.hits.length; i++) {
        expect(group.hits[i - 1].tier).toBeGreaterThanOrEqual(group.hits[i].tier)
      }
    }
  })

  it('returns nothing for empty queries', () => {
    expect(search.search('')).toEqual([])
    expect(search.search('   ')).toEqual([])
  })
})
