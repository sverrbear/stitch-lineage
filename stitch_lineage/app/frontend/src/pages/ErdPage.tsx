// Read-only ERD (spec §9): models as table nodes with expandable column
// lists, relates_to edges between column handles (solid; ✓ badge when
// validated). A scope selector (schema / dbt tag) keeps it from ever
// rendering a 200-node hairball — one scope at a time.

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
import { useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from 'react'
import { SystemBadge } from '../components/badges'
import { GraphLegend } from '../components/bits'
import { useStitch } from '../data'
import { CLICK_SLOP_PX, isClickNotDrag, type Point } from '../lib/canvas'
import {
  erdClickHref,
  erdForScope,
  initialScope,
  listScopes,
  visibleColumns,
  type ErdModel,
  type ErdScope,
} from '../lib/erd'
import { NODE_TYPE_NAME, displayName } from '../lib/present'
import { erdHref, navigate } from '../router'

const COLLAPSED_LIMIT = 8
const OPEN_HINT = 'Open details · ⌘/Ctrl-click for lineage'

type ErdFlowNode = Node<
  {
    model: ErdModel
    expanded: boolean
    onToggle: (id: string) => void
  },
  'erdModel'
>

function ErdModelNode({ data }: NodeProps<ErdFlowNode>) {
  const { model, expanded, onToggle } = data
  const pressedAt = useRef<Point | null>(null)

  const onPointerDown = (event: PointerEvent) => {
    pressedAt.current = { x: event.clientX, y: event.clientY }
  }

  const open = (nodeId: string) => (event: MouseEvent) => {
    const from = pressedAt.current
    pressedAt.current = null
    if (!isClickNotDrag(from, { x: event.clientX, y: event.clientY })) return
    event.stopPropagation()
    navigate(erdClickHref(nodeId, event))
  }

  const openOnEnter = (nodeId: string) => (event: KeyboardEvent) => {
    if (event.key !== 'Enter') return
    event.stopPropagation()
    navigate(erdClickHref(nodeId, event))
  }

  const visible = visibleColumns(model, expanded, COLLAPSED_LIMIT)
  const hidden = model.columns.length - visible.length
  const collapsible = model.columns.some((column) => !column.isKey)
  // The dbt model name is the header; the physical table is a subtitle at most, and
  // only when it says something the schema line does not already say.
  const table = model.node.table

  return (
    <div className={`erd-node${model.external ? ' external' : ''}`} onPointerDown={onPointerDown}>
      <div
        className="erd-node-title"
        role="link"
        tabIndex={0}
        title={OPEN_HINT}
        onClick={open(model.node.node_id)}
        onKeyDown={openOnEnter(model.node.node_id)}
      >
        <div className="erd-node-header">
          <SystemBadge nodeType={model.node.node_type} />
          <span className="erd-node-name">{displayName(model.node)}</span>
          <span className="erd-node-kind">{NODE_TYPE_NAME[model.node.node_type]}</span>
          {model.external && <span className="erd-external-tag">other scope</span>}
        </div>
        <div className="erd-node-schema">
          {model.node.schema ?? ''}
          {table && table !== displayName(model.node) ? (
            <span className="erd-node-relation" title="physical table in the warehouse">
              {table}
            </span>
          ) : null}
        </div>
      </div>
      <ul className="erd-columns">
        {visible.map((column) => (
          <li
            key={column.nodeId}
            className={`erd-column${column.isKey ? ' key' : ''}${column.phantom ? ' phantom' : ''}`}
            role="link"
            tabIndex={0}
            title={column.phantom ? `${OPEN_HINT} · declared by a relationship, not in the catalog` : OPEN_HINT}
            onClick={open(column.nodeId)}
            onKeyDown={openOnEnter(column.nodeId)}
          >
            <Handle type="target" id={column.key} position={Position.Left} className="erd-handle" />
            <span className="erd-column-name">{column.name}</span>
            <span className="erd-column-type">{column.dataType ?? ''}</span>
            <Handle type="source" id={column.key} position={Position.Right} className="erd-handle" />
          </li>
        ))}
      </ul>
      {collapsible && (hidden > 0 || expanded) && (
        <button
          type="button"
          className="erd-expand nodrag"
          onClick={(e) => {
            e.stopPropagation()
            onToggle(model.node.node_id)
          }}
        >
          {expanded ? 'collapse' : `+ ${hidden} more columns`}
        </button>
      )}
    </div>
  )
}

const nodeTypes = { erdModel: ErdModelNode }

function scopeKey(scope: ErdScope): string {
  return `${scope.kind}:${scope.value}`
}

function scopeLabel(scope: ErdScope): string {
  const models = `${scope.modelCount} model${scope.modelCount === 1 ? '' : 's'}`
  const rels = scope.relationshipCount > 0 ? `, ${scope.relationshipCount} rels` : ''
  return `${scope.value} (${models}${rels})`
}

/**
 * The picker leads with what a human models: analytics schemas, then dbt tags.
 * Package/warehouse-internal schemas (elementary, artifacts, …) stay reachable
 * but sit last, so browsing the ERD never starts in somebody else's plumbing.
 */
const SCOPE_GROUPS: Array<{ label: string; match: (scope: ErdScope) => boolean }> = [
  { label: 'Schemas', match: (s) => s.kind === 'schema' && !s.internal },
  { label: 'dbt tags', match: (s) => s.kind === 'tag' },
  { label: 'Tooling & internal schemas', match: (s) => s.kind === 'schema' && s.internal },
]

export function ErdPage({
  scopeKind,
  scopeValue,
}: {
  scopeKind?: 'schema' | 'tag'
  scopeValue?: string
}) {
  const { index, meta } = useStitch()
  const scopes = useMemo(() => listScopes(index), [index])
  const routed =
    (scopeKind && scopeValue && scopes.find((s) => s.kind === scopeKind && s.value === scopeValue)) ||
    null
  // No scope in the URL: open the configured one, else the auto-picked one.
  const landing = useMemo(() => initialScope(scopes, meta.erd_default_scope), [scopes, meta])
  const active = routed ?? landing.scope
  const unknownConfigured = routed ? null : landing.unknownConfigured
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const erd = useMemo(() => (active ? erdForScope(index, active) : null), [index, active])

  const { nodes, edges } = useMemo(() => {
    if (!erd) return { nodes: [] as ErdFlowNode[], edges: [] as Edge[] }
    const onToggle = (id: string) =>
      setExpanded((prev) => {
        const next = new Set(prev)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      })

    const columns = Math.max(1, Math.ceil(Math.sqrt(erd.models.length)))
    const nodes: ErdFlowNode[] = erd.models.map((model, i) => ({
      id: model.node.node_id,
      type: 'erdModel',
      position: { x: (i % columns) * 340, y: Math.floor(i / columns) * 320 },
      data: { model, expanded: expanded.has(model.node.node_id), onToggle },
    }))

    const edges: Edge[] = erd.relationships.map((rel, i) => ({
      id: `rel-${i}`,
      source: rel.fromModelId,
      sourceHandle: rel.fromColumn,
      target: rel.toModelId,
      targetHandle: rel.toColumn,
      type: 'smoothstep',
      className: 'erd-edge',
      // "user_id → user_id ✓" beats a bare tick nobody can decode
      label: `${rel.fromColumn} → ${rel.toColumn}${rel.validated ? ' ✓' : ''}`,
      labelShowBg: true,
    }))
    return { nodes, edges }
  }, [erd, expanded])

  if (!active || !erd) {
    return (
      <main className="graph-page">
        <p className="muted panel">No models in the graph — nothing to draw.</p>
      </main>
    )
  }

  return (
    <main className="graph-page">
      <div className="graph-toolbar">
        <label className="scope-label" htmlFor="erd-scope">
          Scope
        </label>
        <select
          id="erd-scope"
          className="scope-select"
          value={scopeKey(active)}
          onChange={(e) => {
            const next = scopes.find((s) => scopeKey(s) === e.target.value)
            if (next) navigate(erdHref(next.kind, next.value))
          }}
        >
          {SCOPE_GROUPS.map(({ label, match }) => {
            const group = scopes.filter(match)
            if (group.length === 0) return null
            return (
              <optgroup key={label} label={label}>
                {group.map((s) => (
                  <option key={scopeKey(s)} value={scopeKey(s)}>
                    {scopeLabel(s)}
                  </option>
                ))}
              </optgroup>
            )
          })}
        </select>
        <span className="muted">
          {erd.models.length} models · {erd.relationships.length} relationships
        </span>
        {active.internal && (
          <span className="muted">tooling schema — not part of the analytics model</span>
        )}
        {unknownConfigured && (
          <span className="scope-warning" title="serve.erd_default_scope in stitch.yml">
            configured scope <code>{unknownConfigured}</code> is not in this graph
          </span>
        )}
        <span className="muted graph-toolbar-hint">click a table or column for details</span>
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
          nodeClickDistance={CLICK_SLOP_PX}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={24} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
      </div>
      <GraphLegend erd />
    </main>
  )
}
