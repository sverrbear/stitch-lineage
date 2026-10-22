// Readable column lineage: model blocks contain only columns in this trace.
// The complete dependency answer is independent of the progressive drawing.
import {
  applyNodeChanges, Background, Controls, Handle, MiniMap, Position, ReactFlow,
  type Edge, type Node, type NodeChange, type NodeProps, type ReactFlowInstance,
} from '@xyflow/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { NodeBadge } from '../components/badges'
import { GraphLegend } from '../components/bits'
import { LineageRoutedEdge } from '../components/LineageEdge'
import { copy } from '../copy'
import { useStitch } from '../data'
import { canvasSettled, CLICK_SLOP_PX } from '../lib/canvas'
import { readableViewport } from '../lib/diagramViewport'
import { metabaseLink } from '../lib/graph'
import { edgeFans, lineageFor, layoutLineage } from '../lib/lineage'
import { initialLineageBlocks, neighbours, presentLineage, type LineageBlock } from '../lib/lineagePresentation'
import { CONFIDENCE_HELP, displayName, managerOfNode, NODE_TYPE_NAME, nodeContext } from '../lib/present'
import { layerEntities, type Grain } from '../lib/rollup'
import { lineageHref, navigate, nodeHref } from '../router'
import type { GraphNode } from '../types'

type LineageFlowNode = Node<{
  block: LineageBlock
  context: string | null
  isRoot: boolean
  rootId: string
  grain: Grain
  expanded: boolean
  onToggle: () => void
  up: number
  down: number
  onReveal: (direction: 'up' | 'down') => void
}, 'lineage'>

export function titleTarget(
  node: GraphNode,
  metabaseUrl: string | null,
): { href: string; external: boolean } {
  const link = metabaseLink(metabaseUrl, node)
  return link ? { href: link, external: true } : { href: nodeHref(node.node_id), external: false }
}


function LineageNode({ data }: NodeProps<LineageFlowNode>) {
  const { block, context, isRoot, rootId, grain, expanded, onToggle, up, down, onReveal } = data
  const { meta } = useStitch()
  const { node, columns, bindings } = block
  const name = displayName(node)
  const target = titleTarget(node, meta.metabase_url)
  const shown = expanded ? columns : columns.slice(0, 1)
  return (
    <div className={`flow-node lineage-block system-${managerOfNode(node)}${isRoot ? ' root' : ''}`}>
      <Handle type="target" position={Position.Left} className="flow-handle" />
      <div className="flow-node-title">
        <NodeBadge node={node} />
        <a className="flow-node-name flow-node-link" href={target.href}
          title={target.external ? copy.lineage.openInMetabase(name) : copy.lineage.openDetail(NODE_TYPE_NAME[node.node_type], name)}
          target={target.external ? '_blank' : undefined} rel={target.external ? 'noreferrer' : undefined}
          onClick={(event) => event.stopPropagation()}>{name}</a>
      </div>
      <div className="flow-node-sub">
        <span className="flow-node-kind">{NODE_TYPE_NAME[node.node_type]}</span>
        {context && <span className="flow-node-context" title={context}>{context}</span>}
        {node.properties?.archived === true && <span className="flow-node-flag">{copy.search.archivedTag}</span>}
      </div>
      {grain === 'column' && columns.length > 0 && <div className="lineage-columns">
        {shown.map((column) => <a key={column.node_id} href={nodeHref(column.node_id)}
          className={`lineage-column${column.node_id === rootId ? ' selected' : ''}`}
          title={displayName(column)} onClick={(event) => event.stopPropagation()}>{displayName(column)}</a>)}
        {columns.length > 1 && <button type="button" className="lineage-expand nodrag" onClick={(event) => {
          event.stopPropagation(); onToggle()
        }}>{expanded ? copy.diagram.collapseColumns : copy.lineage.moreColumns(columns.length - shown.length)}</button>}
      </div>}
      {bindings.length > 0 && <details className="lineage-bindings nodrag" onClick={(event) => event.stopPropagation()}>
        <summary>{copy.lineage.bindings(bindings.length)}</summary>
        <div className="lineage-binding-list">{bindings.map((field) => <a key={field.node_id} href={nodeHref(field.node_id)}>{displayName(field)}</a>)}</div>
      </details>}
      {(up > 0 || down > 0) && <div className="lineage-frontier">
        {(['up', 'down'] as const).map((direction) => {
          const count = direction === 'up' ? up : down
          return count > 0 && <button type="button" className="lineage-expand nodrag" key={direction}
            onClick={(event) => { event.stopPropagation(); onReveal(direction) }}>
            {copy.lineage.moreGroups(direction, count)}
          </button>
        })}
      </div>}
      <Handle type="source" position={Position.Right} className="flow-handle" />
    </div>
  )
}

