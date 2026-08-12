// Column/model lineage canvas (spec §9): the subgraph REACHABLE from the
// selected node — upstream to sources, downstream through models -> mb_field
// -> card -> dashboard. Left-to-right layered layout (hand-rolled BFS layering
// + barycenter ordering, no dagre). Edge style encodes confidence: exact =
// solid, parsed/inferred/fuzzy = dashed with an evidence tooltip.

import {
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from '@xyflow/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { SystemBadge } from '../components/badges'
import { GraphLegend } from '../components/bits'
import { useStitch } from '../data'
import { CLICK_SLOP_PX } from '../lib/canvas'
import { metabaseLink, type GraphIndex } from '../lib/graph'
import { LineageRoutedEdge } from '../components/LineageEdge'
import { edgeFans, lineageFor, layoutLineage } from '../lib/lineage'
import { CONFIDENCE_HELP, NODE_TYPE_NAME, displayName, nodeContext } from '../lib/present'
import { entityIdOf, layerEntities, rollUp, type Grain, type RollupEdge } from '../lib/rollup'
import { lineageHref, navigate, nodeHref } from '../router'
import type { Confidence, GraphEdge, GraphNode } from '../types'
import { copy } from '../copy'

/** One card shape for all six node types: badge + name + type · context. */
type LineageFlowNode = Node<{ node: GraphNode; context: string | null; isRoot: boolean }, 'lineage'>

/**
 * Where a node's NAME goes when clicked (#140): the thing itself. A Metabase card
 * or dashboard opens in Metabase; everything else opens its detail page here.
 *
 * A field gets the detail page too. Metabase's own field URLs need the database and
 * table ids as well, and the graph carries only the field id — so rather than build
 * a link that 404s, a field's name goes where a dbt node's does.
 */
export function titleTarget(
  node: GraphNode,
  metabaseUrl: string | null,
): { href: string; external: boolean } {
  const link = metabaseLink(metabaseUrl, node)
  return link ? { href: link, external: true } : { href: nodeHref(node.node_id), external: false }
}

function LineageNode({ data }: NodeProps<LineageFlowNode>) {
  const { node, context, isRoot } = data
  const { meta } = useStitch()
  const name = displayName(node)
  const target = titleTarget(node, meta.metabase_url)
  return (
    <div className={`flow-node system-${node.node_type.startsWith('mb_') ? 'mb' : 'dbt'}${isRoot ? ' root' : ''}`}>
      <Handle type="target" position={Position.Left} className="flow-handle" />
      <div className="flow-node-title">
        <SystemBadge nodeType={node.node_type} />
        {/* The name is the ONLY part of the card that navigates; the body re-roots
            the trace. stopPropagation is what keeps the two apart — React Flow's
            onNodeClick sits on the wrapper, so a click that reaches it is a click
            that missed this link. */}
        <a
          className="flow-node-name flow-node-link"
          href={target.href}
          title={
            target.external
              ? copy.lineage.openInMetabase(name)
              : copy.lineage.openDetail(NODE_TYPE_NAME[node.node_type], name)
          }
          target={target.external ? '_blank' : undefined}
          rel={target.external ? 'noreferrer' : undefined}
          onClick={(event) => event.stopPropagation()}
        >
          {name}
        </a>
      </div>
      <div className="flow-node-sub" title={context ?? undefined}>
        <span className="flow-node-kind">{NODE_TYPE_NAME[node.node_type]}</span>
        {context ? <span className="flow-node-context">{context}</span> : null}
        {node.properties?.archived === true ? <span className="flow-node-flag">archived</span> : null}
      </div>
      <Handle type="source" position={Position.Right} className="flow-handle" />
    </div>
  )
}

const nodeTypes = { lineage: LineageNode }
// Fanned ends, and routed around the cards where the drawing is small enough (#176).
const edgeTypes = { lineage: LineageRoutedEdge }
const EDGE_TYPE = 'lineage'

/** The same reachable subgraph at table grain, laid out by the shared layerer. */
function rolledView(
  index: GraphIndex,
  nodes: GraphNode[],
  edges: GraphEdge[],
): { nodes: GraphNode[]; edges: RollupEdge[]; layers: Map<string, number> } {
  const rolled = rollUp(index, nodes, edges)
  const rolledNodes = rolled.nodes.map((entry) => entry.node)
  return {
    nodes: rolledNodes,
    edges: rolled.edges,
    layers: layerEntities(
      rolledNodes.map((node) => node.node_id),
      rolled.edges,
    ),
  }
}

const DASHED: ReadonlySet<Confidence> = new Set(['parsed', 'inferred', 'fuzzy', 'declared'])

interface EdgeTip {
  x: number
  y: number
  text: string
}

export function LineagePage({ nodeId, grain }: { nodeId: string; grain: Grain }) {
  const { index } = useStitch()
  const [tip, setTip] = useState<EdgeTip | null>(null)

  const root = index.nodesById.get(nodeId)
  const rootContext = root ? nodeContext(index, root) : null
  // At table grain the focus is the entity the selected node belongs to, so
  // flipping the toggle on a column lands on its model instead of nothing.
  const rootEntityId = root ? (entityIdOf(root) ?? nodeId) : nodeId

  const { nodes, edges, truncated, impact } = useMemo(() => {
    if (!root) {
      return {
        nodes: [] as LineageFlowNode[],
        edges: [] as Edge[],
        truncated: false,
        impact: { cards: 0, dashboards: 0 },
      }
    }
    const lineage = lineageFor(index, nodeId)
    const focusId = grain === 'table' ? rootEntityId : nodeId
    const view =
      grain === 'table'
        ? rolledView(index, lineage.nodes, lineage.edges)
        : { nodes: lineage.nodes, edges: lineage.edges, layers: lineage.layers }
    const positions = layoutLineage(view, grain === 'table' ? { rowHeight: 92 } : {})
    const flowNodes: LineageFlowNode[] = view.nodes.map((node) => ({
      id: node.node_id,
      type: 'lineage',
      position: positions.get(node.node_id) ?? { x: 0, y: 0 },
      data: { node, context: nodeContext(index, node), isRoot: node.node_id === focusId },
    }))
    // Edges meeting a card at the same pixel are pulled apart, so two relationships
    // arriving at one column row read as two lines (#176). Computed over the whole
    // list, because a fan is a property of the group and not of any one edge.
    const fans = edgeFans(
      view.edges.map((edge) => ({ source: edge.from, target: edge.to })),
    )
    const flowEdges: Edge[] = view.edges.map((edge, i) => {
      const dashed = DASHED.has(edge.confidence)
      const rollupWeight = 'weight' in edge ? (edge as RollupEdge).weight : null
      const evidence =
        rollupWeight === null
          ? Object.entries((edge as GraphEdge).evidence ?? {})
              .map(([key, value]) => `${key}: ${String(value)}`)
              .join('\n')
          : `${rollupWeight} contributing column${rollupWeight === 1 ? '' : 's'}`
      const kind = 'edge_type' in edge ? (edge as GraphEdge).edge_type : 'rolled up'
      return {
        id: `${edge.from}->${edge.to}:${kind}`,
        source: edge.from,
        target: edge.to,
        type: EDGE_TYPE,
        className: `lineage-edge conf-${edge.confidence}`,
        style: {
          strokeDasharray: dashed ? '6 4' : undefined,
          strokeWidth: rollupWeight === null ? undefined : Math.min(5, 1 + Math.log2(Math.max(1, rollupWeight))),
        },
        data: {
          tooltip: `${kind} · ${edge.confidence}\n${CONFIDENCE_HELP[edge.confidence]}${evidence ? `\n${evidence}` : ''}`,
          fan: fans.get(i),
        },
      }
    })
    // The question is "what breaks if I change this?", so the answer leads and
    // the topology below is the evidence for it (principle 01).
    const impact = { cards: 0, dashboards: 0 }
    for (const node of view.nodes) {
      if (node.node_type === 'mb_card') impact.cards += 1
      else if (node.node_type === 'mb_dashboard') impact.dashboards += 1
    }
    return { nodes: flowNodes, edges: flowEdges, truncated: lineage.truncated, impact }
  }, [index, nodeId, root, grain, rootEntityId])

  /**
   * The canvas needs its OWN copy of the nodes and an `onNodesChange` to write
   * back into, because that callback is the only route React Flow has to record
   * what it measured each card at. Without it the store's nodes stay unmeasured
   * however big they render, and anything downstream of a measurement gets
   * nothing to work with — which is why the minimap was drawing zero rectangles
   * and reading as a blank white panel bottom-right (#122).
   *
   * A new lineage is a new drawing, so it replaces this wholesale rather than
   * merging: the layout is computed per root and grain, and carrying anything
   * over from the previous one would be a stale position.
   */
  const [flowNodes, setFlowNodes] = useState<LineageFlowNode[]>(nodes)
  useEffect(() => setFlowNodes(nodes), [nodes])
  const onNodesChange = useCallback(
    (changes: NodeChange<LineageFlowNode>[]) =>
      setFlowNodes((current) => applyNodeChanges(changes, current)),
    [],
  )

  if (!root) {
    return (
      <main className="graph-page">
        <p className="muted panel">{copy.lineage.unknownNode(nodeId)}</p>
      </main>
    )
  }

  return (
    <main className="graph-page">
      <div className="graph-toolbar">
        <span className="graph-toolbar-title">
          <SystemBadge nodeType={root.node_type} />
          {/* a column is qualified by its model, because a bare column name is
              the ambiguity that costs an afternoon (principle 02) */}
          <span className="graph-toolbar-name" title={root.node_id}>
            {root.node_type === 'column' && rootContext ? `${rootContext}.` : ''}
            {displayName(root)}
          </span>
          <span className="muted">
            {NODE_TYPE_NAME[root.node_type]}
            {root.node_type !== 'column' && rootContext ? ` · ${rootContext}` : ''}
          </span>
        </span>
        {/* The answer, before the graph that supports it — but a truncated walk
            stops before it has seen everything, and "0 dashboards" off a capped
            BFS is a confidently wrong answer. Past the cap the counts are floors
            and say so, in the colour this app uses for "may be incomplete". */}
        <span
          className={`graph-toolbar-impact${truncated ? ' partial' : ''}`}
          title={
            truncated
              ? copy.lineage.truncatedCounts
              : undefined
          }
        >
          {impact.cards.toLocaleString()}
          {truncated ? '+' : ''} card{impact.cards === 1 && !truncated ? '' : 's'} ·{' '}
          {impact.dashboards.toLocaleString()}
          {truncated ? '+' : ''} dashboard{impact.dashboards === 1 && !truncated ? '' : 's'}
        </span>
        <div className="grain-toggle" role="group" aria-label={copy.lineage.grainLabel}>
          {(['column', 'table'] as const).map((option) => (
            <a
              key={option}
              className={`grain-option${grain === option ? ' active' : ''}`}
              href={lineageHref(nodeId, option)}
              title={
                option === 'column'
                  ? copy.lineage.grainColumn
                  : copy.lineage.grainTable
              }
            >
              {option}
            </a>
          ))}
        </div>
        <a className="button" href={nodeHref(nodeId)}>
          {copy.lineage.details}
        </a>
        {truncated && <span className="muted">{copy.lineage.truncated}</span>}
        <span className="muted graph-toolbar-hint">{copy.lineage.hint}</span>
      </div>
      <div className="graph-canvas">
        <ReactFlow
          nodes={flowNodes}
          onNodesChange={onNodesChange}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          minZoom={0.05}
          nodesConnectable={false}
          // The fan-out layout is computed per root, and this page has no "reset
          // view" to undo a drag with — so it is a read-only picture, which is
          // what it has always been in practice (drags never survived a render).
          nodesDraggable={false}
          elementsSelectable
          nodeClickDistance={CLICK_SLOP_PX}
          proOptions={{ hideAttribution: true }}
          // The body of a card re-roots the trace on it, in place (#140): exploring a
          // chain is click-click-click along it, with no detour through search or a
          // detail panel. Navigating to the element itself is the NAME's job, and
          // the Details button's -- both of which stop the click before it lands here.
          onNodeClick={(_, node) => {
            if (node.id === nodeId) return
            navigate(lineageHref(node.id, grain))
          }}
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

