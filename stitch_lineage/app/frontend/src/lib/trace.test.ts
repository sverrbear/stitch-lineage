import { describe, expect, it } from 'vitest'
import {
  columnDefinition,
  definedAs,
  definedAsSummary,
  definedOriginSummary,
  definitionSql,
  traceReason,
  traceStatus,
} from './trace'
import type { GraphNode } from '../types'

function column(properties: Record<string, unknown>): GraphNode {
  return {
    node_id: 'model.demo.fct_revenue::net_revenue',
    node_type: 'column',
    name: 'net_revenue',
    column: 'net_revenue',
    properties,
  }
}

describe('traceStatus', () => {
  it('reads the status the build wrote', () => {
    expect(traceStatus(column({ trace_status: 'traced' }))).toBe('traced')
    expect(traceStatus(column({ trace_status: 'untraced' }))).toBe('untraced')
  })

  it('is null on a source column and on a graph built before #147', () => {
    // absence means "does not apply", never "untraced" — a source column is a
    // lineage root and claiming it failed to trace would be a lie
    expect(traceStatus(column({}))).toBeNull()
    expect(traceStatus(column({ trace_status: 'maybe' }))).toBeNull()
  })
})

describe('traceReason', () => {
  it('labels every code in the taxonomy and explains the fix', () => {
    const codes = [
      'no_compiled_code',
      'unparseable_sql',
      'column_not_in_sql',
      'star_not_expandable',
      'upstream_not_in_schema_map',
      'upstream_not_in_project',
      'no_upstream_columns',
      'lineage_failed',
    ]
    for (const code of codes) {
      const reason = traceReason(column({ trace_reason: code }))!
      expect(reason.code).toBe(code)
      // a label that is just the code with the underscores out is the fallback,
      // which means this code has no copy written for it yet
      expect(reason.label).not.toBe(code.replace(/_/g, ' '))
      expect(reason.hint.length).toBeGreaterThan(20)
    }
  })

  it('is null when there is no reason to give', () => {
    expect(traceReason(column({}))).toBeNull()
    expect(traceReason(column({ trace_reason: '' }))).toBeNull()
  })

  it('still shows an unknown code rather than dropping the row', () => {
    // a graph built by a newer stitch must not silently lose columns out of the list
    const reason = traceReason(column({ trace_reason: 'some_future_reason' }))!
    expect(reason.code).toBe('some_future_reason')
    expect(reason.label).toBe('some future reason')
  })
})

describe('definedAs', () => {
  it('reads a passthrough with its upstream column', () => {
    const defined = definedAs(
      column({ defined_as: { kind: 'passthrough', sql: 'o.amount', upstream: 'stg_orders.amount' } }),
    )
    expect(defined).toEqual({
      kind: 'passthrough',
      sql: 'o.amount',
      upstream: 'stg_orders.amount',
      origin: null,
    })
  })

  it('reads an expression with no upstream', () => {
    const defined = definedAs(
      column({ defined_as: { kind: 'expression', sql: 'amount * fx_rate', upstream: null } }),
    )
    expect(defined).toEqual({
      kind: 'expression',
      sql: 'amount * fx_rate',
      upstream: null,
      origin: null,
    })
  })

  it('reads a star with the upstream relation', () => {
    const defined = definedAs(column({ defined_as: { kind: 'star', sql: '*', upstream: 'stg_orders' } }))
    expect(defined).toEqual({ kind: 'star', sql: '*', upstream: 'stg_orders', origin: null })
  })

  it('reads the origin of a passthrough chain', () => {
    const defined = definedAs(
      column({
        defined_as: {
          kind: 'passthrough',
          sql: 'i.amount_usd',
          upstream: 'int_orders.amount_usd',
          origin: {
            kind: 'expression',
            model: 'stg_orders',
            column: 'amount_usd',
            sql: 'amount * fx_rate',
            hops: 3,
          },
        },
      }),
    )
    expect(defined?.origin).toEqual({
      kind: 'expression',
      model: 'stg_orders',
      column: 'amount_usd',
      sql: 'amount * fx_rate',
      hops: 3,
    })
  })

  it('reads a source origin, which has no SQL behind it', () => {
    const defined = definedAs(
      column({
        defined_as: {
          kind: 'passthrough',
          sql: 'user_id',
          upstream: 'stg_users.user_id',
          origin: { kind: 'source', model: 'raw_users', column: 'id', sql: null, hops: 2 },
        },
      }),
    )
    expect(defined?.origin?.kind).toBe('source')
    expect(defined?.origin?.sql).toBeNull()
  })

  it('drops an origin it cannot trust without dropping the definition', () => {
    // a graph from a newer or older stitch must still render its projection
    const cases = [
      'stg_orders',
      { kind: 'guess', model: 'stg_orders', column: 'amount', hops: 1 },
      { kind: 'expression', model: '', column: 'amount', hops: 1 },
      { kind: 'expression', model: 'stg_orders', column: 'amount' },
      { kind: 'expression', model: 'stg_orders', column: 'amount', hops: 0 },
      { kind: 'expression', model: 'stg_orders', column: 'amount', hops: 1.5 },
    ]
    for (const origin of cases) {
      const defined = definedAs(
        column({ defined_as: { kind: 'passthrough', sql: 'o.amount', upstream: null, origin } }),
      )
      expect(defined?.kind).toBe('passthrough')
      expect(defined?.origin).toBeNull()
    }
  })

  it('rejects a payload it cannot trust rather than half-rendering it', () => {
    expect(definedAs(column({}))).toBeNull()
    expect(definedAs(column({ defined_as: 'amount * 2' }))).toBeNull()
    expect(definedAs(column({ defined_as: { kind: 'guess', sql: 'x' } }))).toBeNull()
    expect(definedAs(column({ defined_as: { kind: 'expression', sql: '' } }))).toBeNull()
  })
})