const nodeTypes = { lineage: LineageNode }
const edgeTypes = { lineage: LineageRoutedEdge }

export function LineagePage({ nodeId, grain, embedded = false }: { nodeId: string; grain: Grain; embedded?: boolean }) {
  const Page = embedded ? 'section' : 'main'
  const { index, consequence } = useStitch()
  const root = index.nodesById.get(nodeId)
  const impact = consequence.of(nodeId)
  const rootContext = root ? nodeContext(index, root) : null
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null)
  const [showBindings, setShowBindings] = useState(false)
  const trace = useMemo(() => lineageFor(index, nodeId), [index, nodeId])
  const view = useMemo(() => presentLineage(index, trace, nodeId, showBindings), [index, trace, nodeId, showBindings])
  const [revealed, setRevealed] = useState<Set<string> | null>(null)
  const visible = useMemo(() => revealed ?? initialLineageBlocks(view), [revealed, view])
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const flow = useRef<ReactFlowInstance<LineageFlowNode, Edge> | null>(null)
  const canvas = useRef<HTMLDivElement | null>(null)
  const [ready, setReady] = useState(false)
  const autoFrame = useRef(true)
  const [frame, setFrame] = useState({ kind: 'focus', revision: 0 })
  const fitted = useRef<unknown>(null)

  const requestFrame = (kind: 'focus' | 'overview') => {
    autoFrame.current = true
    setRevealed(kind === 'overview' ? new Set(view.blocks.map((block) => block.node.node_id)) : null)
    setFrame((previous) => ({ kind, revision: previous.revision + 1 }))
  }
  const reveal = useCallback((id: string, direction: 'up' | 'down') => {
    autoFrame.current = false
    setRevealed((previous) => {
      const next = new Set(previous ?? initialLineageBlocks(view))
      for (const neighbour of neighbours(view, id, direction).filter((item) => !next.has(item)).slice(0, 4)) next.add(neighbour)
      return next
    })
  }, [view])
  const toggle = useCallback((id: string) => {
    autoFrame.current = false
    setExpanded((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const { nodes, edges } = useMemo(() => {
    const blocks = view.blocks.filter((block) => visible.has(block.node.node_id))
    const links = view.edges.filter((edge) => visible.has(edge.from) && visible.has(edge.to))
    const graphNodes = blocks.map((block) => block.node)
    const maxRows = grain === 'column' ? Math.max(0, ...blocks.map((block) => expanded.has(block.node.node_id) ? block.columns.length : Math.min(1, block.columns.length))) : 0
    const positions = layoutLineage({ nodes: graphNodes, edges: links, layers: layerEntities(graphNodes.map((node) => node.node_id), links) },
      { columnWidth: 380, rowHeight: 168 + maxRows * 25 })
    // The selected block's origin stays fixed when a frontier is expanded.
    const anchor = positions.get(view.focusId) ?? { x: 0, y: 0 }
    const nodes: LineageFlowNode[] = blocks.map((block) => {
      const id = block.node.node_id
      const position = positions.get(id) ?? { x: 0, y: 0 }
      return {
        id, type: 'lineage', position: { x: position.x - anchor.x, y: position.y - anchor.y },
        data: {
          block, context: nodeContext(index, block.node), isRoot: id === view.focusId, rootId: nodeId, grain,
          expanded: expanded.has(id), onToggle: () => toggle(id),
          up: neighbours(view, id, 'up').filter((item) => !visible.has(item)).length,
          down: neighbours(view, id, 'down').filter((item) => !visible.has(item)).length,
          onReveal: (direction) => reveal(id, direction),
        },
      }
    })
    const fans = edgeFans(links.map((edge) => ({ source: edge.from, target: edge.to })))
    const edges: Edge[] = links.map((edge, i) => ({
      id: `${edge.from}->${edge.to}`, source: edge.from, target: edge.to, type: 'lineage',
      className: `lineage-edge conf-${edge.confidence}`,
      style: { strokeDasharray: edge.confidence === 'exact' || edge.confidence === 'validated' ? undefined : '6 4' },
      data: {
        tooltip: `${edge.confidence} · ${CONFIDENCE_HELP[edge.confidence]}\n${edge.evidence.map((item) => `${item.from} → ${item.to} · ${item.confidence}\n${Object.entries(item.evidence ?? {}).map(([key, value]) => `${key}: ${String(value)}`).join('\n')}`).join('\n')}`,
        fan: fans.get(i),
      },
    }))
    return { nodes, edges }
  }, [view, visible, grain, expanded, nodeId, index, reveal, toggle])

  const [flowNodes, setFlowNodes] = useState<LineageFlowNode[]>(nodes)
  useEffect(() => setFlowNodes(nodes), [nodes])
  const onNodesChange = useCallback((changes: NodeChange<LineageFlowNode>[]) =>
    setFlowNodes((current) => applyNodeChanges(changes, current)), [])
  const framing = useMemo(() => ({ nodes, frame }), [nodes, frame])
  useEffect(() => {
    if (!ready || !autoFrame.current || fitted.current === framing || !flow.current || !canvas.current || !canvasSettled(flowNodes, nodes)) return
    fitted.current = framing
    const instance = flow.current
    if (frame.kind === 'overview') { void instance.fitView({ padding: 0.12, duration: 240 }); return }
    const bounds = instance.getNodesBounds(flowNodes)
    const focus = instance.getNodesBounds(flowNodes.filter((node) => node.id === view.focusId))
    void instance.setViewport(readableViewport(bounds, focus, canvas.current.clientWidth, canvas.current.clientHeight), { duration: 240 })
  }, [ready, framing, frame.kind, flowNodes, nodes, view.focusId])

  if (!root) return <Page className="graph-page"><p className="muted panel">{copy.lineage.unknownNode(nodeId)}</p></Page>
  return (
    <Page className="graph-page">
      <div className="graph-toolbar">
        {!embedded && <span className="graph-toolbar-title"><NodeBadge node={root} />
          <span className="graph-toolbar-name">{root.node_type === 'column' && rootContext ? `${rootContext}.` : ''}{displayName(root)}</span>
        </span>}
        <span className="graph-toolbar-impact">{copy.lineage.downstreamCounts(impact.cards, impact.dashboards)}</span>
        <div className="grain-toggle" role="group" aria-label={copy.lineage.grainLabel}>
          {(['column', 'table'] as const).map((option) => <a key={option} className={`grain-option${grain === option ? ' active' : ''}`}
            href={lineageHref(nodeId, option)} aria-current={grain === option ? 'page' : undefined}
            title={option === 'column' ? copy.lineage.grainColumn : copy.lineage.grainTable}>{option}</a>)}
        </div>
        <div className="diagram-actions">
          <button type="button" className="ghost-button" onClick={() => requestFrame('focus')}>{copy.diagram.focus}</button>
          <button type="button" className="ghost-button" onClick={() => requestFrame('overview')}>{copy.diagram.overview}</button>
        </div>
      </div>
      <div className="diagram-context">
        <span>{copy.lineage.showing(nodes.length, view.blocks.length)}</span>
        <button type="button" className="ghost-button" aria-pressed={showBindings} onClick={() => {
          setShowBindings((value) => !value); setRevealed(null); autoFrame.current = true
          setFrame((previous) => ({ kind: 'focus', revision: previous.revision + 1 }))
        }}>{showBindings ? copy.lineage.hideBindings : copy.lineage.showBindings}</button>
        {trace.truncated && <span className="muted">{copy.lineage.truncated}</span>}
        <span className="muted">{copy.lineage.exploreHint}</span>
      </div>
      <div className="graph-canvas" ref={canvas}>
        <ReactFlow nodes={flowNodes} onNodesChange={onNodesChange} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
          minZoom={0.05} nodesConnectable={false} nodesDraggable={false} elementsSelectable
          onInit={(instance) => { flow.current = instance; setReady(true) }} onMoveStart={(event) => { if (event) autoFrame.current = false }}
          nodeClickDistance={CLICK_SLOP_PX} proOptions={{ hideAttribution: true }}
          onNodeClick={(_, node) => { if (node.id !== view.focusId) navigate(lineageHref(node.id, grain)) }}
          onEdgeMouseEnter={(event, edge) => {
            const text = edge.data?.tooltip
            if (typeof text === 'string') setTip({ x: event.clientX, y: event.clientY, text })
          }} onEdgeMouseLeave={() => setTip(null)}>
          <Background gap={24} /><Controls showInteractive={false} showFitView={false} />{nodes.length > 8 && <MiniMap pannable zoomable />}
        </ReactFlow>
        {tip && <div className="edge-tooltip" style={{ left: tip.x + 12, top: tip.y + 12 }}>{tip.text}</div>}
      </div>
      <GraphLegend />
    </Page>
  )
}
