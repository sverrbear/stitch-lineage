// Small shared presentational pieces: node chips, confidence tags, section
// headers, the graph-view legend.

import type { ReactNode } from 'react'
import type { Confidence, GraphNode } from '../types'
import { nodeHref } from '../router'
import { NODE_TYPE_NAME, SystemBadge, MetabaseMark, SnowflakeMark } from './badges'

export function ConfidenceTag({ confidence }: { confidence: Confidence }) {
  return <span className={`conf-tag conf-${confidence}`}>{confidence}</span>
}

/** Inline, linked reference to a node: badge + name (+ optional context). */
export function NodeChip({
  node,
  context,
  confidence,
}: {
  node: GraphNode
  context?: string | null
  confidence?: Confidence
}) {
  return (
    <a className="node-chip" href={nodeHref(node.node_id)} title={node.node_id}>
      <SystemBadge nodeType={node.node_type} />
      <span className="node-chip-name">{node.name}</span>
      <span className="node-chip-type">{NODE_TYPE_NAME[node.node_type]}</span>
      {context ? <span className="node-chip-context">{context}</span> : null}
      {confidence && confidence !== 'exact' ? <ConfidenceTag confidence={confidence} /> : null}
    </a>
  )
}

export function ChipList({ nodes }: { nodes: Array<{ node: GraphNode; confidence?: Confidence }> }) {
  if (nodes.length === 0) return <p className="muted">none</p>
  return (
    <div className="chip-list">
      {nodes.map(({ node, confidence }) => (
        <NodeChip key={node.node_id} node={node} confidence={confidence} />
      ))}
    </div>
  )
}

export function Section({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <section className="panel-section">
      <h3>{title}</h3>
      {children}
    </section>
  )
}

/** Footer legend for the lineage / ERD canvases (spec §9 + confidence styling). */
export function GraphLegend({ erd = false }: { erd?: boolean }) {
  return (
    <div className="graph-legend">
      <span className="legend-item">
        <SnowflakeMark size={12} /> dbt / warehouse
      </span>
      <span className="legend-item">
        <MetabaseMark size={12} /> Metabase / BI
      </span>
      {erd ? (
        <>
          <span className="legend-item">
            <svg width="26" height="8">
              <line x1="0" y1="4" x2="26" y2="4" className="legend-line-solid" />
            </svg>
            declared relationship
          </span>
          <span className="legend-item">
            <span className="validated-badge">✓</span> validated (relationships test)
          </span>
        </>
      ) : (
        <>
          <span className="legend-item">
            <svg width="26" height="8">
              <line x1="0" y1="4" x2="26" y2="4" className="legend-line-solid" />
            </svg>
            exact
          </span>
          <span className="legend-item">
            <svg width="26" height="8">
              <line x1="0" y1="4" x2="26" y2="4" className="legend-line-dashed" />
            </svg>
            parsed / inferred / fuzzy — hover an edge for evidence
          </span>
        </>
      )}
    </div>
  )
}
