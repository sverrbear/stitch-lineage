// Client-side search over every node in graph.json, mirroring the ranking of
// the Python CLI (stitch_lineage/graph/search.py): exact name > name prefix >
// word-boundary in name > substring in any field > fuzzy (fuse.js on name).
// Index built once at load. Pure TS, unit-testable.

import Fuse from 'fuse.js'
import type { GraphNode, NodeType } from '../types'
import { type GraphIndex, idTail, modelIdOfColumn } from './graph'

export interface SearchHit {
  node: GraphNode
  /** 5 exact, 4 prefix, 3 word-boundary, 2 substring in any field, 1 fuzzy. */
  tier: number
  score: number
  matchedField: string
  /** Compact locator: model name for a column, collection for cards, schema.table otherwise. */
  context: string | null
}

interface SearchDoc {
  node: GraphNode
  name: string
  tail: string
  description: string
  extras: Array<[string, string]>
}

export const NODE_TYPE_ORDER: NodeType[] = [
  'model',
  'column',
  'mb_card',
  'mb_dashboard',
  'source',
  'mb_field',
]

export const NODE_TYPE_LABEL: Record<NodeType, string> = {
  model: 'Models',
  column: 'Columns',
  mb_card: 'Metabase cards',
  mb_dashboard: 'Dashboards',
  source: 'Sources',
  mb_field: 'Metabase fields',
}

function extrasOf(node: GraphNode): Array<[string, string]> {
  const extras: Array<[string, string]> = []
  for (const key of ['title', 'collection_name', 'collection'] as const) {
    const value = node.properties?.[key]
    if (typeof value === 'string' && value) extras.push([`properties.${key}`, value.toLowerCase()])
  }
  const tags = node.properties?.tags
  if (Array.isArray(tags) && tags.length > 0) {
    extras.push(['properties.tags', tags.map(String).join(' ').toLowerCase()])
  }
  return extras
}

function contextOf(node: GraphNode, index: GraphIndex): string | null {
  if (node.node_type === 'column') {
    const modelId = modelIdOfColumn(node.node_id)
    if (!modelId) return null
    return index.nodesById.get(modelId)?.name ?? idTail(modelId)
  }
  if (node.node_type === 'mb_card' || node.node_type === 'mb_dashboard') {
    for (const key of ['collection_name', 'collection'] as const) {
      const value = node.properties?.[key]
      if (typeof value === 'string' && value) return value
    }
    return null
  }
  if (node.table) return node.schema ? `${node.schema}.${node.table}` : node.table
  return null
}

function startsWord(text: string, query: string): boolean {
  let i = text.indexOf(query)
  while (i > 0) {
    const prev = text.charCodeAt(i - 1)
    const isAlnum =
      (prev >= 48 && prev <= 57) || (prev >= 97 && prev <= 122) || (prev >= 65 && prev <= 90)
    if (!isAlnum) return true
    i = text.indexOf(query, i + 1)
  }
  return i === 0
}

export class GraphSearch {
  private docs: SearchDoc[]
  private fuse: Fuse<SearchDoc>
  private index: GraphIndex

  constructor(index: GraphIndex) {
    this.index = index
    // Search real graph nodes only, not synthesized placeholders.
    this.docs = index.graph.nodes.map((node) => ({
      node,
      name: node.name.toLowerCase(),
      tail: idTail(node.node_id).toLowerCase(),
      description: (node.description ?? '').toLowerCase(),
      extras: extrasOf(node),
    }))
    this.fuse = new Fuse(this.docs, {
      keys: ['name', 'tail'],
      includeScore: true,
      threshold: 0.4,
      ignoreLocation: true,
    })
  }

  search(query: string, limit = 30): SearchHit[] {
    const needle = query.trim().toLowerCase()
    if (!needle) return []

    const hits: SearchHit[] = []
    const seen = new Set<string>()
    for (const doc of this.docs) {
      const match = this.matchDoc(doc, needle)
      if (match) {
        hits.push(match)
        seen.add(doc.node.node_id)
      }
    }

    // Fuzzy tier via fuse.js for anything the strict tiers missed.
    for (const result of this.fuse.search(needle, { limit: limit * 2 })) {
      const doc = result.item
      if (seen.has(doc.node.node_id)) continue
      hits.push({
        node: doc.node,
        tier: 1,
        score: 1 - (result.score ?? 1),
        matchedField: 'name',
        context: contextOf(doc.node, this.index),
      })
    }

    hits.sort(
      (a, b) =>
        b.tier - a.tier ||
        b.score - a.score ||
        a.node.name.localeCompare(b.node.name) ||
        a.node.node_id.localeCompare(b.node.node_id),
    )
    return hits.slice(0, limit)
  }

  private matchDoc(doc: SearchDoc, needle: string): SearchHit | null {
    const base = { node: doc.node, context: contextOf(doc.node, this.index) }
    if (doc.name === needle || doc.tail === needle) {
      return { ...base, tier: 5, score: 5, matchedField: 'name' }
    }
    if (doc.name.startsWith(needle) || doc.tail.startsWith(needle)) {
      return { ...base, tier: 4, score: 4, matchedField: 'name' }
    }
    if (startsWord(doc.name, needle)) {
      return { ...base, tier: 3, score: 3, matchedField: 'name' }
    }
    if (doc.name.includes(needle)) {
      return { ...base, tier: 2, score: 2.5, matchedField: 'name' }
    }
    if (doc.description.includes(needle)) {
      return { ...base, tier: 2, score: 2, matchedField: 'description' }
    }
    for (const [field, value] of doc.extras) {
      if (value.includes(needle)) return { ...base, tier: 2, score: 2, matchedField: field }
    }
    return null
  }
}

export interface HitGroup {
  type: NodeType
  label: string
  hits: SearchHit[]
}

/** Group ranked hits by node type, preserving rank order inside each group. */
export function groupHits(hits: SearchHit[]): HitGroup[] {
  const byType = new Map<NodeType, SearchHit[]>()
  for (const hit of hits) {
    const group = byType.get(hit.node.node_type)
    if (group) group.push(hit)
    else byType.set(hit.node.node_type, [hit])
  }
  const groups: HitGroup[] = []
  for (const type of NODE_TYPE_ORDER) {
    const groupHits = byType.get(type)
    if (groupHits) groups.push({ type, label: NODE_TYPE_LABEL[type], hits: groupHits })
  }
  return groups
}
