// Small shared presentational pieces: node chips, confidence tags, section
// headers, the graph-view legend. Names and context come from lib/present, so a
// node reads identically here, in the canvases and in search results.

import type { ReactNode } from 'react'
import type { Confidence, GraphNode } from '../types'
import { useStitch } from '../data'
import {
  CONFIDENCE_HELP,
  CONFIDENCE_LABEL,
  NODE_TYPE_NAME,
  displayName,
  nodeContext,
} from '../lib/present'
import { nodeHref } from '../router'
import { SystemBadge, MetabaseMark, SnowflakeMark } from './badges'

/**
 * Work is happening (#160). Decoration only: it carries no text, so every use must
 * put the words next to it — a spinner alone says "wait" without saying what for.
 *
 * Under prefers-reduced-motion it stops turning and stays a ring, because the label
 * beside it is what actually reports the state (same trade the .erd-reset pulse makes).
 */
export function Spinner({ label }: { label?: string }) {
  return <span className="spinner" role="presentation" aria-hidden="true" title={label} />
}

export function ConfidenceTag({ confidence }: { confidence: Confidence }) {
  return (
    <span className={`conf-tag conf-${confidence}`} title={CONFIDENCE_HELP[confidence]}>
      {CONFIDENCE_LABEL[confidence]}
    </span>
  )
}

/**
 * Inline, linked reference to a node: badge + name + what it belongs to.
 * Context defaults to the shared rule (a column shows its dbt model, a Metabase
 * field its table); pass `context={null}` to suppress it.
 */
export function NodeChip({
  node,
  context,
  confidence,
}: {
  node: GraphNode
  context?: string | null
  confidence?: Confidence
}) {
  const { index } = useStitch()
  const shown = context === undefined ? nodeContext(index, node) : context
  return (
    <a className="node-chip" href={nodeHref(node.node_id)} title={node.node_id}>
      <SystemBadge nodeType={node.node_type} />
      <span className="node-chip-name">{displayName(node)}</span>
      <span className="node-chip-type">{NODE_TYPE_NAME[node.node_type]}</span>
      {shown ? <span className="node-chip-context">{shown}</span> : null}
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

/** A `dt`/`dd` pair that renders nothing when there is no value to show. */
export function Fact({ label, children }: { label: string; children?: ReactNode }) {
  if (children === null || children === undefined || children === '') return null
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  )
}

/** Footer legend for the lineage / ERD / map canvases (spec §9 + confidence styling). */
export function GraphLegend({
  erd = false,
  rollup = false,
  staged = false,
  suggested = false,
}: {
  erd?: boolean
  rollup?: boolean
  staged?: boolean
  suggested?: boolean
}) {
  return (
    <div className="graph-legend">
      <span className="legend-item">
        <SnowflakeMark size={12} /> dbt / warehouse
      </span>
      <span className="legend-item">
        <MetabaseMark size={12} /> Metabase / BI
      </span>
      {rollup && (
        <span className="legend-item">
          <svg width="26" height="10">
            <line x1="0" y1="5" x2="26" y2="5" className="legend-line-solid" strokeWidth={4} />
          </svg>
          thickness = contributing columns
        </span>
      )}
      {erd ? (
        <>
          <span className="legend-item">
            <svg width="26" height="8">
              <line x1="0" y1="4" x2="26" y2="4" className="legend-line-solid" />
            </svg>
            declared relationship — hover or click it to light the columns it joins
          </span>
          <span className="legend-item">
            <span className="validated-badge">✓</span> validated (relationships test)
          </span>
          {staged && (
            <span className="legend-item">
              <svg width="26" height="8">
                <line x1="0" y1="4" x2="26" y2="4" className="legend-line-dashed" />
              </svg>
              staged — not in the repo until <code>stitch apply</code>
            </span>
          )}
          {suggested && (
            <span className="legend-item">
              <svg width="26" height="8">
                <line x1="0" y1="4" x2="26" y2="4" className="legend-line-dotted" />
              </svg>
              suggested — nobody has declared this yet
            </span>
          )}
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
            parsed / inferred / name match — hover an edge for evidence
          </span>
        </>
      )}
    </div>
  )
}
