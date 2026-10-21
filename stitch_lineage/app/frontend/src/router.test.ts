import { describe, expect, it } from 'vitest'
import { coverageHref, erdHref, lineageHref, nodeHref, parseHash } from './router'

describe('parseHash', () => {
  it('routes the basics', () => {
    expect(parseHash('')).toEqual({ page: 'home' })
    expect(parseHash('#/')).toEqual({ page: 'home' })
    expect(parseHash('#/node/model.demo.fct_revenue')).toEqual({
      page: 'node',
      nodeId: 'model.demo.fct_revenue',
    })
    expect(parseHash('#/erd')).toEqual({ page: 'erd' })
    expect(parseHash('#/erd/schema/marts')).toEqual({
      page: 'erd',
      scopeKind: 'schema',
      scopeValue: 'marts',
    })
  })

  it('defaults lineage to column grain and reads the table grain off the path', () => {
    expect(parseHash('#/lineage/model.demo.fct_revenue')).toEqual({
      page: 'lineage',
      nodeId: 'model.demo.fct_revenue',
      grain: 'column',
    })
    expect(parseHash('#/lineage/model.demo.fct_revenue/table')).toEqual({
      page: 'lineage',
      nodeId: 'model.demo.fct_revenue',
      grain: 'table',
    })
    // an unknown grain is column, not a broken page
    expect(parseHash('#/lineage/x/sideways')).toEqual({ page: 'lineage', nodeId: 'x', grain: 'column' })
  })

  it('sends the retired map route home rather than nowhere (#83)', () => {
    expect(parseHash('#/overview')).toEqual({ page: 'home' })
  })

  it('routes the coverage lists', () => {
    expect(parseHash('#/coverage/unbound-models')).toEqual({
      page: 'coverage',
      kind: 'unbound-models',
    })
    expect(parseHash('#/coverage/untraced-columns')).toEqual({
      page: 'coverage',
      kind: 'untraced-columns',
    })
    // an unknown list falls home rather than rendering an empty panel
    expect(parseHash('#/coverage/made-up')).toEqual({ page: 'home' })
  })

  it.each(['#/node/%', '#/lineage/%E0%A4%A', '#/erd/schema/%FF', '#/coverage/%2', '#/%'])('handles malformed URI encoding in %s', (hash) => {
    expect(parseHash(hash)).toEqual({ page: 'not-found' })
    // A bad initial or hashchange route must not prevent the next valid route.
    expect(parseHash('#/node/model.demo.orders')).toEqual({ page: 'node', nodeId: 'model.demo.orders' })
  })

  it('keeps valid escaped percent signs, slashes and Unicode in ids', () => {
    const id = 'model.demo.100%/café::amount'
    expect(parseHash(nodeHref(id))).toEqual({ page: 'node', nodeId: id })
  })

  it('round-trips ids that need escaping', () => {
    const id = 'model.demo.fct_revenue::net_revenue'
    expect(parseHash(nodeHref(id))).toEqual({ page: 'node', nodeId: id })
    expect(parseHash(lineageHref(id))).toEqual({ page: 'lineage', nodeId: id, grain: 'column' })
    expect(parseHash(lineageHref(id, 'table'))).toEqual({ page: 'lineage', nodeId: id, grain: 'table' })
    expect(parseHash(erdHref('schema', 'my schema'))).toEqual({
      page: 'erd',
      scopeKind: 'schema',
      scopeValue: 'my schema',
    })
    expect(parseHash(coverageHref('unresolved-cards'))).toEqual({
      page: 'coverage',
      kind: 'unresolved-cards',
    })
  })
})
