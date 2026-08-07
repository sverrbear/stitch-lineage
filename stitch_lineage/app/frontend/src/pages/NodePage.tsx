// Routed detail panels (spec §9): column / card / dashboard / model / source /
// field. Every panel links to the lineage view and carries system badges.

import { NODE_TYPE_NAME, SystemBadge } from '../components/badges'
import { ChipList, NodeChip, Section } from '../components/bits'
import { useStitch } from '../data'
import { biDetail, columnDetail, modelDetail } from '../lib/details'
import { metabaseLink } from '../lib/graph'
import type { Reach } from '../lib/graph'
import { lineageHref } from '../router'
import type { GraphNode } from '../types'

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

function reachChips(reaches: Reach[]) {
  return <ChipList nodes={reaches.map((r) => ({ node: r.node, confidence: r.confidence }))} />
}

function PanelHeader({ node, subtitle }: { node: GraphNode; subtitle?: string | null }) {
  const { meta } = useStitch()
  const link = metabaseLink(meta.metabase_url, node)
  const archived = node.properties?.archived === true
  return (
    <div className="panel-header">
      <div className="panel-title">
        <SystemBadge nodeType={node.node_type} size={20} />
        <h2>{node.name}</h2>
        <span className="panel-type">{NODE_TYPE_NAME[node.node_type]}</span>
        {archived && <span className="archived-tag">archived</span>}
      </div>
      {subtitle && <p className="panel-subtitle">{subtitle}</p>}
      {node.description && <p className="panel-description">{node.description}</p>}
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

function MbFacts({ node }: { node: GraphNode }) {
  const creator = node.properties?.creator ?? node.properties?.creator_name
  const collection = node.properties?.collection_name ?? node.properties?.collection
  return (
    <dl className="fact-list">
      {typeof creator === 'string' && creator && (
        <>
          <dt>creator</dt>
          <dd>{creator}</dd>
        </>
      )}
      {typeof collection === 'string' && collection && (
        <>
          <dt>collection</dt>
          <dd>{collection}</dd>
        </>
      )}
      <dt>archived</dt>
      <dd>{node.properties?.archived === true ? 'yes' : 'no'}</dd>
    </dl>
  )
}

function ColumnPanel({ nodeId }: { nodeId: string }) {
  const { index } = useStitch()
  const detail = columnDetail(index, nodeId)
  if (!detail) return <NotFound nodeId={nodeId} />
  const { node } = detail
  const headline =
    detail.cards.length > 0
      ? `Consumed by ${plural(detail.cards.length, 'card')} on ${plural(detail.dashboards.length, 'dashboard')}.`
      : 'Not consumed by any Metabase card in this graph.'

  return (
    <article className="panel">
      <PanelHeader node={node} subtitle={detail.model ? `column of ${detail.model.name}` : null} />
      <dl className="fact-list">
        <dt>data type</dt>
        <dd>{node.data_type ?? 'unknown'}</dd>
        <dt>model</dt>
        <dd>{detail.model ? <NodeChip node={detail.model} /> : '—'}</dd>
        {node.schema && (
          <>
            <dt>relation</dt>
            <dd>
              {node.schema}.{node.table}
            </dd>
          </>
        )}
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
          <ul className="rel-list">
            {detail.relationships.map((rel, i) => (
              <li key={i}>
                {rel.direction === 'outgoing' ? '→' : '←'}{' '}
                {rel.other ? <NodeChip node={rel.other} /> : rel.edge.to}{' '}
                {rel.validated && <span className="validated-badge" title="validated by a relationships test">✓</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </article>
  )
}

function BiPanel({ nodeId }: { nodeId: string }) {
  const { index } = useStitch()
  const detail = biDetail(index, nodeId)
  if (!detail) return <NotFound nodeId={nodeId} />
  const { node } = detail
  const isDashboard = node.node_type === 'mb_dashboard'

  return (
    <article className="panel">
      <PanelHeader node={node} />
      <MbFacts node={node} />

      {isDashboard ? (
        <Section title={`Cards on this dashboard — ${detail.cards.length}`}>{reachChips(detail.cards)}</Section>
      ) : (
        detail.dashboards.length > 0 && (
          <Section title={`Appears on ${plural(detail.dashboards.length, 'dashboard')}`}>
            {reachChips(detail.dashboards)}
          </Section>
        )
      )}

      {node.node_type === 'mb_field' && detail.cards.length > 0 && !isDashboard && (
        <Section title={`Consumed by ${plural(detail.cards.length, 'card')}`}>{reachChips(detail.cards)}</Section>
      )}

      <Section
        title={`Depends on ${plural(detail.dependsOnColumns.length, 'dbt column')} across ${plural(detail.dependsOnModels.length, 'model')}`}
      >
        <p className="muted">
          The reverse view: every dbt column this {NODE_TYPE_NAME[node.node_type]} ultimately
          depends on. Non-exact chains are flagged with their weakest hop.
        </p>
        {reachChips(detail.dependsOnColumns)}
      </Section>
    </article>
  )
}

function ModelPanel({ nodeId }: { nodeId: string }) {
  const { index } = useStitch()
  const detail = modelDetail(index, nodeId)
  if (!detail) return <NotFound nodeId={nodeId} />
  const { node } = detail
  const materialization = node.properties?.materialization
  const tags = Array.isArray(node.properties?.tags) ? (node.properties.tags as unknown[]).map(String) : []

  return (
    <article className="panel">
      <PanelHeader
        node={node}
        subtitle={[node.schema && node.table ? `${node.schema}.${node.table}` : null, typeof materialization === 'string' ? materialization : null]
          .filter(Boolean)
          .join(' · ')}
      />
      {tags.length > 0 && (
        <div className="tag-row">
          {tags.map((tag) => (
            <span key={tag} className="tag">
              {tag}
            </span>
          ))}
        </div>
      )}

      <Section
        title={`Fan-in / fan-out — ${plural(detail.upstreamModels.length, 'upstream model')}, ${plural(detail.downstreamModels.length, 'downstream model')}, ${plural(detail.cards.length, 'card')} on ${plural(detail.dashboards.length, 'dashboard')}`}
      >
        <h4 className="subhead">upstream</h4>
        <ChipList nodes={detail.upstreamModels.map((node) => ({ node }))} />
        <h4 className="subhead">downstream models</h4>
        <ChipList nodes={detail.downstreamModels.map((node) => ({ node }))} />
        {(detail.cards.length > 0 || detail.dashboards.length > 0) && (
          <>
            <h4 className="subhead">cards</h4>
            {reachChips(detail.cards)}
            <h4 className="subhead">dashboards</h4>
            {reachChips(detail.dashboards)}
          </>
        )}
      </Section>

      {detail.relationships.length > 0 && (
        <Section title={`Declared relationships — ${detail.relationships.length}`}>
          <ul className="rel-list">
            {detail.relationships.map((rel, i) => (
              <li key={i}>
                <code>{rel.direction === 'outgoing' ? rel.edge.from : rel.edge.to}</code>{' '}
                {rel.direction === 'outgoing' ? '→' : '←'}{' '}
                {rel.other ? <NodeChip node={rel.other} /> : <code>{rel.edge.to}</code>}{' '}
                {rel.validated && <span className="validated-badge" title="validated by a relationships test">✓</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}

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
                  <NodeChip node={column} />
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
