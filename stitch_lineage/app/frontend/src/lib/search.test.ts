import { describe, expect, it } from 'vitest'
import { buildIndex } from './graph'
import { fixtureGraph } from './fixture'
import { GraphSearch, groupHits } from './search'

const index = buildIndex(fixtureGraph())
const search = new GraphSearch(index)

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
