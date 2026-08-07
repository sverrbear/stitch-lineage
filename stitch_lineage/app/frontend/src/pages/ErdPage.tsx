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
import { useMemo, useState } from 'react'
import { SystemBadge } from '../components/badges'
import { GraphLegend } from '../components/bits'
import { useStitch } from '../data'
import { defaultScope, erdForScope, listScopes, type ErdModel, type ErdScope } from '../lib/erd'
import { erdHref, navigate, nodeHref } from '../router'

const COLLAPSED_LIMIT = 8

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
  const key = [...model.keyColumns]
  const keySet = model.keyColumns
  const keyColumns = model.columns.filter((c) => keySet.has(c.name.toLowerCase()) || keySet.has(c.name))
  const rest = model.columns.filter((c) => !keyColumns.includes(c))
  const visibleRest = expanded ? rest : rest.slice(0, Math.max(0, COLLAPSED_LIMIT - keyColumns.length))
  const hidden = rest.length - visibleRest.length
  // Relationship columns missing from the catalog still need handles.
  const phantomKeys = key.filter((k) => !model.columns.some((c) => c.name === k || c.name.toLowerCase() === k))

  return (
    <div className={`erd-node${model.external ? ' external' : ''}`}>
      <div className="erd-node-header" onClick={() => navigate(nodeHref(model.node.node_id))}>
        <SystemBadge nodeType={model.node.node_type} />
        <span className="erd-node-name">{model.node.name}</span>
        {model.external && <span className="erd-external-tag">other scope</span>}
      </div>
      <div className="erd-node-schema">{model.node.schema ?? ''}</div>
      <ul className="erd-columns">
        {[...keyColumns, ...visibleRest].map((column) => {
          const isKey = keyColumns.includes(column)
          return (
            <li key={column.node_id} className={`erd-column${isKey ? ' key' : ''}`}>
              <Handle
                type="target"
                id={column.name}
                position={Position.Left}
                className="erd-handle"
              />
              <span className="erd-column-name">{column.name}</span>
              <span className="erd-column-type">{column.data_type ?? ''}</span>
              <Handle
                type="source"
                id={column.name}
                position={Position.Right}
                className="erd-handle"
              />
            </li>
          )
        })}
        {phantomKeys.map((name) => (
          <li key={`phantom-${name}`} className="erd-column key">
            <Handle type="target" id={name} position={Position.Left} className="erd-handle" />
            <span className="erd-column-name">{name}</span>
            <Handle type="source" id={name} position={Position.Right} className="erd-handle" />
          </li>
        ))}
      </ul>
      {(hidden > 0 || expanded) && rest.length > 0 && (
        <button
          type="button"
          className="erd-expand"
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

export function ErdPage({
  scopeKind,
  scopeValue,
}: {
  scopeKind?: 'schema' | 'tag'
  scopeValue?: string
}) {
  const { index } = useStitch()
  const scopes = useMemo(() => listScopes(index), [index])
  const active =
    (scopeKind && scopeValue && scopes.find((s) => s.kind === scopeKind && s.value === scopeValue)) ||
    defaultScope(scopes)
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
      label: rel.validated ? '✓' : undefined,
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
          <optgroup label="Schemas">
            {scopes
              .filter((s) => s.kind === 'schema')
              .map((s) => (
                <option key={scopeKey(s)} value={scopeKey(s)}>
                  {s.value} ({s.modelCount} models{s.relationshipCount > 0 ? `, ${s.relationshipCount} rels` : ''})
                </option>
              ))}
          </optgroup>
          {scopes.some((s) => s.kind === 'tag') && (
            <optgroup label="dbt tags">
              {scopes
                .filter((s) => s.kind === 'tag')
                .map((s) => (
                  <option key={scopeKey(s)} value={scopeKey(s)}>
                    {s.value} ({s.modelCount} models{s.relationshipCount > 0 ? `, ${s.relationshipCount} rels` : ''})
                  </option>
                ))}
            </optgroup>
          )}
        </select>
        <span className="muted">
          {erd.models.length} models · {erd.relationships.length} relationships
        </span>
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
