// Read-only ERD (spec §9): models as table nodes with expandable column
// lists, relates_to edges between column handles (solid; ✓ badge when
// validated). A scope selector (schema / dbt tag) keeps it from ever
// rendering a 200-node hairball — one scope at a time.

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
  type ReactFlowInstance,
} from '@xyflow/react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
} from 'react'
import { ApplyDialog } from '../components/ApplyDialog'
import { SystemBadge } from '../components/badges'
import { GraphLegend } from '../components/bits'
import { ErdMarkers, ErdRoutedEdge } from '../components/ErdEdge'
import { StageRelationshipModal, type StageTarget } from '../components/StageRelationshipModal'
import { StagedWorkspace } from '../components/StagedWorkspace'
import { useStitch } from '../data'
import { CLICK_SLOP_PX, isClickNotDrag, type Point } from '../lib/canvas'
import {
  autoExpandedModels,
  cardinalityMarkers,
  erdClickHref,
  erdColumnNodeId,
  erdForScope,
  initialScope,
  listScopes,
  resolveStaged,
  scopeModelIds,
  suggestionsInScope,
  visibleColumns,
  type ErdModel,
  type ErdScope,
} from '../lib/erd'
import { erdNodeHeight, layoutErd } from '../lib/erdLayout'
import {
  NODE_TYPE_NAME,
  displayModelName,
  displayName,
  displayTableName,
  fullName,
} from '../lib/present'
import {
  editRelationship,
  listStaged,
  listStagedDescriptions,
  probeApply,
  probeStaging,
  stageRelationship,
  unstageDescription,
  unstageRelationship,
  type Cardinality,
  type StagedDescription,
  type StagedRelationship,
} from '../lib/staging'
import {
  countBySource,
  dismissSuggestion,
  filterBySource,
  listSuggestions,
  probeSuggestions,
  rankSuggestions,
  scoreLabel,
  SOURCE_HELP,
  SOURCE_LABEL,
  type SourceFilter,
  type Suggestion,
} from '../lib/suggestions'
import { workspaceView } from '../lib/workspace'
import { erdHref, navigate, nodeHref } from '../router'

const COLLAPSED_LIMIT = 8
const OPEN_HINT = 'Open details · ⌘/Ctrl-click for lineage'
const RELATE_HINT = 'Drag onto another column to declare a relationship'

type ErdFlowNode = Node<
  {
    model: ErdModel
    expanded: boolean
    onToggle: (id: string) => void
    /** Handles are inert (and invisible) unless this build can stage. */
    connectable: boolean
    /** `model::column` keys the hovered relationship joins — lit up in the card. */
    lit: ReadonlySet<string>
  },
  'erdModel'
>

