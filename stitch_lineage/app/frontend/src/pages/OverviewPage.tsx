// The whole pipeline as one map (#46): table grain, laid out left to right by
// dependency order, Metabase consumption aggregated on the right. The per-node
// lineage view is the street view; this is the map.

import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import { useMemo, useState } from 'react'
import { SystemBadge } from '../components/badges'
import { GraphLegend } from '../components/bits'
import { useStitch } from '../data'
import { CLICK_SLOP_PX } from '../lib/canvas'
import { erdClickHref, listScopes } from '../lib/erd'
import { layoutLineage } from '../lib/lineage'
import { overviewFor, type MetabaseMode, type OverviewNode, type OverviewScope } from '../lib/overview'
import { NODE_TYPE_NAME, displayName, nodeContext } from '../lib/present'
import { navigate } from '../router'

const ALL_SCOPES = 'all'

type OverviewFlowNode = Node<{ entry: OverviewNode; context: string | null; dimmed: boolean }, 'overview'>

function OverviewNodeCard({ data }: NodeProps<OverviewFlowNode>) {
  const { entry, context, dimmed } = data
  const { node } = entry
  const isDbt = node.node_type === 'model' || node.node_type === 'source'
  return (
    <div
      className={`map-node system-${node.node_type.startsWith('mb_') ? 'mb' : 'dbt'}${dimmed ? ' dimmed' : ''}`}
    >
      <Handle type="target" position={Position.Left} className="flow-handle" />
      <div className="map-node-title">
        <SystemBadge nodeType={node.node_type} />
        <span className="map-node-name">{displayName(node)}</span>
      </div>
      <div className="map-node-sub">
        <span>{NODE_TYPE_NAME[node.node_type]}</span>
        {context ? <span className="map-node-context">{context}</span> : null}
      </div>
      {isDbt && (
        <div className="map-node-meta">
          {entry.columnCount} cols
          {entry.cardCount > 0 ? ` · ${entry.cardCount} cards` : ''}
          {entry.dashboardCount > 0 ? ` · ${entry.dashboardCount} dashboards` : ''}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="flow-handle" />
    </div>
  )
}

const nodeTypes = { overview: OverviewNodeCard }

const METABASE_MODES: Array<{ value: MetabaseMode; label: string }> = [
  { value: 'dashboards', label: 'per dashboard' },
  { value: 'cards', label: 'per card' },
  { value: 'none', label: 'hidden' },
]

/** Edge thickness reads as "how much flows here" without needing a legend entry. */
function strokeWidth(weight: number): number {
  if (weight <= 1) return 1
  return Math.min(5, 1 + Math.log2(weight))
}

export function OverviewPage() {
  const { index } = useStitch()
  const scopes = useMemo(() => listScopes(index), [index])
  const [scopeKey, setScopeKey] = useState<string>(ALL_SCOPES)
  const [metabase, setMetabase] = useState<MetabaseMode>('dashboards')
  const [includeInternal, setIncludeInternal] = useState(false)
  const [highlight, setHighlight] = useState('')

  const scope = useMemo<OverviewScope | null>(() => {
    if (scopeKey === ALL_SCOPES) return null
    const separator = scopeKey.indexOf(':')
    const kind = scopeKey.slice(0, separator)
    if (kind !== 'schema' && kind !== 'tag') return null
    return { kind, value: scopeKey.slice(separator + 1) }
  }, [scopeKey])

  const overview = useMemo(
    () => overviewFor(index, { scope, metabase, includeInternal }),
    [index, scope, metabase, includeInternal],
  )

  const { nodes, edges } = useMemo(() => {
    const positions = layoutLineage(
      {
        nodes: overview.nodes.map((entry) => ({ node_id: entry.node.node_id })),
        edges: overview.edges,
        layers: overview.layers,
      },
      { columnWidth: 320, rowHeight: 92 },
    )
    const needle = highlight.trim().toLowerCase()
    const flowNodes: OverviewFlowNode[] = overview.nodes.map((entry) => ({
      id: entry.node.node_id,
      type: 'overview',
      position: positions.get(entry.node.node_id) ?? { x: 0, y: 0 },
      data: {
        entry,
        context: nodeContext(index, entry.node),
        dimmed: needle !== '' && !displayName(entry.node).toLowerCase().includes(needle),
      },
    }))
    const flowEdges: Edge[] = overview.edges.map((edge, i) => ({
      id: `rollup-${i}`,
      source: edge.from,
      target: edge.to,
      className: `map-edge conf-${edge.confidence}`,
      style: {
        strokeWidth: strokeWidth(edge.weight),
        strokeDasharray: edge.confidence === 'exact' ? undefined : '6 4',
      },
    }))
    return { nodes: flowNodes, edges: flowEdges }
  }, [overview, highlight, index])

  const omitted = [
    overview.omitted.internal > 0 ? `${overview.omitted.internal} in tooling schemas` : null,
    overview.omitted.outOfScope > 0 ? `${overview.omitted.outOfScope} out of scope` : null,
    overview.omitted.metabase > 0 ? `${overview.omitted.metabase} Metabase nodes` : null,
  ].filter(Boolean)

  return (
    <main className="graph-page">
      <div className="graph-toolbar">
        <span className="graph-toolbar-title">
          <strong>Pipeline map</strong>
          <span className="muted"> table grain</span>
        </span>
        <label className="scope-label" htmlFor="overview-scope">
          Scope
        </label>
        <select
          id="overview-scope"
          className="scope-select"
          value={scopeKey}
          onChange={(e) => setScopeKey(e.target.value)}
        >
          <option value={ALL_SCOPES}>everything</option>
          <optgroup label="Schemas">
            {scopes
              .filter((s) => s.kind === 'schema' && !s.internal)
              .map((s) => (
                <option key={`schema:${s.value}`} value={`schema:${s.value}`}>
                  {s.value} ({s.modelCount})
                </option>
              ))}
          </optgroup>
          <optgroup label="dbt tags">
            {scopes
              .filter((s) => s.kind === 'tag')
              .map((s) => (
                <option key={`tag:${s.value}`} value={`tag:${s.value}`}>
                  {s.value} ({s.modelCount})
                </option>
              ))}
          </optgroup>
        </select>
        <label className="scope-label" htmlFor="overview-metabase">
          Metabase
        </label>
        <select
          id="overview-metabase"
          className="scope-select"
          value={metabase}
          onChange={(e) => setMetabase(e.target.value as MetabaseMode)}
        >
          {METABASE_MODES.map((mode) => (
            <option key={mode.value} value={mode.value}>
              {mode.label}
            </option>
          ))}
        </select>
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={includeInternal}
            onChange={(e) => setIncludeInternal(e.target.checked)}
          />
          tooling schemas
        </label>
        <input
          className="highlight-input"
          type="search"
          value={highlight}
          onChange={(e) => setHighlight(e.target.value)}
          placeholder="highlight…"
          aria-label="Highlight nodes by name"
          spellCheck={false}
        />
        <span className="muted">
          {overview.counts.model} models · {overview.counts.source} sources
          {overview.counts.card > 0 ? ` · ${overview.counts.card} cards` : ''}
          {overview.counts.dashboard > 0 ? ` · ${overview.counts.dashboard} dashboards` : ''} ·{' '}
          {overview.edges.length} edges
        </span>
        {omitted.length > 0 && <span className="muted">not drawn: {omitted.join(', ')}</span>}
        <span className="muted graph-toolbar-hint">click a node for details · ⌘/Ctrl-click for lineage</span>
      </div>
      <div className="graph-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.02}
          nodesConnectable={false}
          nodesDraggable
          nodeClickDistance={CLICK_SLOP_PX}
          proOptions={{ hideAttribution: true }}
          onNodeClick={(event, node) => navigate(erdClickHref(node.id, event))}
        >
          <Background gap={24} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
      <GraphLegend rollup />
    </main>
  )
}
