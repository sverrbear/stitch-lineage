import { describe, expect, it } from 'vitest'
import {
  AUTO_EXPAND_MAX_MODELS,
  MAX_DRAWN_SUGGESTIONS,
  autoExpandedModels,
  cardinalityMarkers,
  resolveStaged,
  defaultScope,
  erdClickHref,
  erdColumnNodeId,
  erdForScope,
  findScope,
  initialScope,
  listScopes,
  scopeModelIds,
  suggestionsInScope,
  visibleColumns,
} from './erd'
import { buildIndex } from './graph'
import { fixtureGraph } from './fixture'

const index = buildIndex(fixtureGraph())
const erd0Columns = (model: { columns: Array<{ key: string }> }) => model.columns.map((c) => c.key)

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
    expect(scopes.filter((s) => s.kind === 'tag').every((s) => !s.internal)).toBe(true)
  })

  it('flags package and warehouse-internal schemas, and sorts them last', () => {
    const schemas = listScopes(index).filter((s) => s.kind === 'schema')
    const internal = schemas.filter((s) => s.internal).map((s) => s.value)
    // elementary: an installed package's own schema. artifacts: tooling bookkeeping.
    expect(internal).toEqual(expect.arrayContaining(['elementary', 'artifacts']))
    expect(internal).not.toContain('marts')
    expect(internal).not.toContain('staging')
    const firstInternal = schemas.findIndex((s) => s.internal)
    expect(schemas.slice(firstInternal).every((s) => s.internal)).toBe(true)
  })

  it('never auto-opens an internal schema', () => {
    expect(defaultScope(listScopes(index))?.internal).toBe(false)
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

  it('marks relationship columns as key columns and sorts them first', () => {
    const marts = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
    const erd = erdForScope(index, marts)
    const fct = erd.models.find((m) => m.node.name === 'fct_revenue')!
    expect(fct.columns[0].key).toBe('user_id')
    expect(fct.columns[0].isKey).toBe(true)
    expect(fct.columns.find((c) => c.key === 'net_revenue')?.isKey).toBe(false)
    expect(fct.external).toBe(false)
  })

  it('keys columns on the dbt id tail, not the display name', () => {
    // ALERT_ID is the warehouse spelling; edges and handles speak alert_id.
    const internal = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'elementary')!
    const erd = erdForScope(index, internal)
    const column = erd.models[0].columns[0]
    expect(column.key).toBe('alert_id')
    expect(column.name).toBe('ALERT_ID')
    expect(column.nodeId).toBe('model.elementary.alerts_anomaly_detection::alert_id')
  })

  it('gives a relationship column the catalog never had a row of its own', () => {
    const graph = fixtureGraph()
    graph.edges.push({
      from: 'model.demo.fct_revenue::account_id',
      to: 'model.demo.dim_users::user_id',
      edge_type: 'relates_to',
      confidence: 'declared',
      evidence: {},
    })
    const phantomIndex = buildIndex(graph)
    const marts = listScopes(phantomIndex).find((s) => s.kind === 'schema' && s.value === 'marts')!
    const fct = erdForScope(phantomIndex, marts).models.find((m) => m.node.name === 'fct_revenue')!
    const account = fct.columns.find((c) => c.key === 'account_id')!
    // buildIndex synthesized the endpoint, so the edge still lands somewhere
    expect(account.isKey).toBe(true)
    expect(account.phantom).toBe(true)
    expect(erd0Columns(fct)).toContain('account_id')
  })
})

