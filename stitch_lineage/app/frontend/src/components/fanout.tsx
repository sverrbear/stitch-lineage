// The model page's dependency lists (#82), as collapsed rows (#104).
//
// Chip walls out, hierarchy in — and then one step further: a group is a ROW, with
// its count and how far away it sits, and the entries arrive when you ask for them.
// `dim_users` has 39 upstream models across three layers and 400 cards on 37
// dashboards; expanded by default that is a page you scroll past rather than read.
// So both kinds of group open closed, and both use the same row.
//
// Expansion is the `<details>` element's own state: per group, and deliberately not
// persisted — a page should open compact every time, not remember that it was once
// pulled apart.

import { useStitch } from '../data'
import { hopRange, type DashboardUsage, type LayerGroup } from '../lib/fanout'
import { metabaseLink } from '../lib/graph'
import { displayName } from '../lib/present'
import { nodeHref } from '../router'
import { ManagerBadge, NodeBadge } from './badges'
import { ConfidenceTag } from './bits'
import { copy } from '../copy'

function hops(depth: number): string {
  return depth === 1 ? 'direct' : `${depth} hops`
}

/** Models grouped by layer, in pipeline order — a collapsed row each. */
export function LayerGroups({ groups, empty }: { groups: LayerGroup[]; empty: string }) {
  if (groups.length === 0) return <p className="muted">{empty}</p>
  return (
    <div className="group-rows">
      {groups.map((group) => (
        <details key={group.label} className="group-panel dep-group">
          <summary className="group-head">
            <span className="group-name dep-group-name">{group.label}</span>
            <span className="muted group-hint">{hopRange(group.entries)}</span>
            <span className="muted group-count">
              {group.entries.length} model{group.entries.length === 1 ? '' : 's'}
            </span>
          </summary>
          <ul className="dep-rows">
            {group.entries.map((entry) => (
              <li key={entry.node.node_id} className="dep-row">
                <NodeBadge node={entry.node} />
                <a className="dep-row-name" href={nodeHref(entry.node.node_id)}>
                  {displayName(entry.node)}
                </a>
                <span className="muted dep-row-hops" title={copy.fanout.hops}>
                  {hops(entry.depth)}
                </span>
                {entry.confidence !== 'exact' && <ConfidenceTag confidence={entry.confidence} />}
              </li>
            ))}
          </ul>
        </details>
      ))}
    </div>
  )
}

/**
 * BI usage, grouped by the dashboard the cards sit on — the unit a reader
 * recognises. Collapsed, always: the count is the answer most of the time, and the
 * deep link is right there on the closed row (#104).
 */
export function DashboardGroups({ groups, empty }: { groups: DashboardUsage[]; empty: string }) {
  const { meta } = useStitch()
  if (groups.length === 0) return <p className="muted">{empty}</p>
  return (
    <div className="group-rows">
      {groups.map((group) => {
        const link = group.dashboard ? metabaseLink(meta.metabase_url, group.dashboard) : null
        return (
          <details key={group.dashboard?.node_id ?? 'no-dashboard'} className="group-panel bi-group">
            <summary className="group-head">
              <ManagerBadge nodeType="mb_dashboard" />
              {group.dashboard ? (
                <span className="group-name">{displayName(group.dashboard)}</span>
              ) : (
                <span className="group-name muted" title={copy.fanout.noDashboard}>
                  {copy.fanout.cardsOnNoDashboard}
                </span>
              )}
              <span className="muted group-count">
                {group.cards.length} card{group.cards.length === 1 ? '' : 's'}
              </span>
              {/* the two ways out of a closed row: the dashboard here, or its cards inside */}
              {group.dashboard && (
                <a className="group-link" href={nodeHref(group.dashboard.node_id)}>
                  {copy.fanout.details}
                </a>
              )}
              {link && (
                <a className="group-link bi-group-link" href={link} target="_blank" rel="noreferrer">
                  {copy.fanout.openExternal}
                </a>
              )}
            </summary>
            <ul className="bi-cards">
              {group.cards.map((card) => {
                const cardLink = metabaseLink(meta.metabase_url, card.node)
                return (
                  <li key={card.node.node_id} className="bi-card">
                    <NodeBadge node={card.node} />
                    <a className="bi-card-name" href={nodeHref(card.node.node_id)}>
                      {displayName(card.node)}
                    </a>
                    {card.confidence !== 'exact' && <ConfidenceTag confidence={card.confidence} />}
                    {cardLink && (
                      <a className="bi-card-link" href={cardLink} target="_blank" rel="noreferrer">
                        {copy.fanout.openExternal}
                      </a>
                    )}
                  </li>
                )
              })}
            </ul>
          </details>
        )
      })}
    </div>
  )
}
