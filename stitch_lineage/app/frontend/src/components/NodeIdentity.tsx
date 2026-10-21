import { createContext } from 'react'
import { NodeBadge } from './badges'
import { copy } from '../copy'
import { useStitch } from '../data'
import { metabaseLink } from '../lib/graph'
import { displayName, isPlaceholder, NODE_TYPE_NAME, nodeContext, ownerName } from '../lib/present'
import type { GraphNode } from '../types'

/** Summary panels keep their description editors, while the workspace owns identity. */
export const ObjectWorkspaceContext = createContext<{ nodeId: string; summaryActive: boolean } | null>(null)

export function NodeIdentity({ node }: { node: GraphNode }) {
  const { index, meta } = useStitch()
  const owner = node.node_type === 'column' ? ownerName(index, node) : null
  const context = node.node_type === 'column' ? null : nodeContext(index, node)
  const link = metabaseLink(meta.metabase_url, node)
  return (
    <div className="object-identity">
      <div className="panel-title">
        <NodeBadge node={node} size={20} />
        <h1 title={node.node_id}>
          {owner && <span className="panel-name-owner">{owner}.</span>}
          {displayName(node)}
        </h1>
        <span className="panel-type">{NODE_TYPE_NAME[node.node_type]}</span>
        {node.properties?.archived === true && <span className="archived-tag">{copy.workspace.archived}</span>}
        {link && <a className="button object-external" href={link} target="_blank" rel="noreferrer">{copy.node.openInMetabase}</a>}
      </div>
      {context && <p className="panel-subtitle">{context}</p>}
      {isPlaceholder(node) && <p className="muted panel-placeholder">{copy.node.placeholder}</p>}
    </div>
  )
}
