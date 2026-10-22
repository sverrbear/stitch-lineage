// Routed detail panels (spec §9): column / card / dashboard / model / source /
// field. Every panel links to the lineage view and carries manager badges (#187).
// Naming follows lib/present: dbt entities read as dbt names, Metabase entities
// as their Metabase display name, and the physical warehouse relation is only
// ever a secondary fact row.

import { useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ObjectWorkspaceContext } from '../components/NodeIdentity'
import { NodeBadge } from '../components/badges'
import {
  ChipList,
  ConfidenceTag,
  Fact,
  ManagedByFact,
  NodeChip,
  Section,
} from '../components/bits'
import { DescriptionEditor } from '../components/DescriptionEditor'
import { BiImpact } from '../components/BiImpact'
import { LayerGroups } from '../components/fanout'
import { useStitch } from '../data'
import {
  biDetail,
  columnDetail,
  dataTypeLabel,
  modelDetail,
  type ChainGap,
  type RelationshipRef,
} from '../lib/details'
import { layerGroups } from '../lib/fanout'
import { metabaseLink } from '../lib/graph'
import type { GraphIndex, Reach } from '../lib/graph'
import {
  listStagedDescriptions,
  fetchWriteability,
  probeStaging,
  refusalFor,
  type StagedDescription,
  type Writeability,
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
import {
  columnDefinition,
  definedAsSummary,
  definedOriginSummary,
  definitionSql,
  traceReason,
} from '../lib/trace'
import { lineageHref } from '../router'
import type { GraphNode } from '../types'
import { copy, plural } from '../copy'

/**
 * The staged declarations this build can see, resolved onto the graph. Empty on a
 * static export or a serve too old for the endpoint — a read-only build must read
 * as read-only, not as broken.
 */
interface Staging {
  canStage: boolean
  descriptions: StagedDescription[]
  /** Models whose schema file apply could never write, and why (#132). */
  writeability: Writeability
  refresh: () => Promise<void>
}

/**
 * What is staged, for this page. Empty and read-only on a static export or a serve
 * too old for the endpoints — a read-only build must read as read-only, not as
 * broken.
 */
export function useStaging(index: GraphIndex): Staging {
  const { meta } = useStitch()
  const visible = useContext(ObjectWorkspaceContext)?.summaryActive ?? true
  const [canStage, setCanStage] = useState(false)
  const [descriptions, setDescriptions] = useState<StagedDescription[]>([])
  const [writeability, setWriteability] = useState<Writeability>(new Map())

  const refreshGeneration = useRef(0)
  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current
    const edits = await listStagedDescriptions().catch(() => [] as StagedDescription[])
    if (generation !== refreshGeneration.current) return
    setDescriptions(edits)
  }, [])

  useEffect(() => {
    if (!visible) return
    let cancelled = false
    void probeStaging(meta.staging_enabled).then(async (enabled) => {
      if (cancelled || !enabled) return
      setCanStage(true)
      const refusals = await fetchWriteability()
      if (cancelled) return
      setWriteability(refusals)
      await refresh()
    })
    return () => {
      cancelled = true
      // A hidden or replaced snapshot may not overwrite the next activation's fetch.
      refreshGeneration.current += 1
    }
  // Summary remains mounted across views so drafts survive. Its staged snapshot
  // must refresh after editing/applying staged changes, without resetting that editor.
  }, [meta.staging_enabled, refresh, visible, index])

  return { canStage, descriptions, writeability, refresh }
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

function reachChips(reaches: Reach[], partial = false) {
  if (partial && reaches.length === 0) return <p className="muted">{copy.node.noDisplayedEntries}</p>
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
  const embedded = useContext(ObjectWorkspaceContext)
  if (embedded) {
    return <>{description ?? (node.description && <p className="panel-description">{node.description}</p>)}</>
  }
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
        <NodeBadge node={node} size={20} />
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
        <p className="muted panel-placeholder">{copy.node.placeholder}</p>
      )}
      <div className="panel-actions">
        <a className="button" href={lineageHref(node.node_id)}>
          {copy.node.viewLineage}
        </a>
        {link && (
          <a className="button" href={link} target="_blank" rel="noreferrer">
            {copy.node.openInMetabase}
          </a>
        )}
      </div>
    </div>
  )
}

