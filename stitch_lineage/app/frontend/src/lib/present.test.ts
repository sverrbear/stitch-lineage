import { describe, expect, it } from 'vitest'
import { fixtureGraph } from './fixture'
import { buildIndex } from './graph'
import {
  displayName,
  isPlaceholder,
  metabaseRelation,
  nodeContext,
  ownerName,
  packageOf,
  warehouseColumn,
  warehouseRelation,
} from './present'
import type { GraphNode } from '../types'

const index = buildIndex(fixtureGraph())
const get = (id: string): GraphNode => index.nodesById.get(id)!

describe('displayName', () => {
  it('uses the dbt name for dbt entities', () => {
    expect(displayName(get('model.demo.fct_revenue'))).toBe('fct_revenue')
    expect(displayName(get('model.demo.fct_revenue::net_revenue'))).toBe('net_revenue')
    expect(displayName(get('source.demo.app.events'))).toBe('events')
  })

  it('uses the Metabase display name for Metabase entities', () => {
    expect(displayName(get('mb_field::101'))).toBe('Net Revenue')
    expect(displayName(get('mb_card::412'))).toBe('Revenue by country')
  })

  it('never renders a bare Metabase id as a name', () => {
    // mb_field::902 exists only as an edge endpoint, so its name is the id
    const placeholder = get('mb_field::902')
    expect(isPlaceholder(placeholder)).toBe(true)
    expect(placeholder.name).toBe('902')
    expect(displayName(placeholder)).toBe('field 902')
  })

  it('spells out a star pseudo-column', () => {
    expect(displayName(get('model.demo.stg_payments::*'))).toBe('all columns (*)')
  })
})

describe('nodeContext', () => {
  it('gives a column its dbt MODEL, never the physical table', () => {
    const column = get('model.demo.fct_revenue::net_revenue')
    // the fixture model is aliased sis_fct_revenue on purpose
    expect(column.table).toBe('sis_fct_revenue')
    expect(nodeContext(index, column)).toBe('fct_revenue')
  })

  it('resolves the owning model from the id when the model node is missing', () => {
    expect(ownerName(index, get('model.demo.stg_payments::*'))).toBe('stg_payments')
  })

  it('gives a model its dbt schema and a source its dbt source name', () => {
    expect(nodeContext(index, get('model.demo.fct_revenue'))).toBe('marts')
    expect(nodeContext(index, get('source.demo.artifacts.dbt_runs'))).toBe('artifacts')
  })

  it('gives a Metabase field its Metabase table and a card its collection', () => {
    expect(nodeContext(index, get('mb_field::101'))).toBe('FCT_REVENUE')
    expect(nodeContext(index, get('mb_card::412'))).toBe('Board')
    expect(nodeContext(index, get('mb_dash::7'))).toBe('Board')
  })

  it('returns null rather than inventing context', () => {
    expect(nodeContext(index, get('mb_field::902'))).toBeNull()
  })
})

describe('warehouse detail', () => {
  it('keeps the physical relation available as a secondary fact', () => {
    expect(warehouseRelation(get('model.demo.fct_revenue'))).toBe('marts.sis_fct_revenue')
  })

  it('exposes the warehouse spelling only when it differs from the dbt one', () => {
    expect(warehouseColumn(get('model.demo.fct_revenue::net_revenue'))).toBe('NET_REVENUE')
    expect(warehouseColumn(get('model.demo.fct_revenue::user_id'))).toBeNull()
  })

  it('locates a Metabase field in its own database', () => {
    expect(metabaseRelation(get('mb_field::101'))).toBe('Analytics · MARTS.FCT_REVENUE')
  })
})

describe('packageOf', () => {
  it('reads the dbt package out of a unique_id', () => {
    expect(packageOf('model.smitten.fct_matches::user_id')).toBe('smitten')
    expect(packageOf('source.demo.app.events')).toBe('demo')
    expect(packageOf('mb_card::412')).toBeNull()
  })
})
