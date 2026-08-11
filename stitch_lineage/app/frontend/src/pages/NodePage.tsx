// Routed detail panels (spec §9): column / card / dashboard / model / source /
// field. Every panel links to the lineage view and carries system badges.
// Naming follows lib/present: dbt entities read as dbt names, Metabase entities
// as their Metabase display name, and the physical warehouse relation is only
// ever a secondary fact row.

import { Suspense, lazy, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { SystemBadge } from '../components/badges'
import { ChipList, ConfidenceTag, Fact, NodeChip, Section } from '../components/bits'
import { DescriptionEditor } from '../components/DescriptionEditor'
import { DashboardGroups, LayerGroups } from '../components/fanout'
import { useStitch } from '../data'
import {
  biDetail,
  columnDetail,
  dataTypeLabel,
  modelDetail,
  type ChainGap,
  type RelationshipRef,
} from '../lib/details'
import { resolveStaged, type ErdStagedRelationship } from '../lib/erd'
import { dashboardCount, dashboardGroups, layerGroups } from '../lib/fanout'
import { metabaseLink } from '../lib/graph'
import type { GraphIndex, Reach } from '../lib/graph'
import { modelStar } from '../lib/modelStar'
import {
  listStaged,
  listStagedDescriptions,
  fetchWriteability,
  probeStaging,
  refusalFor,
  type StagedDescription,
  type Writeability,
  type StagedRelationship,
} from '../lib/staging'
import {
  NODE_TYPE_NAME,
  displayName,
  fullName,
  hasHiddenPrefix,
  isPlaceholder,
  metabaseRelation,
  nodeContext,
  ownerName,
  warehouseColumn,
  warehouseRelation,
} from '../lib/present'
import { lineageHref } from '../router'
import type { GraphNode } from '../types'

// React Flow is the heavy chunk and a detail panel must stay light: the mini star
// loads with the section, not with the page (same reasoning as App's canvases).
const ModelStar = lazy(() =>
  import('../components/ModelStar').then((m) => ({ default: m.ModelStar })),
)

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

/**
 * The staged declarations this build can see, resolved onto the graph. Empty on a
 * static export or a serve too old for the endpoint — a read-only build must read
 * as read-only, not as broken (same probe the ERD uses).
 */
interface Staging {
  canStage: boolean
  /** Staged relationships, resolved onto the graph (what the mini star draws). */
  relationships: ErdStagedRelationship[]
  descriptions: StagedDescription[]
  /** Models whose schema file apply could never write, and why (#132). */
  writeability: Writeability
  refresh: () => Promise<void>
}

/**
 * What is staged, for this page. Empty and read-only on a static export or a serve
 * too old for the endpoints — a read-only build must read as read-only, not as
 * broken (the same probe the ERD uses).
 */
function useStaging(index: GraphIndex): Staging {
  const { meta } = useStitch()
  const [canStage, setCanStage] = useState(false)
  const [staged, setStaged] = useState<StagedRelationship[]>([])
  const [descriptions, setDescriptions] = useState<StagedDescription[]>([])
  const [writeability, setWriteability] = useState<Writeability>(new Map())

  const refresh = useCallback(async () => {
    try {
      setStaged(await listStaged())
    } catch {
      setStaged([])
    }
    try {
      setDescriptions(await listStagedDescriptions())
    } catch {
      // a serve older than #70 has no description store
      setDescriptions([])
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    void probeStaging(meta.staging_enabled).then(async (enabled) => {
      if (cancelled || !enabled) return
      setCanStage(true)
      const refusals = await fetchWriteability()
      if (!cancelled) setWriteability(refusals)
      await refresh()
    })
    return () => {
      cancelled = true
    }
  }, [meta.staging_enabled, refresh])

  const relationships = useMemo(() => resolveStaged(index, staged).drawable, [index, staged])
  return { canStage, relationships, descriptions, writeability, refresh }
}

/** The staged edit sitting over this entity's description, if there is one. */
function stagedFor(
  descriptions: readonly StagedDescription[],
  entity: string,
  column: string | null,
): StagedDescription | null {
  const wanted = column?.toLowerCase() ?? null
  return (
    descriptions.find(
      (entry) =>
        entry.entity.toLowerCase() === entity.toLowerCase() &&
        (entry.column?.toLowerCase() ?? null) === wanted,
    ) ?? null
  )
}

function reachChips(reaches: Reach[]) {
  return <ChipList nodes={reaches.map((r) => ({ node: r.node, confidence: r.confidence }))} />
}

function PanelHeader({
  node,
  subtitle,
  description,
}: {
  node: GraphNode
  subtitle?: string | null
  /** Replaces the read-only description line — the editor, where one is offered (#70). */
  description?: ReactNode
}) {
  const { meta, index } = useStitch()
  const link = metabaseLink(meta.metabase_url, node)
  const archived = node.properties?.archived === true
  // undefined means "use the shared rule"; an explicit null suppresses the line.
  const shown = subtitle === undefined ? nodeContext(index, node) : subtitle
  // A column is named with its model, always: `dim_users.user_id`, never
  // `user_id` (principle 02). The title is selectable text, not a shape.
  const owner = node.node_type === 'column' ? ownerName(index, node) : null
  return (
    <div className="panel-header">
      <div className="panel-title">
        <SystemBadge nodeType={node.node_type} size={20} />
        <h2 title={node.node_id}>
          {owner && <span className="panel-name-owner">{owner}.</span>}
          {displayName(node)}
        </h2>
        <span className="panel-type">{NODE_TYPE_NAME[node.node_type]}</span>
        {archived && <span className="archived-tag">archived</span>}
      </div>
      {shown && <p className="panel-subtitle">{shown}</p>}
      {description ?? (node.description && <p className="panel-description">{node.description}</p>)}
      {isPlaceholder(node) && (
        <p className="muted panel-placeholder">
          Only referenced by an edge — this build never resolved a definition for it.
        </p>
      )}
      <div className="panel-actions">
        <a className="button" href={lineageHref(node.node_id)}>
          View lineage →
        </a>
        {link && (
          <a className="button" href={link} target="_blank" rel="noreferrer">
            Open in Metabase ↗
          </a>
        )}
      </div>
    </div>
  )
}

function ColumnPanel({ nodeId }: { nodeId: string }) {
  const { index } = useStitch()
  const staging = useStaging(index)
  const detail = columnDetail(index, nodeId)
  if (!detail) return <NotFound nodeId={nodeId} />
  const { node } = detail
  const dataType = dataTypeLabel(node)
  // A column's description is written on its MODEL's schema entry, so the staging
  // API is addressed by the model's dbt name plus the column name.
  const entity = detail.model ? fullName(detail.model) : null
  const headline =
    detail.cards.length > 0
      ? `Consumed by ${plural(detail.cards.length, 'card')} on ${plural(detail.dashboards.length, 'dashboard')}.`
      : 'Not consumed by any Metabase card in this graph.'

  return (
    <article className="panel">
      <PanelHeader
        node={node}
        subtitle={detail.model ? `column of ${displayName(detail.model)}` : nodeContext(index, node)}
        description={
          entity ? (
            <DescriptionEditor
              entity={entity}
              column={node.column ?? displayName(node)}
              applied={node.description}
              staged={stagedFor(staging.descriptions, entity, node.column ?? displayName(node))}
              canStage={staging.canStage}
              refusal={refusalFor(staging.writeability, entity)}
              onChanged={staging.refresh}
            />
          ) : undefined
        }
      />
      <dl className="fact-list">
        <Fact label={detail.model?.node_type === 'source' ? 'source' : 'model'}>
          {detail.model ? <NodeChip node={detail.model} /> : '—'}
        </Fact>
        <Fact label="data type">
          {/* "unknown" alone reads as a bug; the graph knows why, so it says so (#122).
              A type that DID resolve says which source answered for it (#149): the
              catalog, the Metabase sync and a sqlglot guess are not equal evidence. */}
          <span title={dataType.hint ?? undefined}>{dataType.text}</span>
          {dataType.source ? (
            <span className="type-source" title={dataType.source.hint}>
              {dataType.source.label}
            </span>
          ) : null}
        </Fact>
        {/* the display name may hide a routing prefix, so the real dbt name
            stays one line away (#69) */}
        <Fact label="dbt name">
          {hasHiddenPrefix(node) ? <code>{fullName(node)}</code> : null}
        </Fact>
        <Fact label="in the warehouse">
          {warehouseRelation(node) && (
            <code>
              {warehouseRelation(node)}.{warehouseColumn(node) ?? displayName(node)}
            </code>
          )}
        </Fact>
      </dl>

      <Section title={`Upstream — ${plural(detail.upstreamColumns.length, 'column')}, ${plural(detail.upstreamSources.length, 'source')}`}>
        {detail.upstreamSources.length > 0 && (
          <>
            <h4 className="subhead">sources</h4>
            {reachChips(detail.upstreamSources)}
          </>
        )}
        <h4 className="subhead">columns</h4>
        {reachChips(detail.upstreamColumns)}
      </Section>

      <Section title={`Downstream — ${headline}`}>
        {detail.truncated && <p className="muted">(fan-out truncated for display)</p>}
        <h4 className="subhead">{plural(detail.downstreamModels.length, 'model')}</h4>
        <ChipList nodes={detail.downstreamModels.map((node) => ({ node }))} />
        <h4 className="subhead">{plural(detail.fields.length, 'Metabase field')}</h4>
        {reachChips(detail.fields)}
        <h4 className="subhead">{plural(detail.cards.length, 'card')}</h4>
        {reachChips(detail.cards)}
        <h4 className="subhead">{plural(detail.dashboards.length, 'dashboard')}</h4>
        {reachChips(detail.dashboards)}
      </Section>

      {detail.relationships.length > 0 && (
        <Section title="Declared relationships">
          <RelationshipList relationships={detail.relationships} />
        </Section>
      )}
    </article>
  )
}

/** Metabase-side facts. A field has none of a card's, so each gets its own set. */
function FieldFacts({ node }: { node: GraphNode }) {
  const semantic = node.properties?.semantic_type
  const visibility = node.properties?.visibility
  return (
    <dl className="fact-list">
      <Fact label="Metabase table">{node.table ? <code>{node.table}</code> : null}</Fact>
      <Fact label="in Metabase">{metabaseRelation(node)}</Fact>
      <Fact label="data type">{node.data_type ?? null}</Fact>
      <Fact label="semantic type">{typeof semantic === 'string' ? semantic : null}</Fact>
      <Fact label="visibility">{typeof visibility === 'string' ? visibility : null}</Fact>
    </dl>
  )
}

function CardFacts({ node }: { node: GraphNode }) {
  const creator = node.properties?.creator ?? node.properties?.creator_name
  const collection = node.properties?.collection_name ?? node.properties?.collection
  const display = node.properties?.display
  return (
    <dl className="fact-list">
      <Fact label="collection">{typeof collection === 'string' ? collection : null}</Fact>
      <Fact label="creator">{typeof creator === 'string' ? creator : null}</Fact>
      <Fact label="visualization">{typeof display === 'string' ? display : null}</Fact>
      <Fact label="archived">{node.properties?.archived === true ? 'yes' : 'no'}</Fact>
    </dl>
  )
}

/** The two ways a `binds_to` hop goes missing, and the command that lists them. */
function UnboundWhy() {
  return (
    <>
      Either the Metabase table is not bound to a dbt model in this build, or the column is absent
      from the dbt artifacts this graph was built from. <code>stitch doctor --unbound</code> lists
      the models with no bound Metabase table.
    </>
  )
}

/**
 * What to say when the reverse view is empty. A bare "none" reads as a broken
 * app; naming the missing hop and the command that lists it makes the gap
 * diagnosable (#25).
 */
function ChainGapNote({ gap, node, fieldCount }: { gap: ChainGap; node: GraphNode; fieldCount: number }) {
  const type = NODE_TYPE_NAME[node.node_type]
  return (
    <p className="chain-gap">
      {gap === 'fields-unbound' && (
        <>
          No dbt column binds to the {plural(fieldCount, 'Metabase field')} this {type} queries, so
          the chain stops at Metabase. <UnboundWhy />
        </>
      )}
      {gap === 'field-unbound' && (
        <>
          No dbt column binds to this field. <UnboundWhy />
        </>
      )}
      {gap === 'native-unresolved' && (
        <>
          This card is native SQL, and native cards are not resolved into column lineage in this
          build — the chain is unknown, not empty. Coverage counts them separately.
        </>
      )}
      {gap === 'query-unresolved' && (
        <>
          No Metabase field reference resolved out of this card&rsquo;s query, so there is no chain
          to walk. <code>stitch doctor --unresolved-cards</code> lists the refs and why each one
          failed.
        </>
      )}
      {gap === 'dashboard-unresolved' && (
        <>
          None of the cards on this dashboard resolved to a Metabase field, so there is no chain to
          walk down into dbt.
        </>
      )}
    </p>
  )
}

function BiPanel({ nodeId }: { nodeId: string }) {
  const { index } = useStitch()
  const detail = biDetail(index, nodeId)
  if (!detail) return <NotFound nodeId={nodeId} />
  const { node } = detail
  const isDashboard = node.node_type === 'mb_dashboard'
  const isField = node.node_type === 'mb_field'
  const dependsTitle = detail.gap
    ? 'Depends on — no dbt column resolved'
    : `Depends on ${plural(detail.dependsOnColumns.length, 'dbt column')} across ${plural(detail.dependsOnModels.length, 'model')}`

  return (
    <article className="panel">
      <PanelHeader node={node} />
      {isField ? <FieldFacts node={node} /> : <CardFacts node={node} />}

      {isDashboard ? (
        <Section title={`Cards on this dashboard — ${detail.cards.length}`}>{reachChips(detail.cards)}</Section>
      ) : (
        detail.dashboards.length > 0 && (
          <Section title={`Appears on ${plural(detail.dashboards.length, 'dashboard')}`}>
            {reachChips(detail.dashboards)}
          </Section>
        )
      )}

      {isField && detail.cards.length > 0 && (
        <Section title={`Consumed by ${plural(detail.cards.length, 'card')}`}>{reachChips(detail.cards)}</Section>
      )}

      <Section title={dependsTitle}>
        {detail.gap ? (
          <>
            <ChainGapNote gap={detail.gap} node={node} fieldCount={detail.fields.length} />
            {detail.fields.length > 0 && (
              <>
                {/* the half of the chain stitch did resolve: without it a card
                    whose tables are unbound looks like one that never parsed */}
                <h4 className="subhead">{plural(detail.fields.length, 'Metabase field')} it queries</h4>
                {reachChips(detail.fields)}
              </>
            )}
          </>
        ) : (
          <>
            <p className="muted">
              The reverse view: every dbt column this {NODE_TYPE_NAME[node.node_type]} ultimately
              depends on, named the dbt way with the model it comes from. Non-exact chains carry
              their weakest hop — hover it for what that means.
            </p>
            <h4 className="subhead">models</h4>
            <ChipList nodes={detail.dependsOnModels.map((node) => ({ node }))} />
            <h4 className="subhead">columns</h4>
            {reachChips(detail.dependsOnColumns)}
            {detail.unboundFields.length > 0 && (
              <>
                <h4 className="subhead">
                  {plural(detail.unboundFields.length, 'Metabase field')} with no dbt column
                </h4>
                <p className="chain-gap">
                  The chain stops at Metabase for these, so whatever they contribute is missing from
                  the columns above. <UnboundWhy />
                </p>
                {reachChips(detail.unboundFields)}
              </>
            )}
          </>
        )}
      </Section>
    </article>
  )
}

function ModelPanel({ nodeId }: { nodeId: string }) {
  const { index } = useStitch()
  // Staged declarations belong on the canvas too: a relationship drawn a minute ago
  // is a relationship, and this page would otherwise deny it exists (#81).
  const staging = useStaging(index)
  const detail = modelDetail(index, nodeId)
  const star = useMemo(
    () => modelStar(index, nodeId, staging.relationships),
    [index, nodeId, staging.relationships],
  )
  const upstreamGroups = useMemo(() => layerGroups(detail?.upstream ?? [], 'up'), [detail])
  const downstreamGroups = useMemo(() => layerGroups(detail?.downstream ?? [], 'down'), [detail])
  const biGroups = useMemo(() => dashboardGroups(index, detail?.cards ?? []), [index, detail])
  if (!detail) return <NotFound nodeId={nodeId} />
  const { node } = detail
  const materialization = node.properties?.materialization
  const path = node.properties?.path
  const tags = Array.isArray(node.properties?.tags) ? (node.properties.tags as unknown[]).map(String) : []

  const entity = fullName(node)
  return (
    <article className="panel">
      <PanelHeader
        node={node}
        description={
          <DescriptionEditor
            entity={entity}
            column={null}
            applied={node.description}
            staged={stagedFor(staging.descriptions, entity, null)}
            canStage={staging.canStage}
            refusal={refusalFor(staging.writeability, entity)}
            onChanged={staging.refresh}
          />
        }
      />
      <dl className="fact-list">
        <Fact label={node.node_type === 'source' ? 'dbt source' : 'schema'}>
          {nodeContext(index, node)}
        </Fact>
        <Fact label="materialization">
          {typeof materialization === 'string' ? materialization : null}
        </Fact>
        {/* the display name may hide a routing prefix, so the real dbt name
            stays one line away (#69) */}
        <Fact label="dbt name">
          {hasHiddenPrefix(node) ? <code>{fullName(node)}</code> : null}
        </Fact>
        <Fact label="in the warehouse">
          {warehouseRelation(node) ? <code>{warehouseRelation(node)}</code> : null}
        </Fact>
        <Fact label="defined in">{typeof path === 'string' ? <code>{path}</code> : null}</Fact>
      </dl>
      {tags.length > 0 && (
        <div className="tag-row">
          {tags.map((tag) => (
            <span key={tag} className="tag">
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Relationships get the canvas, dependencies get the lists below (#81/#82). */}
      <Section title={`Relationships — ${star?.joinCount ?? 0}`}>
        <Suspense fallback={<div className="star-canvas" />}>
          <ModelStar star={star} />
        </Suspense>
        {detail.relationships.length > 0 && (
          <RelationshipList relationships={detail.relationships} />
        )}
      </Section>

      <Section
        title={`Dependencies — ${plural(detail.upstream.length, 'model')} upstream, ${plural(detail.downstream.length, 'model')} downstream`}
      >
        <h4 className="subhead">upstream</h4>
        <LayerGroups
          groups={upstreamGroups}
          empty={
            node.node_type === 'source'
              ? 'nothing upstream — this is where the data enters'
              : 'nothing upstream in this graph'
          }
        />
        <h4 className="subhead">downstream</h4>
        <LayerGroups groups={downstreamGroups} empty="nothing downstream — this is a leaf" />
      </Section>

      <Section
        title={`BI usage — ${plural(detail.cards.length, 'card')} on ${plural(dashboardCount(biGroups), 'dashboard')}`}
      >
        <DashboardGroups
          groups={biGroups}
          empty="no Metabase card reads this table in this graph"
        />
      </Section>

      <Section title={`Columns — ${detail.columns.length}`}>
        <table className="columns-table">
          <thead>
            <tr>
              <th>column</th>
              <th>type</th>
              <th>description</th>
            </tr>
          </thead>
          <tbody>
            {detail.columns.map((column) => (
              <tr key={column.node_id}>
                <td>
                  <NodeChip node={column} context={null} />
                </td>
                <td>
                  <code>{column.data_type ?? ''}</code>
                </td>
                <td className="muted">{column.description ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </article>
  )
}

/**
 * Declared relationships read as `model.column → model.column`, both sides in
 * dbt names, with the direction spelled out rather than implied by an arrow.
 */
function RelationshipList({ relationships }: { relationships: RelationshipRef[] }) {
  const { index } = useStitch()
  return (
    <ul className="rel-list">
      {relationships.map((rel, i) => {
        const ownId = rel.direction === 'outgoing' ? rel.edge.from : rel.edge.to
        const own = index.nodesById.get(ownId)
        const ownLabel = own
          ? `${nodeContext(index, own) ?? ''}.${displayName(own)}`.replace(/^\./, '')
          : ownId
        return (
          <li key={i}>
            <span className="rel-own">{ownLabel}</span>
            <span className="rel-arrow" title={rel.direction === 'outgoing' ? 'references' : 'referenced by'}>
              {rel.direction === 'outgoing' ? '→' : '←'}
            </span>
            {rel.other ? <NodeChip node={rel.other} /> : <code>{rel.edge.to}</code>}
            {rel.validated ? (
              <span className="validated-badge" title="validated by a dbt relationships test">
                ✓
              </span>
            ) : (
              <ConfidenceTag confidence={rel.edge.confidence} />
            )}
          </li>
        )
      })}
    </ul>
  )
}

function NotFound({ nodeId }: { nodeId: string }) {
  return (
    <article className="panel">
      <h2>Node not found</h2>
      <p className="muted">
        <code>{nodeId}</code> is not in the loaded graph. It may have been removed in a newer
        build.
      </p>
    </article>
  )
}

export function NodePage({ nodeId }: { nodeId: string }) {
  const { index } = useStitch()
  const node = index.nodesById.get(nodeId)
  if (!node) return <NotFound nodeId={nodeId} />
  switch (node.node_type) {
    case 'column':
      return <ColumnPanel nodeId={nodeId} />
    case 'mb_card':
    case 'mb_dashboard':
    case 'mb_field':
      return <BiPanel nodeId={nodeId} />
    default:
      return <ModelPanel nodeId={nodeId} />
  }
}
