import { describe, expect, it } from 'vitest'
import {
  defaultScope,
  erdClickHref,
  erdColumnNodeId,
  erdForScope,
  findScope,
  initialScope,
  listScopes,
} from './erd'
import { buildIndex } from './graph'
import { fixtureGraph } from './fixture'

const index = buildIndex(fixtureGraph())

describe('listScopes / defaultScope', () => {
  it('lists schema scopes first, most-connected schema leading', () => {
    const scopes = listScopes(index)
    const schemas = scopes.filter((s) => s.kind === 'schema')
    expect(schemas.map((s) => s.value)).toContain('marts')
    expect(schemas.map((s) => s.value)).toContain('staging')
    // marts has the only relates_to edge, so it must sort first
    expect(schemas[0].value).toBe('marts')
    expect(schemas[0].relationshipCount).toBe(1)
    expect(defaultScope(scopes)?.value).toBe('marts')
  })

  it('exposes dbt tags as scopes', () => {
    const scopes = listScopes(index)
    const tags = scopes.filter((s) => s.kind === 'tag').map((s) => s.value)
    expect(tags).toEqual(expect.arrayContaining(['core', 'finance', 'reporting']))
  })
})

describe('erdForScope', () => {
  it('returns only the scoped models plus relationship targets', () => {
    const scopes = listScopes(index)
    const staging = scopes.find((s) => s.kind === 'schema' && s.value === 'staging')!
    const erd = erdForScope(index, staging)
    expect(erd.models.map((m) => m.node.name)).toEqual(['stg_payments'])
    expect(erd.relationships).toHaveLength(0)
  })

  it('maps relates_to edges to model pairs with column names and validation flag', () => {
    const marts = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
    const erd = erdForScope(index, marts)
    expect(erd.relationships).toHaveLength(1)
    const rel = erd.relationships[0]
    expect(rel.fromModelId).toBe('model.demo.fct_revenue')
    expect(rel.toModelId).toBe('model.demo.dim_users')
    expect(rel.fromColumn).toBe('user_id')
    expect(rel.toColumn).toBe('user_id')
    expect(rel.validated).toBe(true)
  })

  it('marks relationship columns as key columns (always visible)', () => {
    const marts = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
    const erd = erdForScope(index, marts)
    const fct = erd.models.find((m) => m.node.name === 'fct_revenue')!
    expect(fct.keyColumns.has('user_id')).toBe(true)
    expect(fct.external).toBe(false)
  })

  it('scopes by dbt tag', () => {
    const finance = listScopes(index).find((s) => s.kind === 'tag' && s.value === 'finance')!
    const erd = erdForScope(index, finance)
    expect(erd.models.some((m) => m.node.name === 'fct_revenue')).toBe(true)
    // dim_users pulled in as the relationship target, flagged external
    const dim = erd.models.find((m) => m.node.name === 'dim_users')
    expect(dim?.external).toBe(true)
  })
})

describe('initialScope', () => {
  const scopes = listScopes(index)

  it('falls back to the auto-picked scope when nothing is configured', () => {
    for (const configured of [undefined, null, '', '   ']) {
      expect(initialScope(scopes, configured)).toEqual({
        scope: defaultScope(scopes),
        unknownConfigured: null,
      })
    }
  })

  it('opens the configured schema scope', () => {
    const picked = initialScope(scopes, 'schema:staging')
    expect(picked.scope?.kind).toBe('schema')
    expect(picked.scope?.value).toBe('staging')
    expect(picked.unknownConfigured).toBeNull()
  })

  it('opens the configured tag scope', () => {
    const picked = initialScope(scopes, ' tag:finance ')
    expect(picked.scope?.kind).toBe('tag')
    expect(picked.scope?.value).toBe('finance')
  })

  it('falls back and reports the key when the configured scope is not in the graph', () => {
    expect(initialScope(scopes, 'schema:nope')).toEqual({
      scope: defaultScope(scopes),
      unknownConfigured: 'schema:nope',
    })
  })

  it('falls back and reports the key when the configured value is malformed', () => {
    for (const configured of ['marts', 'schemaish:marts', ':marts']) {
      expect(initialScope(scopes, configured)).toEqual({
        scope: defaultScope(scopes),
        unknownConfigured: configured,
      })
    }
  })

  it('findScope only matches its own kind', () => {
    expect(findScope(scopes, 'tag:marts')).toBeNull()
    expect(findScope(scopes, 'schema:core')).toBeNull()
    expect(findScope(scopes, 'schema:marts')?.value).toBe('marts')
  })
})

describe('erdClickHref', () => {
  const marts = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
  const erd = erdForScope(index, marts)
  const fct = erd.models.find((m) => m.node.name === 'fct_revenue')!
  const userId = fct.columns.find((c) => c.name === 'user_id')!

  it('routes a table header click to the model detail panel', () => {
    expect(erdClickHref(fct.node.node_id)).toBe('#/node/model.demo.fct_revenue')
  })

  it('routes a column row click to the column detail panel', () => {
    expect(erdClickHref(userId.node_id)).toBe('#/node/model.demo.fct_revenue%3A%3Auser_id')
  })

  it('routes a modifier-click straight to the lineage view', () => {
    expect(erdClickHref(fct.node.node_id, { metaKey: true })).toBe('#/lineage/model.demo.fct_revenue')
    expect(erdClickHref(userId.node_id, { ctrlKey: true })).toBe('#/lineage/model.demo.fct_revenue%3A%3Auser_id')
  })

  it('builds ids for relationship columns missing from the catalog', () => {
    expect(erdColumnNodeId('model.demo.fct_revenue', 'account_id')).toBe('model.demo.fct_revenue::account_id')
  })
})
