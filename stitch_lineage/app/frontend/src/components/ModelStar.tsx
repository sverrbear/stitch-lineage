// The model page's mini star-schema (#81): this table in the middle, the tables it
// joins to around it, the joining columns on both cards, `1`/`*` on the ends.
//
// It is the ERD's own machinery at panel scale — the constellation layout (#76)
// with a single hub, and the obstacle-avoiding edge routing (#79) — so a
// relationship reads the same here as on the full canvas. What it is NOT is a
// second ERD: nothing is draggable, the wheel scrolls the page rather than zooming
// the canvas, and the cards list only the columns that take part.

import {
  ReactFlow,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from '@xyflow/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { layoutErd } from '../lib/erdLayout'
import {
  STAR_CARD_WIDTH,
  starCardHeight,
  type ModelStar as ModelStarData,
  type StarJoin,
  type StarNeighbour,
} from '../lib/modelStar'
import { displayName } from '../lib/present'
import { erdHref, navigate, nodeHref } from '../router'
import { ErdRoutedEdge } from './ErdEdge'

type StarFlowNode = Node<
  {
    label: string
    kind: string | null
    rows: string[]
    isHub: boolean
    href: string | null
    /** `model::column` keys the hovered relationship joins. */
    lit: ReadonlySet<string>
  },
  'starModel'
>

/** `model::column` — the key a row and its edge agree on. */
function rowKey(nodeId: string, column: string): string {
  return `${nodeId}::${column}`
}

function StarNode({ id, data }: NodeProps<StarFlowNode>) {
  const { label, kind, rows, isHub, href, lit } = data
  const open = () => {
    if (href) navigate(href)
  }
  return (
    <div
      className={`star-node${isHub ? ' hub' : ''}`}
      role={href ? 'link' : undefined}
      tabIndex={href ? 0 : undefined}
      title={href ? `${label} — open` : `${label} — you are here`}
      onClick={open}
      onKeyDown={(event) => {
        if (event.key === 'Enter') open()
      }}
    >
      <div className="star-node-head">
        <span className="star-node-name">{label}</span>
        {kind && <span className="star-node-kind">{kind}</span>}
      </div>
      <ul className="star-columns">
        {rows.map((column) => (
          <li
            key={column}
            className={`star-column${lit.has(rowKey(id, column)) ? ' lit' : ''}`}
          >
            <Handle
              type="target"
              id={column}
              position={Position.Left}
              className="star-handle"
              isConnectable={false}
            />
            <span className="star-column-name" title={column}>
              {column}
            </span>
            <Handle
              type="source"
              id={column}
              position={Position.Right}
              className="star-handle"
              isConnectable={false}
            />
          </li>
        ))}
      </ul>
    </div>
  )
}

const nodeTypes = { starModel: StarNode }
const edgeTypes = { erdRouted: ErdRoutedEdge }

/** How the join reads in words, on hover. */
function joinLabel(join: StarJoin, hub: string, other: string): string {
  const from = join.direction === 'outgoing' ? `${hub}.${join.ownColumn}` : `${other}.${join.otherColumn}`
  const to = join.direction === 'outgoing' ? `${other}.${join.otherColumn}` : `${hub}.${join.ownColumn}`
  const tail = join.staged ? ' · staged' : join.validated ? ' ✓' : ''
  return `${from} → ${to}${tail}`
}

export function ModelStar({ star }: { star: ModelStarData | null }) {
  const [hovered, setHovered] = useState<{ id: string; label: string; columns: string[] } | null>(
    null,
  )
  const instance = useRef<ReactFlowInstance<StarFlowNode, Edge> | null>(null)
  // `fitView` on mount runs before React Flow has measured the cards, which left a
  // two-table star at half scale in the top corner. Fit again once it has.
  const refit = () => {
    window.setTimeout(() => void instance.current?.fitView({ padding: 0.14, duration: 200 }), 90)
  }
  useEffect(refit, [star])
  const lit = useMemo<ReadonlySet<string>>(() => new Set(hovered?.columns ?? []), [hovered])

  const flow = useMemo(() => {
    if (!star || star.neighbours.length === 0) return null
    const hubId = star.hub.node_id
    const cards = [
      { id: hubId, rows: star.hubColumns, node: star.hub, isHub: true as const },
      ...star.neighbours.map((neighbour: StarNeighbour) => ({
        id: neighbour.node.node_id,
        rows: [...new Set(neighbour.joins.map((join) => join.otherColumn))],
        node: neighbour.node,
        isHub: false as const,
      })),
    ]
    const layoutEdges = star.neighbours.map((neighbour) => ({
      from: hubId,
      to: neighbour.node.node_id,
    }))
    const positions = layoutErd(
      cards.map((card) => ({
        id: card.id,
        width: STAR_CARD_WIDTH,
        height: starCardHeight(card.rows.length),
      })),
      layoutEdges,
      { cardWidth: STAR_CARD_WIDTH, gap: 44 },
    )

    const nodes: StarFlowNode[] = cards.map((card, i) => ({
      id: card.id,
      type: 'starModel' as const,
      position: positions.get(card.id) ?? { x: i * (STAR_CARD_WIDTH + 44), y: 0 },
      data: {
        label: displayName(card.node),
        kind: card.node.schema ?? null,
        rows: card.rows,
        isHub: card.isHub,
        href: card.isHub ? null : nodeHref(card.node.node_id),
        lit,
      },
    }))

    const edges: Edge[] = star.neighbours.flatMap((neighbour) =>
      neighbour.joins.map((join) => {
        const outgoing = join.direction === 'outgoing'
        const label = joinLabel(join, displayName(star.hub), displayName(neighbour.node))
        return {
          id: join.id,
          source: outgoing ? hubId : neighbour.node.node_id,
          sourceHandle: outgoing ? join.ownColumn : join.otherColumn,
          target: outgoing ? neighbour.node.node_id : hubId,
          targetHandle: outgoing ? join.otherColumn : join.ownColumn,
          type: 'erdRouted',
          className: `erd-edge${join.staged ? ' staged' : ''}${hovered?.id === join.id ? ' hovered' : ''}`,
          style: join.staged ? { strokeDasharray: '5 4' } : undefined,
          label: hovered?.id === join.id ? hovered.label : undefined,
          labelShowBg: true,
          data: {
            pair: label,
            columns: [
              rowKey(hubId, join.ownColumn),
              rowKey(neighbour.node.node_id, join.otherColumn),
            ],
          },
          // No 1/⋇ cardinality glyphs: at this size they are a row of ticks
          // nobody reads, and the join's own `from → to` label already says
          // which way it points. (#110 does the same for the ERD canvas.)
        }
      }),
    )
    return { nodes, edges }
  }, [star, lit, hovered])

  if (!star) return null
  if (!flow) {
    return (
      <p className="muted star-empty">
        No relationships on this table yet —{' '}
        <a href={erdHref(star.hub.schema ? 'schema' : undefined, star.hub.schema ?? undefined)}>
          draw one in the ERD
        </a>{' '}
        and it shows up here.
      </p>
    )
  }

  return (
    <>
      {/* room for the ring the constellation actually needs, and no more */}
      <div
        className="star-canvas"
        style={{ height: Math.min(460, 240 + star.neighbours.length * 22) }}
      >
        <ReactFlow
          nodes={flow.nodes}
          edges={flow.edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.14 }}
          minZoom={0.2}
          maxZoom={1.4}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          // an embedded canvas must not eat the page's scroll
          zoomOnScroll={false}
          zoomOnPinch={false}
          preventScrolling={false}
          panOnDrag
          proOptions={{ hideAttribution: true }}
          onEdgeMouseEnter={(_event, edge) => {
            const data = edge.data as { pair?: string; columns?: string[] } | undefined
            setHovered({ id: edge.id, label: data?.pair ?? '', columns: data?.columns ?? [] })
          }}
          onEdgeMouseLeave={() => setHovered(null)}
          onInit={(created) => {
            instance.current = created
            refit()
          }}
        />
      </div>
      <p className="muted star-note">
        {star.hiddenNeighbours > 0
          ? `${star.hiddenNeighbours} more related table${star.hiddenNeighbours === 1 ? '' : 's'} — `
          : 'Hover a line for the column pair · '}
        <a
          href={erdHref(
            star.hub.schema ? 'schema' : undefined,
            star.hub.schema ?? undefined,
          )}
        >
          see them all in the ERD →
        </a>
      </p>
    </>
  )
}
