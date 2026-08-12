import { describe, expect, it } from 'vitest'
import {
  AUTO_EXPAND_MAX_MODELS,
  MAX_DRAWN_SUGGESTIONS,
  autoExpandedModels,
  resolveStaged,
  defaultScope,
  erdClickHref,
  erdColumnNodeId,
  erdCounts,
  erdCountsLabel,
  erdForScope,
  findScope,
  focusErd,
  relatedModelIds,
  initialScope,
  isErdTable,
  listScopes,
  relationshipScopeLabel,
  scopeModelIds,
  suggestionsInScope,
  visibleColumns,
  type ErdData,
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

// #191: a Snowflake semantic view is a semantic-layer definition over the facts
// and dims beneath it, not a relation you join to. The fixture's `sv_revenue`
// sits in `marts` and carries `core`, so every assertion here is about a count or
// a card the ERD used to get wrong — while its lineage stays untouched.
describe('semantic views are not ERD tables (#191)', () => {
  const SV = 'model.demo.sv_revenue'

  it('is not an ERD table, while an ordinary model is', () => {
    expect(isErdTable(index.nodesById.get(SV)!)).toBe(false)
    expect(isErdTable(index.nodesById.get('model.demo.fct_revenue')!)).toBe(true)
    expect(isErdTable(index.nodesById.get('source.demo.app.events')!)).toBe(true)
  })

  it('is left out of the scope counts the picker offers', () => {
    const scopes = listScopes(index)
    const marts = scopes.find((s) => s.kind === 'schema' && s.value === 'marts')!
    const core = scopes.find((s) => s.kind === 'tag' && s.value === 'core')!
    expect(marts.modelCount).toBe(3) // fct_revenue, dim_users, mart_board
    expect(core.modelCount).toBe(3) // stg_payments, fct_revenue, dim_users
    // a tag only semantic views carry stops being a scope at all
    expect(scopes.some((s) => s.kind === 'tag' && s.value === 'semantic')).toBe(false)
  })

  it('is not in the scope, so it is never drawn or auto-expanded', () => {
    const marts = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
    expect(scopeModelIds(index, marts).has(SV)).toBe(false)
    const erd = erdForScope(index, marts)
    expect(erd.models.map((m) => m.node.node_id)).not.toContain(SV)
    expect(autoExpandedModels(erd.models).has(SV)).toBe(false)
  })

  it('still resolves by name, so a staged entry on one is not reported missing', () => {
    const { drawable, unresolvedIds } = resolveStaged(index, [
      {
        id: 'staged-on-sv',
        from_model: 'fct_revenue',
        from_column: 'user_id',
        to_model: 'sv_revenue',
        to_column: 'user_id',
      },
    ])
    expect(unresolvedIds).toEqual([])
    expect(drawable[0].toModelId).toBe(SV)
  })

  it('never lands an edge on a card the canvas will not draw', () => {
    const marts = listScopes(index).find((s) => s.kind === 'schema' && s.value === 'marts')!
    const { drawable } = resolveStaged(index, [
      {
        id: 'staged-on-sv',
        from_model: 'fct_revenue',
        from_column: 'user_id',
        to_model: 'sv_revenue',
        to_column: 'user_id',
      },
    ])
    const erd = erdForScope(index, marts, drawable, drawable)
    expect(erd.staged).toEqual([])
    expect(erd.suggested).toEqual([])
    // and it is not pulled in as an external endpoint to hold the dropped edge
    expect(erd.models.map((m) => m.node.node_id)).not.toContain(SV)
  })

  it('keeps a declared relationship off the canvas at both ends', () => {
    const withRel = fixtureGraph()
    withRel.edges.push({
      from: 'model.demo.dim_users::user_id',
      to: `${SV}::user_id`,
      edge_type: 'relates_to',
      confidence: 'declared',
      evidence: {},
    })
    const relIndex = buildIndex(withRel)
    const marts = listScopes(relIndex).find((s) => s.kind === 'schema' && s.value === 'marts')!
    // the picker's count and the canvas agree, and neither counts the semantic view
    expect(marts.relationshipCount).toBe(1)
    const erd = erdForScope(relIndex, marts)
    expect(erd.relationships).toHaveLength(1)
    expect(erd.models.map((m) => m.node.node_id)).not.toContain(SV)
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


// #120: the picker said "intermediate (100 models, 2 rels)" and the header beside
// it said "102 models · 2 relationships" for the same scope. Whatever the canvas
// pulls in, the two numbers have to be talking about the same population.
describe('erdCounts / erdCountsLabel', () => {
  it('keeps the scope own models apart from the endpoints it reaches into', () => {
    const finance = listScopes(index).find((s) => s.kind === 'tag' && s.value === 'finance')!
    const erd = erdForScope(index, finance)
    const counts = erdCounts(erd)
    // dim_users is on the canvas, but it is not one of finance's models
    expect(erd.models).toHaveLength(counts.models + counts.external)
    expect(counts.external).toBeGreaterThan(0)
    expect(counts.models).toBe(finance.modelCount)
  })

  it('every scope the picker offers reports what its canvas draws', () => {
    for (const scope of listScopes(index)) {
      const erd = erdForScope(index, scope)
      expect([scope.value, erdCounts(erd).models]).toEqual([scope.value, scope.modelCount])
      expect([scope.value, erd.relationships.length]).toEqual([
        scope.value,
        scope.relationshipCount,
      ])
    }
  })

  it('labels the joined-in endpoints instead of hiding them in the total', () => {
    expect(erdCountsLabel({ models: 100, relationships: 2, external: 2 })).toBe(
      '100 models · 2 relationships · +2 joined from other scopes',
    )
  })

  it('says nothing about other scopes when it reached into none', () => {
    expect(erdCountsLabel({ models: 42, relationships: 33, external: 0 })).toBe(
      '42 models · 33 relationships',
    )
  })

  it('counts one of a thing in the singular', () => {
    expect(erdCountsLabel({ models: 1, relationships: 1, external: 1 })).toBe(
      '1 model · 1 relationship · +1 joined from other scopes',
    )
  })
})

// #121: a cross-scope candidate is judged on the pair, not the picture — so the
// panel says where its two models live instead of drawing neither of them.
describe('relationshipScopeLabel', () => {
  const rel = (fromModelId: string, toModelId: string) => ({ fromModelId, toModelId })
  const M = 'model.demo'

  it('names one scope when both ends share it', () => {
    expect(relationshipScopeLabel(index, rel(`${M}.fct_revenue`, `${M}.dim_users`))).toBe('marts')
  })

  it('names both when the pair crosses scopes', () => {
    expect(relationshipScopeLabel(index, rel(`${M}.stg_payments`, `${M}.fct_revenue`))).toBe(
      'staging → marts',
    )
  })

  it('is null when the graph knows neither model', () => {
    expect(relationshipScopeLabel(index, rel('model.demo.gone', 'model.demo.also_gone'))).toBeNull()
  })
})

// --- focusing one table's neighbourhood (#163) --------------------------------

/** A canvas of bare tables: focusErd only ever looks at ids and endpoints. */
function canvas(
  models: string[],
  edges: {
    declared?: Array<[string, string]>
    staged?: Array<[string, string]>
    suggested?: Array<[string, string]>
  } = {},
): ErdData {
  const pair = ([fromModelId, toModelId]: [string, string], i: number) => ({
    id: `s${i}`,
    fromModelId,
    toModelId,
    fromColumn: 'a',
    toColumn: 'b',
    cardinality: 'many-to-one',
  })
  return {
    scope: {
      kind: 'schema',
      value: 'marts',
      modelCount: models.length,
      relationshipCount: 0,
      internal: false,
    },
    models: models.map((id) => ({
      node: { node_id: id, node_type: 'model' as const, name: id, properties: {} },
      columns: [],
      external: false,
    })),
    relationships: (edges.declared ?? []).map(([fromModelId, toModelId]) => ({
      edge: { from: `${fromModelId}::a`, to: `${toModelId}::b`, edge_type: 'relates_to' },
      fromModelId,
      toModelId,
      fromColumn: 'a',
      toColumn: 'b',
      validated: false,
    })) as ErdData['relationships'],
    staged: (edges.staged ?? []).map(pair),
    suggested: (edges.suggested ?? []).map(pair),
    suggestedHidden: 0,
  }
}

const ids = (erd: ErdData) => erd.models.map((model) => model.node.node_id).sort()

describe('relatedModelIds', () => {
  it('is the table plus everything an edge joins it to, in either direction', () => {
    const erd = canvas(['a', 'b', 'c', 'd'], { declared: [['a', 'b'], ['c', 'a']] })
    expect([...relatedModelIds(erd, 'a')].sort()).toEqual(['a', 'b', 'c'])
  })

  it('counts staged and suggested edges as relations too', () => {
    // a staged relationship is one the reader just drew: hiding its far end would
    // hide the thing they were looking at
    const erd = canvas(['a', 'b', 'c'], { staged: [['a', 'b']], suggested: [['c', 'a']] })
    expect([...relatedModelIds(erd, 'a')].sort()).toEqual(['a', 'b', 'c'])
  })

  it('is just the table itself when nothing joins it', () => {
    expect([...relatedModelIds(canvas(['a', 'b']), 'a')]).toEqual(['a'])
  })
})

describe('focusErd', () => {
  it('keeps the table and its direct relations, and drops the rest', () => {
    const erd = canvas(['a', 'b', 'c', 'far'], { declared: [['a', 'b'], ['a', 'c']] })
    expect(ids(focusErd(erd, 'a'))).toEqual(['a', 'b', 'c'])
  })

  it('keeps a relationship between two neighbours', () => {
    // a neighbourhood that hides its own internal joins is a star, not a model
    const erd = canvas(['a', 'b', 'c'], {
      declared: [['a', 'b'], ['a', 'c'], ['b', 'c']],
    })
    const focused = focusErd(erd, 'a')
    expect(ids(focused)).toEqual(['a', 'b', 'c'])
    expect(focused.relationships).toHaveLength(3)
  })

  it('never leaves an edge with an endpoint that is not drawn', () => {
    const erd = canvas(['a', 'b', 'c', 'd'], {
      declared: [['a', 'b'], ['c', 'd']],
      staged: [['b', 'd']],
      suggested: [['a', 'c']],
    })
    const focused = focusErd(erd, 'a')
    const drawn = new Set(ids(focused))
    for (const edge of [...focused.relationships, ...focused.staged, ...focused.suggested]) {
      expect(drawn.has(edge.fromModelId)).toBe(true)
      expect(drawn.has(edge.toModelId)).toBe(true)
    }
    // b and c are neighbours; d is only reachable through them, so it stays out —
    // and the two edges that reach for it go with it
    expect(ids(focused)).toEqual(['a', 'b', 'c'])
    expect(focused.staged).toHaveLength(0)
    expect(focused.relationships.map((r) => [r.fromModelId, r.toModelId])).toEqual([['a', 'b']])
  })

  it('drops the relationships of a table it drops', () => {
    const erd = canvas(['a', 'b', 'x', 'y'], { declared: [['a', 'b'], ['x', 'y']] })
    expect(focusErd(erd, 'a').relationships).toHaveLength(1)
  })

  it('is the whole canvas again with nothing focused', () => {
    const erd = canvas(['a', 'b', 'c'], { declared: [['a', 'b']] })
    expect(focusErd(erd, null)).toBe(erd)
  })

  it('leaves the canvas alone when the focused table is not on it', () => {
    // the scope changed under the focus; collapsing to an empty diagram would read
    // as a bug rather than as a filter
    const erd = canvas(['a', 'b'], { declared: [['a', 'b']] })
    expect(focusErd(erd, 'gone')).toBe(erd)
  })

  it('keeps the scope it was given, so the header still says where you are', () => {
    const erd = canvas(['a', 'b', 'c'], { declared: [['a', 'b']] })
    expect(focusErd(erd, 'a').scope).toEqual(erd.scope)
  })
})
