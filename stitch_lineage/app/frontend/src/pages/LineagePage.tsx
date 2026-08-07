// Column/model lineage canvas (spec §9): the subgraph REACHABLE from the
// selected node — upstream to sources, downstream through models -> mb_field
// -> card -> dashboard. Left-to-right layered layout (hand-rolled BFS layering
// + barycenter ordering, no dagre). Edge style encodes confidence: exact =
// solid, parsed/inferred/fuzzy = dashed with an evidence tooltip.

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
import { NODE_TYPE_NAME, SystemBadge } from '../components/badges'
import { GraphLegend } from '../components/bits'
import { useStitch } from '../data'
import { CLICK_SLOP_PX } from '../lib/canvas'
import { lineageFor, layoutLineage } from '../lib/lineage'
import { navigate, nodeHref } from '../router'
import type { Confidence, GraphNode } from '../types'

type LineageFlowNode = Node<{ node: GraphNode; isRoot: boolean }, 'lineage'>

function LineageNode({ data }: NodeProps<LineageFlowNode>) {
  const { node, isRoot } = data
  return (
    <div className={`flow-node system-${node.node_type.startsWith('mb_') ? 'mb' : 'dbt'}${isRoot ? ' root' : ''}`}>
      <Handle type="target" position={Position.Left} className="flow-handle" />
      <div className="flow-node-title">
        <SystemBadge nodeType={node.node_type} />
        <span className="flow-node-name">{node.name}</span>
      </div>
      <div className="flow-node-sub">
        {NODE_TYPE_NAME[node.node_type]}
        {node.node_type === 'column' && node.table ? ` · ${node.table}` : ''}
        {node.properties?.archived === true ? ' · archived' : ''}
      </div>
      <Handle type="source" position={Position.Right} className="flow-handle" />
    </div>
  )
}

const nodeTypes = { lineage: LineageNode }

const DASHED: ReadonlySet<Confidence> = new Set(['parsed', 'inferred', 'fuzzy', 'declared'])

interface EdgeTip {
  x: number
  y: number
  text: string
}

export function LineagePage({ nodeId }: { nodeId: string }) {
  const { index } = useStitch()
  const [tip, setTip] = useState<EdgeTip | null>(null)

  const root = index.nodesById.get(nodeId)

  const { nodes, edges, truncated } = useMemo(() => {
    if (!root) return { nodes: [] as LineageFlowNode[], edges: [] as Edge[], truncated: false }
    const lineage = lineageFor(index, nodeId)
    const positions = layoutLineage(lineage)
    const flowNodes: LineageFlowNode[] = lineage.nodes.map((node) => ({
      id: node.node_id,
      type: 'lineage',
      position: positions.get(node.node_id) ?? { x: 0, y: 0 },
      data: { node, isRoot: node.node_id === nodeId },
    }))
    const flowEdges: Edge[] = lineage.edges.map((edge) => {
      const dashed = DASHED.has(edge.confidence)
      const evidence = Object.entries(edge.evidence ?? {})
        .map(([key, value]) => `${key}: ${String(value)}`)
        .join('\n')
      return {
        id: `${edge.from}->${edge.to}:${edge.edge_type}`,
        source: edge.from,
        target: edge.to,
        className: `lineage-edge conf-${edge.confidence}`,
        style: dashed ? { strokeDasharray: '6 4' } : undefined,
        data: {
          tooltip: `${edge.edge_type} · confidence: ${edge.confidence}${evidence ? `\n${evidence}` : ''}`,
        },
      }
    })
    return { nodes: flowNodes, edges: flowEdges, truncated: lineage.truncated }
  }, [index, nodeId, root])

  if (!root) {
    return (
      <main className="graph-page">
        <p className="muted panel">Unknown node: {nodeId}</p>
      </main>
    )
  }

  return (
    <main className="graph-page">
      <div className="graph-toolbar">
        <span className="graph-toolbar-title">
          <SystemBadge nodeType={root.node_type} /> Lineage of <strong>{root.name}</strong>
          <span className="muted"> ({NODE_TYPE_NAME[root.node_type]})</span>
        </span>
        <a className="button" href={nodeHref(nodeId)}>
          Details
        </a>
        {truncated && <span className="muted">large fan-out truncated</span>}
        <span className="muted graph-toolbar-hint">click a node for details</span>
      </div>
      <div className="graph-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          minZoom={0.05}
          nodesConnectable={false}
          nodesDraggable
          elementsSelectable
          nodeClickDistance={CLICK_SLOP_PX}
          proOptions={{ hideAttribution: true }}
          onNodeClick={(_, node) => navigate(nodeHref(node.id))}
          onEdgeMouseEnter={(event, edge) => {
            const text = (edge.data as { tooltip?: string } | undefined)?.tooltip
            if (text) setTip({ x: event.clientX, y: event.clientY, text })
          }}
          onEdgeMouseLeave={() => setTip(null)}
        >
          <Background gap={24} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
        {tip && (
          <div className="edge-tooltip" style={{ left: tip.x + 12, top: tip.y + 12 }}>
            {tip.text}
          </div>
        )}
      </div>
      <GraphLegend />
    </main>
  )
}