/**
 * 'Defined as' (#148): how this column is built, and — when lineage could not
 * follow it — why (#147). Every column answers one of the two, so the panel is
 * never silent about a column it has no upstreams for.
 *
 * For a passthrough, "how it is built" is two facts, not one: the parent it reads,
 * and the model the value was actually DEFINED in — which is usually not the parent,
 * because a parent that is itself a passthrough only restates the same edge (#162).
 * The origin line carries the second, and the SQL shown is the origin's expression
 * whenever there is one: this block is titled for the definition, so it shows the
 * definition rather than the last restatement of it.
 *
 * Renders nothing for a source column: warehouse-native, no SQL behind it, and
 * "unknown" there would be noise rather than a finding. The expression is also the
 * evidence for the upstream list right below it, which is why it sits above it.
 */
function DefinedAsBlock({ node }: { node: GraphNode }) {
  const info = columnDefinition(node)
  if (!info) return null
  const origin = info.definition?.origin
  return (
    <Section title={copy.node.definedAs}>
      {/* the reason leads: on an untraced column it IS the finding */}
      {info.reason && (
        <>
          <p className="defined-as-untraced">
            <span className="defined-as-reason" title={info.reason.hint}>
              {info.reason.label}
            </span>
          </p>
          <p className="muted defined-as-hint">{info.reason.hint}</p>
        </>
      )}
      {info.definition && (
        <div className={info.reason ? 'defined-as-body is-secondary' : 'defined-as-body'}>
          {/* the summary already names the upstream, so the SQL needs no caption */}
          <p className="defined-as-summary">{definedAsSummary(info.definition)}</p>
          {origin && (
            <p className="defined-as-origin">{definedOriginSummary(origin)}</p>
          )}
          <pre className="defined-as-sql">
            <code>{definitionSql(info.definition)}</code>
          </pre>
        </div>
      )}
    </Section>
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
  const reason = traceReason(node)
  const traceGap = reason && reason.code !== 'no_upstream_columns' ? reason : null

  return (
    <article className="panel">
      <PanelHeader
        node={node}
        subtitle={detail.model ? `column of ${displayName(detail.model)}` : nodeContext(index, node)}
        description={<></>}
      />
      <BiImpact
        cards={detail.cards}
        counts={detail.counts}
        truncated={detail.downstreamTruncated}
        uncertainty={traceGap && (
          <span title={traceGap.hint}>{copy.node.impactTraceGap(traceGap.label)}</span>
        )}
      />

      <Section title={copy.node.downstreamPaths(
        plural(detail.counts.downstreamModels, 'model'),
        plural(detail.counts.fields, 'Metabase field'),
      )}>
        {detail.downstreamTruncated && <p className="muted">{copy.node.listTruncated}</p>}
        <h4 className="subhead">{plural(detail.counts.downstreamModels, 'model')}</h4>
        {detail.downstreamTruncated && detail.downstreamModels.length === 0 ? (
          <p className="muted">{copy.node.noDisplayedEntries}</p>
        ) : <ChipList nodes={detail.downstreamModels.map((node) => ({ node }))} />}
        <h4 className="subhead">{plural(detail.counts.fields, 'Metabase field')}</h4>
        {reachChips(detail.fields, detail.downstreamTruncated)}
      </Section>

      <DefinedAsBlock node={node} />

      <Section
        title={copy.node.upstream(
          plural(detail.counts.upstreamColumns, 'column'),
          plural(detail.counts.upstreamSources, 'source'),
        )}
      >
        {detail.upstreamTruncated && <p className="muted">{copy.node.listTruncated}</p>}
        {detail.upstreamSources.length > 0 && (
          <>
            <h4 className="subhead">sources</h4>
            {reachChips(detail.upstreamSources, detail.upstreamTruncated)}
          </>
        )}
        <h4 className="subhead">columns</h4>
        {reachChips(detail.upstreamColumns, detail.upstreamTruncated)}
      </Section>

      <Section title={copy.node.objectDetails}>
        {entity ? (
          <DescriptionEditor
            entity={entity}
            column={node.column ?? displayName(node)}
            applied={node.description}
            staged={stagedFor(staging.descriptions, entity, node.column ?? displayName(node))}
            canStage={staging.canStage}
            refusal={refusalFor(staging.writeability, entity)}
            onChanged={staging.refresh}
          />
        ) : (node.description && <p className="panel-description">{node.description}</p>)}
        <dl className="fact-list">
          {/* A column inherits its table's manager, so it is answered here too — the
              question "can I change this" is asked of columns more than of tables. */}
          <ManagedByFact node={node} />
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
      </Section>

      {detail.relationships.length > 0 && (
        <Section title={copy.node.declaredRelationships}>
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
  return copy.node.unboundWhy()
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
        copy.node.gapFieldsUnbound(plural(fieldCount, 'Metabase field'), type, <UnboundWhy />)
      )}
      {gap === 'field-unbound' && (
        copy.node.gapFieldUnbound(<UnboundWhy />)
      )}
      {gap === 'native-unresolved' && (
        copy.node.gapNativeUnresolved
      )}
      {gap === 'query-unresolved' && (
        copy.node.gapQueryUnresolved()
      )}
      {gap === 'dashboard-unresolved' && (
        copy.node.gapDashboardUnresolved
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
    : `Depends on ${plural(detail.counts.columns, 'dbt column')} across ${plural(detail.counts.models, 'model')}`

  return (
    <article className="panel">
      <PanelHeader node={node} />
      {isField ? <FieldFacts node={node} /> : <CardFacts node={node} />}

      {isDashboard ? (
        <Section title={copy.node.cardsOnDashboard(String(detail.counts.cards))}>
          {detail.upstreamTruncated && <p className="muted">{copy.node.listTruncated}</p>}
          {reachChips(detail.cards, detail.upstreamTruncated)}
        </Section>
      ) : (
        detail.counts.dashboards > 0 && (
          <Section title={copy.node.appearsOn(plural(detail.counts.dashboards, 'dashboard'))}>
            {detail.downstreamTruncated && <p className="muted">{copy.node.listTruncated}</p>}
            {reachChips(detail.dashboards, detail.downstreamTruncated)}
          </Section>
        )
      )}

      {isField && detail.counts.cards > 0 && (
        <Section title={copy.node.consumedBy(plural(detail.counts.cards, 'card'))}>
          {detail.downstreamTruncated && <p className="muted">{copy.node.listTruncated}</p>}
          {reachChips(detail.cards, detail.downstreamTruncated)}
        </Section>
      )}

      <Section title={dependsTitle}>
        {detail.upstreamTruncated && <p className="muted">{copy.node.listTruncated}</p>}
        {detail.gap ? (
          <>
            <ChainGapNote gap={detail.gap} node={node} fieldCount={detail.counts.fields} />
            {detail.counts.fields > 0 && (
              <>
                {/* the half of the chain stitch did resolve: without it a card
                    whose tables are unbound looks like one that never parsed */}
                <h4 className="subhead">
                  {copy.node.fieldsItQueries(plural(detail.counts.fields, 'Metabase field'))}
                </h4>
                {reachChips(detail.fields, detail.upstreamTruncated)}
              </>
            )}
          </>
        ) : (
          <>
            <p className="muted">{copy.node.reverseView(NODE_TYPE_NAME[node.node_type])}</p>
            <h4 className="subhead">models</h4>
            {detail.upstreamTruncated && detail.dependsOnModels.length === 0 ? (
              <p className="muted">{copy.node.noDisplayedEntries}</p>
            ) : <ChipList nodes={detail.dependsOnModels.map((node) => ({ node }))} />}
            <h4 className="subhead">columns</h4>
            {reachChips(detail.dependsOnColumns, detail.upstreamTruncated)}
            {detail.counts.unboundFields > 0 && (
              <>
                <h4 className="subhead">
                  {copy.node.fieldsWithNoColumn(
                    plural(detail.counts.unboundFields, 'Metabase field'),
                  )}
                </h4>
                <p className="chain-gap">{copy.node.unboundFieldsNote(<UnboundWhy />)}</p>
                {reachChips(detail.unboundFields, detail.upstreamTruncated)}
              </>
            )}
          </>
        )}
      </Section>
    </article>
  )
}

function ModelPanel({ nodeId }: { nodeId: string }) {
  const { index, consequence } = useStitch()
  const usage = consequence.of(nodeId)
  const staging = useStaging(index)
  const detail = modelDetail(index, nodeId)
  const upstreamGroups = useMemo(() => layerGroups(detail?.upstream ?? [], 'up'), [detail])
  const downstreamGroups = useMemo(() => layerGroups(detail?.downstream ?? [], 'down'), [detail])
  if (!detail) return <NotFound nodeId={nodeId} />
  const { node } = detail
  const materialization = node.properties?.materialization
  const path = node.properties?.path
  const tags = Array.isArray(node.properties?.tags) ? (node.properties.tags as unknown[]).map(String) : []

  const traceGaps = detail.columns.filter((column) => {
    const reason = traceReason(column)
    return reason && reason.code !== 'no_upstream_columns'
  }).length
  const entity = fullName(node)
  return (
    <article className="panel">
      <PanelHeader
        node={node}
        description={<></>}
      />
      <BiImpact
        cards={detail.cards}
        counts={usage}
        truncated={detail.usageTruncated}
        uncertainty={traceGaps > 0 && copy.node.impactModelGaps(plural(traceGaps, 'column'))}
      />

      <Section title={copy.node.objectDetails}>
        <DescriptionEditor
          entity={entity}
          column={null}
          applied={node.description}
          staged={stagedFor(staging.descriptions, entity, null)}
          canStage={staging.canStage}
          refusal={refusalFor(staging.writeability, entity)}
          onChanged={staging.refresh}
        />
        <dl className="fact-list">
          {/* The first fact about a table is who manages it: it decides whether a
              change is yours to make at all (#187). */}
          <ManagedByFact node={node} />
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
      </Section>

      {detail.relationships.length > 0 && (
        <Section title={copy.node.declaredRelationships}>
          <RelationshipList relationships={detail.relationships} />
        </Section>
      )}

      <Section
        title={copy.node.dependencies(
          plural(detail.counts.upstream, 'model'),
          plural(detail.counts.downstream, 'model'),
        )}
      >
        {detail.upstreamTruncated && <p className="muted">{copy.node.upstreamListTruncated}</p>}
        <h4 className="subhead">upstream</h4>
        <LayerGroups
          groups={upstreamGroups}
          empty={
            detail.upstreamTruncated ? copy.node.noDisplayedEntries : node.node_type === 'source'
              ? copy.node.nothingUpstreamSource
              : copy.node.nothingUpstream
          }
        />
        {detail.downstreamTruncated && <p className="muted">{copy.node.downstreamListTruncated}</p>}
        <h4 className="subhead">downstream</h4>
        <LayerGroups groups={downstreamGroups} empty={detail.downstreamTruncated ? copy.node.noDisplayedEntries : copy.node.nothingDownstream} />
      </Section>

      <Section title={copy.node.columns(detail.columns.length)}>
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
            <span className="rel-arrow" title={rel.direction === 'outgoing' ? copy.node.references : copy.node.referencedBy}>
              {rel.direction === 'outgoing' ? '→' : '←'}
            </span>
            {rel.other ? <NodeChip node={rel.other} /> : <code>{rel.edge.to}</code>}
            {rel.validated ? (
              <span className="validated-badge" title={copy.node.validated}>
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
      <h2>{copy.node.notFound}</h2>
      <p className="muted">
        <code>{nodeId}</code>
        {copy.node.notFoundDetail()}
      </p>
    </article>
  )
}

export function NodePage({ nodeId }: { nodeId: string }) {
  const { index } = useStitch()
  const node = index.nodesById.get(nodeId)
  if (!node) return <NotFound nodeId={nodeId} />
  // Each node owns its editor and staging state. Remount on navigation so an
  // old draft or pending save cannot carry into another node of the same type.
  switch (node.node_type) {
    case 'column':
      return <ColumnPanel key={nodeId} nodeId={nodeId} />
    case 'mb_card':
    case 'mb_dashboard':
    case 'mb_field':
      return <BiPanel key={nodeId} nodeId={nodeId} />
    default:
      return <ModelPanel key={nodeId} nodeId={nodeId} />
  }
}
