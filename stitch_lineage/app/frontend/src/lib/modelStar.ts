// The model page's mini star-schema (#81). Pure TS, unit-tested.
//
// One model as the hub, the tables it is joined to around it, and the column pairs
// that do the joining — the same picture the ERD draws for a whole schema, scoped
// to one table so opening `dim_users` answers "what does this join to" without a
// scope selector and a pan.
//
// Declared `relates_to` edges and staged declarations both count: a relationship
// you drew a minute ago is a relationship, and hiding it until `stitch apply` runs
// would make the page lie. Which is which stays visible (`staged`).

import type { ErdStagedRelationship } from './erd'
import type { GraphIndex } from './graph'
import { modelIdOfColumn } from './graph'
import type { GraphNode } from '../types'

/** One column pair joining the hub to a neighbour. */
export interface StarJoin {
  /** Stable within a star: keys the edge and its hover state. */
  id: string
  /** Column name on the hub. */
  ownColumn: string
  /** Column name on the neighbour. */
  otherColumn: string
  /** `outgoing` = the hub holds the foreign key. */
  direction: 'outgoing' | 'incoming'
  cardinality: string | null
  validated: boolean
  /** Declared in the repo, or staged and waiting for `stitch apply`. */
  staged: boolean
}

export interface StarNeighbour {
  node: GraphNode
  joins: StarJoin[]
}

export interface ModelStar {
  hub: GraphNode
  neighbours: StarNeighbour[]
  /** Hub columns that take part, in the order the card should list them. */
  hubColumns: string[]
  /** Neighbours left off because the panel is a panel — the ERD has them all. */
  hiddenNeighbours: number
  /** Column pairs on this table, hidden neighbours included. */
  joinCount: number
}

/** Neighbours a compact panel can hold before it stops being readable. */
const DEFAULT_LIMIT = 10

/**
 * Build the star around `nodeId`. `staged` is the resolved staged set (the same
 * shape the ERD draws), already filtered or not — entries not touching this model
 * are ignored either way.
 */
export function modelStar(
  index: GraphIndex,
  nodeId: string,
  staged: readonly ErdStagedRelationship[] = [],
  options: { limit?: number } = {},
): ModelStar | null {
  const hub = index.nodesById.get(nodeId)
  if (!hub) return null
  const limit = options.limit ?? DEFAULT_LIMIT

  const joinsByNeighbour = new Map<string, StarJoin[]>()
  const seen = new Set<string>()
  const add = (neighbourId: string, join: StarJoin) => {
    if (neighbourId === nodeId) return
    // the same column pair can be declared AND staged; the declaration wins
    const key = `${neighbourId} ${join.ownColumn.toLowerCase()} ${join.otherColumn.toLowerCase()}`
    if (seen.has(key)) return
    seen.add(key)
    const bucket = joinsByNeighbour.get(neighbourId)
    if (bucket) bucket.push(join)
    else joinsByNeighbour.set(neighbourId, [join])
  }

  const columnName = (columnId: string): string =>
    index.nodesById.get(columnId)?.column ??
    index.nodesById.get(columnId)?.name ??
    columnId.split('::').pop() ??
    columnId

  for (const edge of index.graph.edges) {
    if (edge.edge_type !== 'relates_to') continue
    const fromModel = modelIdOfColumn(edge.from)
    const toModel = modelIdOfColumn(edge.to)
    const outgoing = fromModel === nodeId
    const incoming = toModel === nodeId
    if (!outgoing && !incoming) continue
    const neighbourId = outgoing ? toModel : fromModel
    if (!neighbourId || !index.nodesById.has(neighbourId)) continue
    add(neighbourId, {
      id: `${edge.from}->${edge.to}`,
      ownColumn: columnName(outgoing ? edge.from : edge.to),
      otherColumn: columnName(outgoing ? edge.to : edge.from),
      direction: outgoing ? 'outgoing' : 'incoming',
      cardinality: null,
      validated: edge.confidence === 'validated',
      staged: false,
    })
  }

  for (const entry of staged) {
    const outgoing = entry.fromModelId === nodeId
    const incoming = entry.toModelId === nodeId
    if (!outgoing && !incoming) continue
    const neighbourId = outgoing ? entry.toModelId : entry.fromModelId
    if (!index.nodesById.has(neighbourId)) continue
    add(neighbourId, {
      id: `staged-${entry.id}`,
      ownColumn: outgoing ? entry.fromColumn : entry.toColumn,
      otherColumn: outgoing ? entry.toColumn : entry.fromColumn,
      direction: outgoing ? 'outgoing' : 'incoming',
      cardinality: entry.cardinality || null,
      validated: false,
      staged: true,
    })
  }

  const all: StarNeighbour[] = [...joinsByNeighbour.entries()]
    .flatMap(([id, joins]) => {
      const node = index.nodesById.get(id)
      return node ? [{ node, joins: joins.sort((a, b) => a.id.localeCompare(b.id)) }] : []
    })
    // most-joined first, so what the panel drops is what matters least
    .sort((a, b) => b.joins.length - a.joins.length || a.node.name.localeCompare(b.node.name))

  const neighbours = all.slice(0, limit)
  const hubColumns: string[] = []
  for (const neighbour of neighbours) {
    for (const join of neighbour.joins) {
      if (!hubColumns.includes(join.ownColumn)) hubColumns.push(join.ownColumn)
    }
  }

  return {
    hub,
    neighbours,
    hubColumns,
    hiddenNeighbours: all.length - neighbours.length,
    joinCount: all.reduce((total, neighbour) => total + neighbour.joins.length, 0),
  }
}

// The mini card's box, which the layout needs before React Flow has measured
// anything. Every value here mirrors `.star-node` in styles.css: change one, change
// both, or the constellation spaces cards it cannot see.
export const STAR_CARD_WIDTH = 208
/** 40px of header plus the card's own 1px borders. */
const STAR_HEADER_PX = 42
const STAR_ROW_PX = 19
const STAR_LIST_PADDING_PX = 8

/** Height of a mini card listing `rows` joined columns. */
export function starCardHeight(rows: number): number {
  return STAR_HEADER_PX + STAR_LIST_PADDING_PX + Math.max(1, rows) * STAR_ROW_PX
}