function ErdModelNode({ data }: NodeProps<ErdFlowNode>) {
  const { model, expanded, onToggle, connectable, lit } = data
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
  // only when it says something the name does not already say. The configured
  // table_prefix comes off first — `sis_fct_x` is this machine's dev alias, so with
  // it gone the line is usually redundant and goes away entirely (#80).
  const name = displayName(model.node)
  const table = displayTableName(model.node.table)

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
        {/* The header is the NAME's line: `mart_content_feed_impressions_daily` used to
            shove the type tag out of the card, so the tag moved down to the detail line
            and the name gets the full width, ellipsised with a tooltip (#80). */}
        <div className="erd-node-header">
          <SystemBadge nodeType={model.node.node_type} />
          <span className="erd-node-name" title={name}>
            {name}
          </span>
          {model.external && <span className="erd-external-tag">other scope</span>}
        </div>
        <div className="erd-node-schema">
          <span className="erd-node-kind">{NODE_TYPE_NAME[model.node.node_type]}</span>
          <span className="erd-node-scope">{model.node.schema ?? ''}</span>
          {table && table !== name ? (
            <span
              className="erd-node-relation"
              title={`${model.node.table} — physical table in the warehouse`}
            >
              {table}
            </span>
          ) : null}
        </div>
      </div>
      <ul className="erd-columns">
        {visible.map((column) => (
          <li
            key={column.nodeId}
            className={`erd-column${column.isKey ? ' key' : ''}${column.phantom ? ' phantom' : ''}${
              lit.has(column.nodeId) ? ' lit' : ''
            }`}
            role="link"
            tabIndex={0}
            title={column.phantom ? `${OPEN_HINT} · declared by a relationship, not in the catalog` : OPEN_HINT}
            onClick={open(column.nodeId)}
            onKeyDown={openOnEnter(column.nodeId)}
          >
            {/* Grab strips run the full height of the row and sit INSIDE the card.
                A 9px dot centred on the card's edge was half-clipped by the card's
                `overflow: hidden` — not hit-testable at its own centre, 1-2px wide
                at ERD zoom — which is why drawing a relationship read as broken (#64). */}
            <Handle
              type="target"
              id={column.key}
              position={Position.Left}
              className={`erd-handle${connectable ? ' drawable' : ''}`}
              isConnectable={connectable}
              title={connectable ? RELATE_HINT : undefined}
            />
            <span className="erd-column-name" title={column.name}>
              {column.name}
            </span>
            <span className="erd-column-type">{column.dataType ?? ''}</span>
            <Handle
              type="source"
              id={column.key}
              position={Position.Right}
              className={`erd-handle${connectable ? ' drawable' : ''}`}
              isConnectable={connectable}
              title={connectable ? RELATE_HINT : undefined}
            />
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
// every relationship is routed around the cards rather than drawn through them (#79)
const edgeTypes = { erdRouted: ErdRoutedEdge }
const EDGE_TYPE = 'erdRouted'

/** `markerStart`/`markerEnd` for an edge, from its cardinality (see ErdMarkers). */
function cardinalityMarkerProps(cardinality?: string | null) {
  const { start, end } = cardinalityMarkers(cardinality)
  return { markerStart: start, markerEnd: end }
}

function scopeKey(scope: ErdScope): string {
  return `${scope.kind}:${scope.value}`
}

/** Height-cache key: a table measures one thing collapsed and another expanded. */
function sizeKey(id: string, expanded: boolean): string {
  return `${expanded ? 'open' : 'shut'}:${id}`
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
  const { index, meta, reload } = useStitch()
  const scopes = useMemo(() => listScopes(index), [index])
  const routed =
    (scopeKind && scopeValue && scopes.find((s) => s.kind === scopeKind && s.value === scopeValue)) ||
    null
  // No scope in the URL: open the configured one, else the auto-picked one.
  const landing = useMemo(() => initialScope(scopes, meta.erd_default_scope), [scopes, meta])
  const active = routed ?? landing.scope
  const unknownConfigured = routed ? null : landing.unknownConfigured
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  // Staging exists only under `stitch serve`. A static export, or an older serve
  // without the endpoint, must read as a plain read-only ERD -- not as a broken one.
  const [canStage, setCanStage] = useState(false)
  const [staged, setStaged] = useState<StagedRelationship[]>([])
  const [descriptions, setDescriptions] = useState<StagedDescription[]>([])
  // Applying is a separate capability from staging: `stitch serve` without a
  // stitch.yml can stage but has nothing to write into (#72).
  const [canApply, setCanApply] = useState(false)
  const [applyOpen, setApplyOpen] = useState(false)
  const [draft, setDraft] = useState<StageTarget | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [canSuggest, setCanSuggest] = useState(false)
  const [panelOpen, setPanelOpen] = useState(true)
  const [stagedOpen, setStagedOpen] = useState(false)
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('implicit_join')

  // Canvas state the auto-layout does not own: tables the reader dragged, and
  // whether a relationship landed since — which is what the reset control offers
  // to fix (see `resetView`).
  const flow = useRef<ReactFlowInstance<ErdFlowNode, Edge> | null>(null)
  const [manual, setManual] = useState<Record<string, { x: number; y: number }>>({})
  const [layoutStale, setLayoutStale] = useState(false)
  const movedCount = Object.keys(manual).length

  // Rendered node heights, keyed by table AND expansion state, so the layout
  // spaces tables by what they actually measure rather than by an estimate of
  // the CSS box (#62). `measuredHeights` is a ref because it is layout input,
  // not render output; the counter is what re-runs the layout when it changes.
  /**
   * The relationship under the pointer. A permanent `user_id → user_id` label
   * floating mid-canvas was noise on a real scope (#65); the pair belongs on the
   * edge you are actually looking at, and on the two rows it joins.
   */
  const [hovered, setHovered] = useState<{
    id: string
    label: string
    columns: string[]
  } | null>(null)
  const measuredHeights = useRef<Record<string, number>>({})
  const measuredWidths = useRef<Record<string, number>>({})
  const [measuredVersion, setMeasuredVersion] = useState(0)

  const fitSoon = () => {
    // let React Flow measure the new nodes before it fits them
    window.setTimeout(() => void flow.current?.fitView({ padding: 0.12, duration: 320 }), 80)
  }

  /** Reset: back to the auto-layout, fitted, with every dragged table let go. */
  const resetView = () => {
    setManual({})
    setLayoutStale(false)
    fitSoon()
  }

  /**
   * A relationship was declared or dropped, so the arrangement changed underneath
   * the reader. If the layout still owns the canvas, show the new one; if they
   * have been moving tables by hand, say so on the reset control instead of
   * yanking their work away.
   */
  const relayout = () => {
    if (movedCount > 0) setLayoutStale(true)
    else fitSoon()
  }

  const refreshStaged = useCallback(async () => {
    try {
      setStaged(await listStaged())
    } catch {
      setStaged([])
    }
  }, [])

  const refreshDescriptions = useCallback(async () => {
    try {
      setDescriptions(await listStagedDescriptions())
    } catch {
      // a serve older than #70 has no such endpoint: no description edits exist
      setDescriptions([])
    }
  }, [])

  const refreshSuggestions = useCallback(async () => {
    try {
      setSuggestions(rankSuggestions(await listSuggestions()))
    } catch {
      setSuggestions([])
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    void probeStaging(meta.staging_enabled).then(async (enabled) => {
      if (cancelled || !enabled) return
      setCanStage(true)
      await refreshStaged()
      // description edits and applying are later additions than staging: probe each
      // separately, so an older serve shows no such control rather than a broken one
      await refreshDescriptions()
      if (!cancelled && (await probeApply(meta.apply_enabled))) setCanApply(true)
      // the suggestion engine is a later addition than staging: probe separately,
      // so a serve without it shows no panel rather than an empty one
      const suggests = await probeSuggestions(meta.staging_enabled)
      if (cancelled || !suggests) return
      setCanSuggest(true)
      await refreshSuggestions()
    })
    return () => {
      cancelled = true
    }
  }, [
    meta.staging_enabled,
    meta.apply_enabled,
    refreshStaged,
    refreshDescriptions,
    refreshSuggestions,
  ])

  const resolved = useMemo(() => resolveStaged(index, staged), [index, staged])
  const workspace = useMemo(() => workspaceView(staged, descriptions), [staged, descriptions])
  const litColumns = useMemo<ReadonlySet<string>>(
    () => new Set(hovered?.columns ?? []),
    [hovered],
  )
  // Suggestions arrive graph-wide (hundreds on a real project). Scope them to the
  // ERD first: both endpoints inside it, which is exactly what the canvas can draw.
  const inScopeIds = useMemo(
    () => (active ? scopeModelIds(index, active) : new Set<string>()),
    [index, active],
  )
  const resolvedSuggestions = useMemo(
    () =>
      resolveStaged(
        index,
        suggestions.map((entry) => ({ ...entry, cardinality: entry.cardinality_guess })),
      ),
    [index, suggestions],
  )
  const scopedSuggestions = useMemo(
    () => suggestionsInScope(suggestions, resolvedSuggestions.drawable, inScopeIds),
    [suggestions, resolvedSuggestions, inScopeIds],
  )
  const sourceCounts = useMemo(() => countBySource(scopedSuggestions), [scopedSuggestions])
  // the canvas draws what the panel lists: one filter, both surfaces
  const shownSuggestions = useMemo(
    () => filterBySource(scopedSuggestions, sourceFilter),
    [scopedSuggestions, sourceFilter],
  )
  const drawableSuggestions = useMemo(() => {
    const shown = new Set(shownSuggestions.map((entry) => entry.id))
    return resolvedSuggestions.drawable.filter((rel) => shown.has(rel.id))
  }, [resolvedSuggestions, shownSuggestions])
  const erd = useMemo(
    () => (active ? erdForScope(index, active, resolved.drawable, drawableSuggestions) : null),
    [index, active, resolved, drawableSuggestions],
  )

  /** node id -> dbt model name, which is what the staging API speaks. */
  const modelNameOf = useCallback(
    (nodeId: string | null | undefined): string | null => {
      const node = nodeId ? index.nodesById.get(nodeId) : null
      // the staging API needs the real dbt name, prefix and all
      return node ? fullName(node) : null
    },
    [index],
  )

  /**
   * One path for staging and for editing (#71): the modal is the same, and which
   * verb runs depends on whether the draft carries the id of an existing entry.
   */
  const confirmStage = async (cardinality: Cardinality): Promise<string | null> => {
    if (!draft) return null
    const request = {
      from_model: draft.fromModel,
      from_column: draft.fromColumn,
      to_model: draft.toModel,
      to_column: draft.toColumn,
      cardinality,
    }
    try {
      if (draft.id) {
        const result = await editRelationship(draft.id, request)
        await refreshStaged()
        if (canSuggest) await refreshSuggestions()
        setDraft(null)
        setNotice(
          result.moved
            ? 'edited — the declaration moved to a different column pair'
            : 'edited the staged declaration',
        )
        relayout()
        return null
      }
      const result = await stageRelationship(request)
      await refreshStaged()
      if (canSuggest) await refreshSuggestions()
      setDraft(null)
      setNotice(
        result.created
          ? `staged ${result.relationship.from_model}.${result.relationship.from_column} → ${result.relationship.to_model}.${result.relationship.to_column}`
          : 'that column pair was already staged',
      )
      // what just happened is in the panel: show it rather than announce it nowhere
      setStagedOpen(true)
      relayout()
      return null
    } catch (error) {
      return error instanceof Error ? error.message : String(error)
    }
  }

  const removeStaged = async (id: string) => {
    await unstageRelationship(id)
    await refreshStaged()
    if (canSuggest) await refreshSuggestions()
    setNotice(null)
    relayout()
  }

  /** Editing reopens the staging modal, prefilled — the same decision, revisited. */
  const editStaged = (entry: StagedRelationship) => {
    setNotice(null)
    setDraft({
      id: entry.id,
      fromModel: entry.from_model,
      fromColumn: entry.from_column,
      toModel: entry.to_model,
      toColumn: entry.to_column,
      cardinality: entry.cardinality,
    })
  }

  const removeDescription = async (id: string) => {
    await unstageDescription(id)
    await refreshDescriptions()
    setNotice(null)
  }

  /**
   * A description is edited where it is read — on the table's own page — so the
   * workspace sends you there rather than growing a second editor (SPEC §12.2).
   */
  const openDescription = (entry: StagedDescription) => {
    const model = index.nodes.find(
      (node) =>
        (node.node_type === 'model' || node.node_type === 'source') &&
        fullName(node).toLowerCase() === entry.entity.toLowerCase(),
    )
    if (!model) {
      setNotice(`${entry.entity} is not in this graph — open it after the next build`)
      return
    }
    navigate(nodeHref(entry.column ? erdColumnNodeId(model.node_id, entry.column) : model.node_id))
  }

  /** After a real apply the repo AND the graph changed: re-read everything. */
  const afterApply = async () => {
    await reload()
    await refreshStaged()
    await refreshDescriptions()
    if (canSuggest) await refreshSuggestions()
    relayout()
  }

  /** Accepting is not a shortcut: it opens the same modal a drag opens, prefilled. */
  const acceptSuggestion = (suggestion: Suggestion) => {
    setNotice(null)
    setDraft({
      fromModel: suggestion.from_model,
      fromColumn: suggestion.from_column,
      toModel: suggestion.to_model,
      toColumn: suggestion.to_column,
      cardinality: suggestion.cardinality_guess,
      provenance: `${SOURCE_LABEL[suggestion.source]} — ${scoreLabel(suggestion)}`,
    })
  }

  const dismiss = async (id: string) => {
    await dismissSuggestion(id)
    await refreshSuggestions()
    setNotice('suggestion dismissed — it will not come back')
  }

  /**
   * Auto-layout for the current scope: declared and staged relationships place the
   * tables (see lib/erdLayout). Suggestions are deliberately NOT layout input —
   * they are proposals, and the canvas must not rearrange itself every time the
   * source filter changes.
   *
   * Expanding a table changes its height, which changes this map, which reflows
   * the scope — a grown table pushes its neighbours down instead of covering them,
   * and collapsing gives the space back (#62).
   */
  const positions = useMemo(() => {
    if (!erd) return new Map<string, { x: number; y: number }>()
    return layoutErd(
      erd.models.map((model) => {
        const open = expanded.has(model.node.node_id)
        const estimate = erdNodeHeight(
          visibleColumns(model, open, COLLAPSED_LIMIT).length,
          model.columns.some((column) => !column.isKey),
        )
        return {
          id: model.node.node_id,
          height: measuredHeights.current[sizeKey(model.node.node_id, open)] ?? estimate,
          width: measuredWidths.current[model.node.node_id],
        }
      }),
      // Suggestions the canvas actually DRAWS place tables too: an edge the
      // reader can see is an edge that must be short, and leaving proposals out
      // is what left long lines whipping across the scope (#65). The list is
      // already capped, so the arrangement stays stable.
      [...erd.relationships, ...erd.staged, ...erd.suggested].map((rel) => ({
        from: rel.fromModelId,
        to: rel.toModelId,
      })),
    )
    // measuredVersion: the ref it reads is mutated in place, so the counter is
    // what tells this memo a table now measures something else
  }, [erd, expanded, measuredVersion])

  const baseNodes = useMemo(() => {
    if (!erd) return [] as ErdFlowNode[]
    const onToggle = (id: string) =>
      setExpanded((prev) => {
        const next = new Set(prev)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      })

    return erd.models.map((model, i) => ({
      id: model.node.node_id,
      type: 'erdModel' as const,
      // a dragged table keeps where the reader put it until they reset the view
      position: manual[model.node.node_id] ??
        positions.get(model.node.node_id) ?? { x: i * 360, y: 0 },
      data: {
        model,
        expanded: expanded.has(model.node.node_id),
        onToggle,
        connectable: canStage,
        lit: litColumns,
      },
    }))
  }, [erd, expanded, canStage, positions, manual, litColumns])

  const edges = useMemo(() => {
    if (!erd) return [] as Edge[]
    const edges: Edge[] = erd.relationships.map((rel, i) => ({
      id: `rel-${i}`,
      source: rel.fromModelId,
      sourceHandle: rel.fromColumn,
      target: rel.toModelId,
      targetHandle: rel.toColumn,
      type: EDGE_TYPE,
      className: `erd-edge${hovered?.id === `rel-${i}` ? ' hovered' : ''}`,
      // the pair is on the edge you point at, never floating over the canvas
      label: hovered?.id === `rel-${i}` ? hovered.label : undefined,
      labelShowBg: true,
      data: {
        pair: `${rel.fromColumn} → ${rel.toColumn}${rel.validated ? ' ✓' : ''}`,
        columns: [
          erdColumnNodeId(rel.fromModelId, rel.fromColumn),
          erdColumnNodeId(rel.toModelId, rel.toColumn),
        ],
      },
      // the graph never records a cardinality for a declared FK, so it reads as
      // the many-to-one it almost always is
      ...cardinalityMarkerProps(),
    }))

    // Suggestions are proposals: thinner, fainter and further from solid than a
    // staged edge, so "stitch thinks" never reads as "somebody decided".
    for (const rel of erd.suggested) {
      edges.push({
        id: `suggested-${rel.id}`,
        source: rel.fromModelId,
        sourceHandle: rel.fromColumn,
        target: rel.toModelId,
        targetHandle: rel.toColumn,
        type: EDGE_TYPE,
        className: `erd-edge suggested${hovered?.id === `suggested-${rel.id}` ? ' hovered' : ''}`,
        style: { strokeDasharray: '2 5', strokeWidth: 1.6 },
        label: hovered?.id === `suggested-${rel.id}` ? hovered.label : undefined,
        labelShowBg: true,
        data: {
          pair: `${rel.fromColumn} → ${rel.toColumn} · suggested`,
          columns: [
            erdColumnNodeId(rel.fromModelId, rel.fromColumn),
            erdColumnNodeId(rel.toModelId, rel.toColumn),
          ],
        },
        ...cardinalityMarkerProps(rel.cardinality),
      })
    }

    // Staged edges are visibly provisional: nothing is in the repo until `stitch apply`.
    for (const rel of erd.staged) {
      edges.push({
        id: `staged-${rel.id}`,
        source: rel.fromModelId,
        sourceHandle: rel.fromColumn,
        target: rel.toModelId,
        targetHandle: rel.toColumn,
        type: EDGE_TYPE,
        className: `erd-edge staged${hovered?.id === `staged-${rel.id}` ? ' hovered' : ''}`,
        style: { strokeDasharray: '5 4' },
        label: hovered?.id === `staged-${rel.id}` ? hovered.label : undefined,
        labelShowBg: true,
        data: {
          pair: `${rel.fromColumn} → ${rel.toColumn} · staged`,
          columns: [
            erdColumnNodeId(rel.fromModelId, rel.fromColumn),
            erdColumnNodeId(rel.toModelId, rel.toColumn),
          ],
        },
        ...cardinalityMarkerProps(rel.cardinality),
      })
    }
    return edges
  }, [erd, hovered])

  // React Flow owns node positions while a drag is in flight; the layout owns them
  // otherwise. `manual` is the reader's overrides, and resetting the view drops it.
  const [nodes, setNodes] = useState<ErdFlowNode[]>(baseNodes)
  // Rebuilt nodes go to React Flow WITHOUT a carried-over `measured`: that field
  // is React Flow's own bookkeeping, and handing it back on a fresh object makes
  // it skip re-measuring — which silently drops every edge on that node, because
  // handle bounds are measured with it. `measuredHeights` is our copy for layout.
  useEffect(() => setNodes(baseNodes), [baseNodes])
  const onNodesChange = useCallback(
    (changes: NodeChange<ErdFlowNode>[]) =>
      setNodes((current) => applyNodeChanges(changes, current)),
    [],
  )

  // What a table actually measures, per expansion state — the layout's input.
  useEffect(() => {
    let changed = false
    for (const node of nodes) {
      const height = node.measured?.height
      const width = node.measured?.width
      if (width && Math.abs((measuredWidths.current[node.id] ?? 0) - width) > 1) {
        measuredWidths.current[node.id] = width
        changed = true
      }
      if (!height) continue
      const key = sizeKey(node.id, node.data.expanded)
      if (Math.abs((measuredHeights.current[key] ?? 0) - height) > 1) {
        measuredHeights.current[key] = height
        changed = true
      }
    }
    if (changed) setMeasuredVersion((version) => version + 1)
  }, [nodes])

  // A new scope is a new drawing: nobody's drags carry over to it, and a small
  // one opens with every table's columns showing (#62).
  const activeKey = active ? scopeKey(active) : ''
  useEffect(() => {
    setManual({})
    setLayoutStale(false)
    setExpanded(autoExpandedModels(erd?.models ?? []))
    fitSoon()
  }, [activeKey])

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
          {erd.staged.length > 0 ? ` · ${erd.staged.length} staged` : ''}
          {erd.suggested.length > 0
            ? ` · ${erd.suggested.length} suggested drawn${erd.suggestedHidden > 0 ? ` of ${erd.suggested.length + erd.suggestedHidden}` : ''}`
            : ''}
        </span>
        {active.internal && (
          <span className="muted">tooling schema — not part of the analytics model</span>
        )}
        {unknownConfigured && (
          <span className="scope-warning" title="serve.erd_default_scope in stitch.yml">
            configured scope <code>{unknownConfigured}</code> is not in this graph
          </span>
        )}
        <button
          type="button"
          className={`ghost-button erd-reset${layoutStale ? ' stale' : ''}`}
          onClick={resetView}
          title={
            layoutStale
              ? 'the relationships changed — reset to lay the scope out again'
              : 'lay the scope out again and fit it to the window (drops dragged positions)'
          }
        >
          Reset view
          {movedCount > 0 ? ` (${movedCount} moved)` : ''}
        </button>
        {canStage && !stagedOpen && workspace.total > 0 && (
          <button
            type="button"
            className="ghost-button"
            onClick={() => setStagedOpen(true)}
            title="everything waiting for `stitch apply` — relationships and description edits"
          >
            Staged changes ({workspace.total})
          </button>
        )}
        {canSuggest && !panelOpen && (
          <button type="button" className="ghost-button" onClick={() => setPanelOpen(true)}>
            Suggested ({scopedSuggestions.length})
          </button>
        )}
        {canStage ? (
          <span className="muted graph-toolbar-hint">
            drag from a column's edge onto another column to declare a relationship · click for
            details
          </span>
        ) : (
          <span className="muted graph-toolbar-hint">click a table or column for details</span>
        )}
      </div>
      <div
        className={`graph-canvas${canSuggest && panelOpen ? ' with-panel' : ''}${
          canStage && stagedOpen ? ' with-staged' : ''
        }`}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          minZoom={0.05}
          nodesConnectable={canStage}
          // generous snap on the drop side: releasing near a column's strip counts
          connectionRadius={40}
          nodesDraggable
          onNodesChange={onNodesChange}
          onEdgeMouseEnter={(_event, edge) => {
            const data = edge.data as { pair?: string; columns?: string[] } | undefined
            setHovered({ id: edge.id, label: data?.pair ?? '', columns: data?.columns ?? [] })
          }}
          onEdgeMouseLeave={() => setHovered(null)}
          onInit={(instance) => {
            flow.current = instance
          }}
          onNodeDragStop={(_event, node) =>
            setManual((current) => ({ ...current, [node.id]: node.position }))
          }
          nodeClickDistance={CLICK_SLOP_PX}
          proOptions={{ hideAttribution: true }}
          onConnect={(connection) => {
            const fromModel = modelNameOf(connection.source)
            const toModel = modelNameOf(connection.target)
            if (!fromModel || !toModel || !connection.sourceHandle || !connection.targetHandle) return
            setNotice(null)
            setDraft({
              fromModel,
              fromColumn: connection.sourceHandle,
              toModel,
              toColumn: connection.targetHandle,
            })
          }}
        >
          <ErdMarkers />
          <Background gap={24} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
        {canStage && stagedOpen && (
          <StagedWorkspace
            view={workspace}
            unresolvedIds={resolved.unresolvedIds}
            canApply={canApply}
            busy={applyOpen}
            notice={notice}
            onClose={() => setStagedOpen(false)}
            onEditRelationship={editStaged}
            onDiscardRelationship={(id) => void removeStaged(id)}
            onEditDescription={openDescription}
            onDiscardDescription={(id) => void removeDescription(id)}
            onApply={() => setApplyOpen(true)}
          />
        )}
        {canSuggest && panelOpen && (
          <aside className="suggest-panel" aria-label="Suggested relationships">
            <div className="suggest-panel-head">
              <span className="suggest-panel-title">Suggested ({shownSuggestions.length})</span>
              <span
                className="muted suggest-scope"
                title={`candidates with both models in ${active.value}; the rest join models in other scopes`}
              >
                {scopedSuggestions.length} in this scope · {suggestions.length} total
              </span>
              {erd.suggestedHidden > 0 && (
                <span className="muted suggest-cap" title="the panel lists them all; the canvas draws the strongest">
                  {erd.suggestedHidden} not drawn
                </span>
              )}
              <button
                type="button"
                className="ghost-button"
                onClick={() => setPanelOpen(false)}
                aria-label="Hide suggestions"
              >
                ✕
              </button>
            </div>
            <div className="suggest-filter" role="group" aria-label="Suggestion source">
              {(['implicit_join', 'naming', 'all'] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`suggest-filter-option${sourceFilter === value ? ' active' : ''}`}
                  onClick={() => setSourceFilter(value)}
                  title={value === 'all' ? 'every candidate' : SOURCE_HELP[value]}
                >
                  {value === 'all' ? 'all' : SOURCE_LABEL[value]} ({sourceCounts[value]})
                </button>
              ))}
            </div>
            {shownSuggestions.length === 0 ? (
              <p className="muted suggest-empty">
                {suggestions.length === 0
                  ? 'Nothing to suggest — every join stitch can see is already declared, staged or dismissed.'
                  : scopedSuggestions.length === 0
                    ? `None inside ${active.value}: all ${suggestions.length} candidates join a model in another scope.`
                    : 'None from this source. The counts above show what the others hold.'}
              </p>
            ) : (
              <ul className="suggest-list">
                {shownSuggestions.map((entry) => {
                  // everything listed is in scope and resolvable now; only the draw
                  // cap can still keep one off the canvas
                  const notDrawn = !erd.suggested.some((rel) => rel.id === entry.id)
                  return (
                    <li key={entry.id} className="suggest-entry">
                      <code className="suggest-pair">
                        {displayModelName(entry.from_model)}.{entry.from_column} →{' '}
                        {displayModelName(entry.to_model)}.{entry.to_column}
                      </code>
                      <div className="suggest-meta">
                        <span className="suggest-source" title={SOURCE_HELP[entry.source]}>
                          {SOURCE_LABEL[entry.source]}
                        </span>
                        <span className="muted">{scoreLabel(entry)}</span>
                        <span className="muted">{entry.cardinality_guess}</span>
                        {notDrawn && (
                          <span className="muted" title="listed here, but not drawn on this canvas">
                            not drawn
                          </span>
                        )}
                      </div>
                      <div className="suggest-actions">
                        <button
                          type="button"
                          className="button"
                          onClick={() => acceptSuggestion(entry)}
                        >
                          Accept…
                        </button>
                        <button
                          type="button"
                          className="ghost-button"
                          onClick={() => void dismiss(entry.id)}
                          title="dismissed permanently — it will not be suggested again"
                        >
                          Dismiss
                        </button>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </aside>
        )}
      </div>
      <GraphLegend
        erd
        staged={canStage && erd.staged.length > 0}
        suggested={canSuggest && erd.suggested.length > 0}
      />
      {draft && (
        <StageRelationshipModal
          target={draft}
          onCancel={() => setDraft(null)}
          onConfirm={confirmStage}
        />
      )}
      {applyOpen && (
        <ApplyDialog
          onClose={() => setApplyOpen(false)}
          onApplied={async (outcome) => {
            await afterApply()
            setNotice(
              outcome.refused.length > 0
                ? `applied — ${outcome.refused.length} file${outcome.refused.length === 1 ? '' : 's'} refused, still staged`
                : `applied ${outcome.applied} change${outcome.applied === 1 ? '' : 's'}`,
            )
          }}
        />
      )}
    </main>
  )
}
