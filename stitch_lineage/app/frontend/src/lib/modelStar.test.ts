import { describe, expect, it } from 'vitest'
import { fixtureGraph } from './fixture'
import { buildIndex } from './graph'
import { modelStar, starCardHeight, STAR_CARD_WIDTH } from './modelStar'
import type { ErdStagedRelationship } from './erd'

const index = buildIndex(fixtureGraph())
const REVENUE = 'model.demo.fct_revenue'
const USERS = 'model.demo.dim_users'

function staged(partial: Partial<ErdStagedRelationship> = {}): ErdStagedRelationship {
  return {
    id: 's1',
    fromModelId: REVENUE,
    toModelId: 'model.demo.mart_board',
    fromColumn: 'net_revenue',
    toColumn: 'net_revenue',
    cardinality: 'many-to-one',
    ...partial,
  }
}

describe('modelStar', () => {
  it('puts the model at the centre with the tables it joins around it', () => {
    const star = modelStar(index, REVENUE)!
    expect(star.hub.node_id).toBe(REVENUE)
    expect(star.neighbours.map((n) => n.node.node_id)).toEqual([USERS])
    expect(star.joinCount).toBe(1)
  })

  it('reads the joined column pair off the declaration, both sides', () => {
    const [join] = modelStar(index, REVENUE)!.neighbours[0].joins
    expect(join.ownColumn).toBe('user_id')
    expect(join.otherColumn).toBe('user_id')
    expect(join.direction).toBe('outgoing')
    expect(join.validated).toBe(true)
    expect(join.staged).toBe(false)
  })

  it('works from the other end too — the referenced model sees it as incoming', () => {
    const star = modelStar(index, USERS)!
    expect(star.neighbours.map((n) => n.node.node_id)).toEqual([REVENUE])
    const [join] = star.neighbours[0].joins
    expect(join.direction).toBe('incoming')
    expect(join.ownColumn).toBe('user_id')
  })

  it('counts staged declarations, and says they are staged', () => {
    const star = modelStar(index, REVENUE, [staged()])!
    expect(star.neighbours.map((n) => n.node.node_id).sort()).toEqual([
      USERS,
      'model.demo.mart_board',
    ])
    const board = star.neighbours.find((n) => n.node.node_id === 'model.demo.mart_board')!
    expect(board.joins[0].staged).toBe(true)
    expect(board.joins[0].cardinality).toBe('many-to-one')
  })

  it('never shows the same column pair twice when it is declared and staged', () => {
    const duplicate = staged({ toModelId: USERS, fromColumn: 'user_id', toColumn: 'user_id' })
    const star = modelStar(index, REVENUE, [duplicate])!
    expect(star.neighbours).toHaveLength(1)
    expect(star.neighbours[0].joins).toHaveLength(1)
    expect(star.neighbours[0].joins[0].staged).toBe(false)
  })

  it('ignores staged entries for other models, and self-joins', () => {
    const elsewhere = staged({ id: 's2', fromModelId: USERS, toModelId: 'model.demo.mart_board' })
    const selfJoin = staged({ id: 's3', toModelId: REVENUE, toColumn: 'user_id' })
    const star = modelStar(index, REVENUE, [elsewhere, selfJoin])!
    expect(star.neighbours.map((n) => n.node.node_id)).toEqual([USERS])
  })

  it('lists the hub columns that take part, once each, most-joined neighbour first', () => {
    const extra = [
      staged({ id: 'a', toModelId: 'model.demo.mart_board', fromColumn: 'net_revenue', toColumn: 'net_revenue' }),
      staged({ id: 'b', toModelId: 'model.demo.mart_board', fromColumn: 'user_id', toColumn: 'net_revenue' }),
    ]
    const star = modelStar(index, REVENUE, extra)!
    expect(star.neighbours[0].node.node_id).toBe('model.demo.mart_board')
    expect(star.neighbours[0].joins).toHaveLength(2)
    expect(star.hubColumns).toEqual(['net_revenue', 'user_id'])
  })

  it('caps the neighbours a panel draws and says how many it dropped', () => {
    const many = ['model.demo.dim_users', 'model.demo.mart_board', 'model.demo.stg_payments'].map(
      (id, i) => staged({ id: `s${i}`, toModelId: id, fromColumn: `c${i}`, toColumn: `c${i}` }),
    )
    // three distinct neighbours in play (dim_users is also the declared one)
    const star = modelStar(index, REVENUE, many, { limit: 2 })!
    expect(star.neighbours).toHaveLength(2)
    expect(star.hiddenNeighbours).toBe(1)
  })

  it('is empty, not broken, for a model nothing joins', () => {
    const star = modelStar(index, 'model.demo.stg_payments')!
    expect(star.neighbours).toEqual([])
    expect(star.joinCount).toBe(0)
    expect(star.hubColumns).toEqual([])
  })

  it('has nothing to say about a node that is not in the graph', () => {
    expect(modelStar(index, 'model.demo.gone')).toBeNull()
  })
})

describe('mini card metrics', () => {
  it('grows with the rows it lists, and never collapses to nothing', () => {
    expect(starCardHeight(4)).toBeGreaterThan(starCardHeight(1))
    expect(starCardHeight(0)).toBe(starCardHeight(1))
    expect(STAR_CARD_WIDTH).toBeGreaterThan(120)
  })
})