describe('columnDefinition', () => {
  it('gives the definition alone for a traced column', () => {
    const result = columnDefinition(
      column({
        trace_status: 'traced',
        defined_as: { kind: 'expression', sql: 'amount * fx_rate', upstream: null },
      }),
    )
    expect(result).toEqual({
      definition: { kind: 'expression', sql: 'amount * fx_rate', upstream: null, origin: null },
      reason: null,
    })
  })

  it('gives the reason for an untraced column with no definition', () => {
    const result = columnDefinition(
      column({ trace_status: 'untraced', trace_reason: 'star_not_expandable' }),
    )
    expect(result?.definition).toBeNull()
    expect(result?.reason?.code).toBe('star_not_expandable')
  })

  it('never drops the reason when an untraced column also has a definition', () => {
    // the regression this guards: a passthrough whose upstream is undocumented
    // rendered `passthrough / feed_id` over an empty upstream list and explained
    // nothing, which is precisely the "looks broken" state #147/#148 remove
    const result = columnDefinition(
      column({
        trace_status: 'untraced',
        trace_reason: 'upstream_not_in_schema_map',
        defined_as: { kind: 'passthrough', sql: 'feed_id', upstream: null },
      }),
    )
    expect(result?.reason?.code).toBe('upstream_not_in_schema_map')
    expect(result?.definition?.kind).toBe('passthrough')
  })

  it('shows nothing for a source column or a non-column node', () => {
    expect(columnDefinition(column({}))).toBeNull()
    expect(
      columnDefinition({
        node_id: 'model.demo.fct_revenue',
        node_type: 'model',
        name: 'fct_revenue',
        properties: { defined_as: { kind: 'expression', sql: 'x' } },
      }),
    ).toBeNull()
  })
})

describe('definedAsSummary', () => {
  it('names the upstream when there is one, and stays honest when there is not', () => {
    expect(
      definedAsSummary({
        kind: 'passthrough',
        sql: 'o.amount',
        upstream: 'stg_orders.amount',
        origin: null,
      }),
    ).toBe('passthrough from stg_orders.amount')
    expect(
      definedAsSummary({ kind: 'passthrough', sql: 'o.amount', upstream: null, origin: null }),
    ).toBe('passthrough')
    expect(
      definedAsSummary({ kind: 'star', sql: '*', upstream: 'stg_orders', origin: null }),
    ).toBe('via star from stg_orders')
    expect(definedAsSummary({ kind: 'star', sql: '*', upstream: null, origin: null })).toBe(
      'via star',
    )
    expect(
      definedAsSummary({ kind: 'expression', sql: 'a * b', upstream: null, origin: null }),
    ).toBe('expression')
  })
})

describe('definedOriginSummary', () => {
  it('names the defining model, with the distance once it is more than one hop', () => {
    expect(
      definedOriginSummary({
        kind: 'expression',
        model: 'stg_orders',
        column: 'amount_usd',
        sql: 'amount * fx_rate',
        hops: 3,
      }),
    ).toBe('defined in stg_orders · 3 hops upstream')
    // one hop up, the parent is already named on the line above: no count needed
    expect(
      definedOriginSummary({
        kind: 'expression',
        model: 'stg_orders',
        column: 'amount_usd',
        sql: 'amount * fx_rate',
        hops: 1,
      }),
    ).toBe('defined in stg_orders')
  })

  it('says a source column originates rather than claiming it is defined', () => {
    expect(
      definedOriginSummary({
        kind: 'source',
        model: 'raw_users',
        column: 'id',
        sql: null,
        hops: 2,
      }),
    ).toBe('originates in raw_users.id · 2 hops upstream')
  })
})

describe('definitionSql', () => {
  it('shows the origin expression rather than the passthrough that restates it', () => {
    // the #162 fix: `i.amount_usd` says nothing the upstream list did not already say
    expect(
      definitionSql({
        kind: 'passthrough',
        sql: 'i.amount_usd',
        upstream: 'int_orders.amount_usd',
        origin: {
          kind: 'expression',
          model: 'stg_orders',
          column: 'amount_usd',
          sql: 'amount * fx_rate',
          hops: 3,
        },
      }),
    ).toBe('amount * fx_rate')
  })

  it('falls back to the projection when the origin carries no SQL of its own', () => {
    // a source origin, and every graph built before #162
    expect(
      definitionSql({
        kind: 'passthrough',
        sql: 'id',
        upstream: 'raw_users.id',
        origin: { kind: 'source', model: 'raw_users', column: 'id', sql: null, hops: 1 },
      }),
    ).toBe('id')
    expect(
      definitionSql({ kind: 'expression', sql: 'a * b', upstream: null, origin: null }),
    ).toBe('a * b')
  })
})
