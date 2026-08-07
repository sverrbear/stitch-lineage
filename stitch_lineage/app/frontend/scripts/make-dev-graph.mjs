#!/usr/bin/env node
// Copies a real graph.json into dev-public/dev-graph.json (gitignored) for
// `npm run dev` without a running API. If the source graph has no Metabase
// nodes (a --no-metabase build), deterministically appends a few synthetic
// mb_field/mb_card/mb_dashboard nodes bound to real fct/dim columns so the
// BI half of the UI is exercisable in dev.
//
// Usage: node scripts/make-dev-graph.mjs /path/to/.stitch/graph.json

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const source = process.argv[2]
if (!source) {
  console.error('usage: node scripts/make-dev-graph.mjs /path/to/graph.json')
  process.exit(1)
}

const graph = JSON.parse(readFileSync(source, 'utf8'))
const hasMb = graph.nodes.some((n) => n.node_type.startsWith('mb_'))

if (!hasMb) {
  const columns = graph.nodes
    .filter(
      (n) =>
        n.node_type === 'column' &&
        /\.(fct_|dim_|mart_)/.test(n.node_id) &&
        n.column !== '*' &&
        !n.node_id.startsWith('model.elementary.'),
    )
    .sort((a, b) => a.node_id.localeCompare(b.node_id))
    .filter((_, i) => i % 37 === 0) // deterministic spread
    .slice(0, 12)

  const dashboards = [
    { node_id: 'mb_dash::1', node_type: 'mb_dashboard', name: 'Board dashboard', properties: { collection_name: 'Board', synthetic_dev: true } },
    { node_id: 'mb_dash::2', node_type: 'mb_dashboard', name: 'Growth weekly', properties: { collection_name: 'Growth', synthetic_dev: true } },
  ]
  const nodes = [...dashboards]
  const edges = []
  columns.forEach((column, i) => {
    const fieldId = `mb_field::${9000 + i}`
    const cardId = `mb_card::${400 + i}`
    nodes.push({
      node_id: fieldId,
      node_type: 'mb_field',
      name: column.name,
      column: column.column,
      table: column.table,
      schema: column.schema,
      properties: { synthetic_dev: true },
    })
    nodes.push({
      node_id: cardId,
      node_type: 'mb_card',
      name: `${(column.name || 'metric').replace(/_/g, ' ')} — card ${400 + i}`,
      properties: {
        creator: 'sverrir',
        collection_name: i % 2 === 0 ? 'Board' : 'Growth',
        archived: i === 5,
        synthetic_dev: true,
      },
    })
    edges.push({ from: column.node_id, to: fieldId, edge_type: 'binds_to', confidence: i % 4 === 0 ? 'fuzzy' : 'exact', evidence: { source: 'dev-fixture' } })
    edges.push({ from: fieldId, to: cardId, edge_type: 'consumed_by', confidence: 'exact', evidence: { source: 'dev-fixture' } })
    edges.push({ from: cardId, to: i % 2 === 0 ? 'mb_dash::1' : 'mb_dash::2', edge_type: 'appears_on', confidence: 'exact', evidence: { source: 'dev-fixture' } })
  })
  graph.nodes.push(...nodes)
  graph.edges.push(...edges)
  console.log(`no Metabase nodes in source graph — appended ${nodes.length} synthetic BI nodes for dev`)
}

const here = dirname(fileURLToPath(import.meta.url))
const out = join(here, '..', 'dev-public', 'dev-graph.json')
mkdirSync(dirname(out), { recursive: true })
writeFileSync(out, JSON.stringify(graph))
console.log(`wrote ${out} (${graph.nodes.length} nodes, ${graph.edges.length} edges)`)
