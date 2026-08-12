import { afterEach, describe, expect, it } from 'vitest'
import { fixtureGraph } from './fixture'
import { buildIndex } from './graph'
import {
  displayName,
  displayTableName,
  managerOf,
  managerOfNode,
  strippedPrefixes,
  setStripModelPrefixes,
  setTablePrefixes,
  hasHiddenPrefix,
  fullName,
  isArchived,
  isPlaceholder,
  metabaseRelation,
  nodeContext,
  ownerName,
  packageOf,
  warehouseColumn,
  warehouseRelation,
} from './present'
import type { GraphNode, NodeType } from '../types'

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

describe('routing prefixes hidden from display names (#69)', () => {
  const model = (name: string, type: 'model' | 'source' = 'model'): GraphNode => ({
    node_id: `model.demo.${name}`,
    node_type: type,
    name,
    properties: {},
  })

  afterEach(() => setStripModelPrefixes([]))

  it('hides a configured prefix from the display name only', () => {
    setStripModelPrefixes(['viz_', 'sv_'])
    const node = model('viz_dim_users')
    expect(displayName(node)).toBe('dim_users')
    expect(fullName(node)).toBe('viz_dim_users')
    expect(node.node_id).toBe('model.demo.viz_dim_users')
    expect(hasHiddenPrefix(node)).toBe(true)
  })

  it('leaves names alone when nothing is configured', () => {
    expect(displayName(model('viz_dim_users'))).toBe('viz_dim_users')
    expect(hasHiddenPrefix(model('viz_dim_users'))).toBe(false)
  })

  it('never strips a name down to nothing, and ignores blank config', () => {
    setStripModelPrefixes(['viz_', '  '])
    expect(displayName(model('viz_'))).toBe('viz_')
    expect(strippedPrefixes()).toEqual(['viz_'])
  })

  it('only applies to models and sources, never to columns or BI entities', () => {
    setStripModelPrefixes(['viz_'])
    const column: GraphNode = {
      node_id: 'model.demo.viz_dim_users::viz_id',
      node_type: 'column',
      name: 'viz_id',
      properties: {},
    }
    expect(displayName(column)).toBe('viz_id')
  })
})

describe('table_prefix hidden from displayed physical names (#80)', () => {
  afterEach(() => setTablePrefixes([]))

  it('strips a configured prefix, case-insensitively', () => {
    setTablePrefixes(['sis_'])
    expect(displayTableName('sis_fct_boost_performance')).toBe('fct_boost_performance')
    expect(displayTableName('SIS_FCT_BOOST_PERFORMANCE')).toBe('FCT_BOOST_PERFORMANCE')
  })

  it('leaves a name alone when nothing is configured or nothing matches', () => {
    expect(displayTableName('sis_fct_revenue')).toBe('sis_fct_revenue')
    setTablePrefixes(['xx_'])
    expect(displayTableName('sis_fct_revenue')).toBe('sis_fct_revenue')
  })

  it('never strips a name down to nothing, and ignores blank config', () => {
    setTablePrefixes(['sis_', '   '])
    expect(displayTableName('sis_')).toBe('sis_')
  })

  it('has nothing to say about a missing table', () => {
    setTablePrefixes(['sis_'])
    expect(displayTableName(null)).toBeNull()
    expect(displayTableName('  ')).toBeNull()
  })

  it('leaves the exact warehouse relation alone — that one is a locator', () => {
    setTablePrefixes(['sis_'])
    expect(warehouseRelation(get('model.demo.fct_revenue'))).toBe('marts.sis_fct_revenue')
  })
})

// #122: "Match to Conversation Ratio" appeared three times with nothing to tell
// the three apart.
describe('card context and archived flag', () => {
  const card = (properties: Record<string, unknown>): GraphNode => ({
    node_id: 'mb_card::418',
    node_type: 'mb_card',
    name: 'Match to Conversation Ratio',
    properties,
  })

  it('locates a card by its collection breadcrumb', () => {
    expect(nodeContext(index, card({ collection_path: 'Growth/Retention' }))).toBe(
      'Growth/Retention',
    )
  })

  it('still reads a graph built before the breadcrumb existed', () => {
    expect(nodeContext(index, card({ collection_name: 'Marts' }))).toBe('Marts')
  })

  it('says nothing rather than guessing when the card has no collection', () => {
    expect(nodeContext(index, card({ collection_id: null }))).toBeNull()
  })

  it('flags archived, and only when it is true', () => {
    expect(isArchived(card({ archived: true }))).toBe(true)
    expect(isArchived(card({ archived: false }))).toBe(false)
    expect(isArchived(card({}))).toBe(false)
  })
})

// #187: the badge answers "who manages this table", not "which system does it live
// in". Getting this wrong is invisible — a wrong mark is still a mark — so the rule
// is pinned per node type here rather than left to a reader of the component.
describe('who manages a node (#187)', () => {
  it('gives dbt everything the pipeline produces', () => {
    expect(managerOf('model')).toBe('dbt')
    expect(managerOfNode(get('model.demo.stg_payments'))).toBe('dbt')
  })

  it('gives Snowflake a source: it landed in the warehouse, dbt does not manage it', () => {
    expect(managerOf('source')).toBe('snowflake')
    expect(managerOfNode(get('source.demo.app.events'))).toBe('snowflake')
    expect(managerOfNode(get('source.demo.artifacts.dbt_runs'))).toBe('snowflake')
  })

  it('gives a column the manager of its own table, either side of the boundary', () => {
    expect(managerOfNode(get('model.demo.stg_payments::amount'))).toBe('dbt')
    expect(managerOfNode(get('source.demo.app.events::amount'))).toBe('snowflake')
  })

  it('leaves the Metabase side alone', () => {
    expect(managerOf('mb_field')).toBe('metabase')
    expect(managerOf('mb_card')).toBe('metabase')
    expect(managerOf('mb_dashboard')).toBe('metabase')
    expect(managerOfNode(get('mb_card::412'))).toBe('metabase')
  })

  it('never claims dbt manages a warehouse table it cannot identify', () => {
    // a column whose owner prefix is neither `model.` nor `source.`, and a column
    // asked about with no id at all: both are "we do not know", and the honest
    // answer to that is the mark that claims nothing about the dbt repo.
    expect(managerOf('column', 'seed.demo.country_codes::code')).toBe('snowflake')
    expect(managerOf('column')).toBe('snowflake')
  })

  it('classifies every node type in the schema, so a new one cannot slip through', () => {
    const types: NodeType[] = ['source', 'model', 'column', 'mb_field', 'mb_card', 'mb_dashboard']
    for (const type of types) {
      expect(['dbt', 'snowflake', 'metabase']).toContain(managerOf(type))
    }
  })
})
