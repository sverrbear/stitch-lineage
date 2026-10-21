import { isErdTable, listScopes, scopeModelIds, type ErdScope } from './erd'
import { modelIdOfColumn, type GraphIndex } from './graph'
import type { GraphNode } from '../types'

/** A column keeps its identity while Relationships uses its owning table. */
export function relationshipTable(index: GraphIndex, node: GraphNode): GraphNode | null {
  const ownerId = node.node_type === 'column' ? modelIdOfColumn(node.node_id) : node.node_id
  const table = ownerId ? index.nodesById.get(ownerId) : undefined
  return table && isErdTable(table) ? table : null
}

/** Only open a scope that actually contains this table, never the global default. */
export function relationshipScope(index: GraphIndex, table: GraphNode): ErdScope | null {
  const scopes = listScopes(index)
  return scopes.find((scope) => scope.kind === 'schema' && scope.value === table.schema)
    ?? scopes.find((scope) => scopeModelIds(index, scope).has(table.node_id))
    ?? null
}