describe('visibleColumns', () => {
  const marts = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
  const fct = erdForScope(index, marts).models.find((m) => m.node.name === 'fct_revenue')!

  it('never hides a key column, even under the budget', () => {
    const shown = visibleColumns(fct, false, 1)
    expect(shown.map((c) => c.key)).toEqual(['user_id'])
  })

  it('shows everything when expanded', () => {
    expect(visibleColumns(fct, true, 1)).toHaveLength(fct.columns.length)
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
  const userId = fct.columns.find((c) => c.key === 'user_id')!

  it('routes a table header click to the model detail panel', () => {
    expect(erdClickHref(fct.node.node_id)).toBe('#/node/model.demo.fct_revenue')
  })

  it('routes a column row click to the column detail panel', () => {
    expect(erdClickHref(userId.nodeId)).toBe('#/node/model.demo.fct_revenue%3A%3Auser_id')
  })

  it('routes a modifier-click straight to the lineage view', () => {
    expect(erdClickHref(fct.node.node_id, { metaKey: true })).toBe('#/lineage/model.demo.fct_revenue')
    expect(erdClickHref(userId.nodeId, { ctrlKey: true })).toBe('#/lineage/model.demo.fct_revenue%3A%3Auser_id')
  })

  it('builds ids for relationship columns missing from the catalog', () => {
    expect(erdColumnNodeId('model.demo.fct_revenue', 'account_id')).toBe('model.demo.fct_revenue::account_id')
  })
})

describe('staged relationships on the canvas', () => {
  const staged = [
    {
      id: 'staged-1',
      from_model: 'fct_revenue',
      from_column: 'user_id',
      to_model: 'dim_users',
      to_column: 'user_id',
      cardinality: 'many-to-one',
    },
    {
      id: 'staged-2',
      from_model: 'stg_payments',
      from_column: 'amount',
      to_model: 'gone_model',
      to_column: 'amount',
      cardinality: 'one-to-one',
    },
  ]

  it('resolves dbt model names onto node ids, reporting the ones it cannot place', () => {
    const { drawable, unresolvedIds } = resolveStaged(index, staged)
    expect(drawable).toHaveLength(1)
    expect(drawable[0]).toMatchObject({
      id: 'staged-1',
      fromModelId: 'model.demo.fct_revenue',
      toModelId: 'model.demo.dim_users',
      fromColumn: 'user_id',
    })
    // never silently dropped: the user would meet it again at `stitch apply`
    expect(unresolvedIds).toEqual(['staged-2'])
  })

  it('pins a staged endpoint as a key column so its edge has a handle to land on', () => {
    const drawable = resolveStaged(index, [
      {
        id: 'staged-3',
        from_model: 'fct_revenue',
        from_column: 'net_revenue',
        to_model: 'mart_board',
        to_column: 'net_revenue',
        cardinality: 'one-to-one',
      },
    ]).drawable
    const marts = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
    const erd = erdForScope(index, marts, drawable)
    const fct = erd.models.find((m) => m.node.name === 'fct_revenue')!
    expect(fct.columns.find((c) => c.key === 'net_revenue')?.isKey).toBe(true)
    expect(erd.staged.map((r) => r.id)).toEqual(['staged-3'])
  })

  it('leaves the scope untouched when nothing is staged', () => {
    const marts = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
    expect(erdForScope(index, marts).staged).toEqual([])
  })
})

describe('suggested relationships on the canvas', () => {
  const pair = (id: string, from: string, fromCol: string, to: string, toCol: string) => ({
    id,
    from_model: from,
    from_column: fromCol,
    to_model: to,
    to_column: toCol,
  })
  const marts = () => listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!

  it('draws suggestions alongside staged ones, pinning their columns too', () => {
    const suggested = resolveStaged(index, [
      pair('sug-1', 'fct_revenue', 'net_revenue', 'mart_board', 'net_revenue'),
    ]).drawable
    const erd = erdForScope(index, marts(), [], suggested)
    expect(erd.suggested.map((r) => r.id)).toEqual(['sug-1'])
    const fct = erd.models.find((m) => m.node.name === 'fct_revenue')!
    expect(fct.columns.find((c) => c.key === 'net_revenue')?.isKey).toBe(true)
  })

  it('defaults a suggestion with no cardinality to many-to-one', () => {
    expect(resolveStaged(index, [pair('sug-2', 'fct_revenue', 'user_id', 'dim_users', 'user_id')]).drawable[0]
      .cardinality).toBe('many-to-one')
  })

  it('drops a suggestion that is already staged — the same id means the same pair', () => {
    const same = [pair('shared-id', 'fct_revenue', 'user_id', 'dim_users', 'user_id')]
    const drawable = resolveStaged(index, same).drawable
    const erd = erdForScope(index, marts(), drawable, drawable)
    expect(erd.staged.map((r) => r.id)).toEqual(['shared-id'])
    expect(erd.suggested).toEqual([]) // never both at once
  })

  it('leaves suggested empty when there are none', () => {
    expect(erdForScope(index, marts()).suggested).toEqual([])
  })
})

describe('the suggestion cap', () => {
  const marts = () => listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
  // every marts column paired with itself across the two marts models
  const many = Array.from({ length: 5 }, (_, i) => ({
    id: `many-${i}`,
    from_model: 'fct_revenue',
    from_column: i % 2 === 0 ? 'net_revenue' : 'user_id',
    to_model: 'mart_board',
    to_column: 'net_revenue',
  }))

  it('draws only the strongest, and counts what it left off the canvas', () => {
    const drawable = resolveStaged(index, many).drawable
    const erd = erdForScope(index, marts(), [], drawable, 2)
    expect(erd.suggested.map((r) => r.id)).toEqual(['many-0', 'many-1'])
    expect(erd.suggestedHidden).toBe(3)
  })

  it('hides nothing when everything fits', () => {
    const drawable = resolveStaged(index, many).drawable
    const erd = erdForScope(index, marts(), [], drawable)
    expect(erd.suggested).toHaveLength(drawable.length)
    expect(erd.suggestedHidden).toBe(0)
    expect(MAX_DRAWN_SUGGESTIONS).toBeGreaterThan(drawable.length)
  })

  it('pins only the columns it actually draws', () => {
    const drawable = resolveStaged(index, [
      { id: 'a', from_model: 'fct_revenue', from_column: 'user_id', to_model: 'mart_board', to_column: 'net_revenue' },
      { id: 'b', from_model: 'fct_revenue', from_column: 'net_revenue', to_model: 'mart_board', to_column: 'net_revenue' },
    ]).drawable
    const erd = erdForScope(index, marts(), [], drawable, 1)
    const fct = erd.models.find((m) => m.node.name === 'fct_revenue')!
    expect(fct.columns.find((c) => c.key === 'user_id')?.isKey).toBe(true)
    expect(fct.columns.find((c) => c.key === 'net_revenue')?.isKey).toBe(false)
  })
})

describe('scoping suggestions to the canvas (#60)', () => {
  const marts = () => listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
  const inside = {
    id: 'inside',
    from_model: 'fct_revenue',
    from_column: 'net_revenue',
    to_model: 'mart_board',
    to_column: 'net_revenue',
  }
  const crossing = {
    id: 'crossing',
    from_model: 'fct_revenue',
    from_column: 'net_revenue',
    to_model: 'stg_payments',
    to_column: 'amount',
  }
  const offGraph = {
    id: 'off-graph',
    from_model: 'fct_revenue',
    from_column: 'user_id',
    to_model: 'gone_model',
    to_column: 'user_id',
  }

  it('lists the scope’s own models', () => {
    const ids = scopeModelIds(index, marts())
    expect([...ids].sort()).toEqual([
      'model.demo.dim_users',
      'model.demo.fct_revenue',
      'model.demo.mart_board',
    ])
  })

  it('keeps only candidates with BOTH endpoints in the scope', () => {
    const entries = [inside, crossing, offGraph]
    const { drawable } = resolveStaged(index, entries)
    const scoped = suggestionsInScope(entries, drawable, scopeModelIds(index, marts()))
    expect(scoped.map((entry) => entry.id)).toEqual(['inside'])
  })

  it('refilters when the scope changes', () => {
    const entries = [inside, crossing]
    const { drawable } = resolveStaged(index, entries)
    const staging = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'staging')!
    expect(suggestionsInScope(entries, drawable, scopeModelIds(index, staging))).toEqual([])
  })

  it('never draws a candidate whose other end is outside the scope', () => {
    const drawable = resolveStaged(index, [inside, crossing]).drawable
    const erd = erdForScope(index, marts(), [], drawable)
    expect(erd.suggested.map((rel) => rel.id)).toEqual(['inside'])
    // ...and no external table is pulled in to hold the crossing one
    expect(erd.models.some((model) => model.node.name === 'stg_payments')).toBe(false)
  })
})

describe('auto-expanding a small scope (#62)', () => {
  const marts = () => listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!

  it('opens every table of a small scope expanded', () => {
    const erd = erdForScope(index, marts())
    expect(erd.models.length).toBeLessThanOrEqual(AUTO_EXPAND_MAX_MODELS)
    expect(autoExpandedModels(erd.models)).toEqual(
      new Set(erd.models.map((model) => model.node.node_id)),
    )
  })

  it('leaves a big scope collapsed, so the shape stays readable', () => {
    const erd = erdForScope(index, marts())
    expect(autoExpandedModels(erd.models, 2)).toEqual(new Set())
  })

  it('expands nothing when the scope is empty', () => {
    expect(autoExpandedModels([])).toEqual(new Set())
  })
})

describe('cardinality endpoint markers (#65)', () => {
  it('reads a declared FK as many-to-one', () => {
    expect(cardinalityMarkers()).toEqual({ start: 'url(#erd-card-many)', end: 'url(#erd-card-one)' })
    expect(cardinalityMarkers('many-to-one')).toEqual(cardinalityMarkers(null))
  })

  it('flips for one-to-many and doubles up for one-to-one', () => {
    expect(cardinalityMarkers('one-to-many')).toEqual({
      start: 'url(#erd-card-one)',
      end: 'url(#erd-card-many)',
    })
    expect(cardinalityMarkers('one-to-one')).toEqual({
      start: 'url(#erd-card-one)',
      end: 'url(#erd-card-one)',
    })
  })

  it('falls back rather than drawing nothing for an unknown value', () => {
    expect(cardinalityMarkers('who-knows')).toEqual(cardinalityMarkers('many-to-one'))
  })
})
