// The model page's dependency lists (#82). Chip walls out, hierarchy in: one row
// per entry, grouped by the layer it lives in or the dashboard it appears on, with
// the counts in the group heads so a reader can stop reading at any level.

import { useStitch } from '../data'
import type { DashboardUsage, LayerGroup } from '../lib/fanout'
import { metabaseLink } from '../lib/graph'
import { displayName } from '../lib/present'
import { nodeHref } from '../router'
import { SystemBadge } from './badges'
import { ConfidenceTag } from './bits'

function hops(depth: number): string {
  return depth === 1 ? 'direct' : `${depth} hops`
}

/** Models grouped by layer, in pipeline order — one compact row each. */
export function LayerGroups({ groups, empty }: { groups: LayerGroup[]; empty: string }) {
  if (groups.length === 0) return <p className="muted">{empty}</p>
  return (
    <div className="dep-groups">
      {groups.map((group) => (
        <section key={group.label} className="dep-group">
          <h4 className="dep-group-head">
            {group.label}
            <span className="muted dep-group-count">{group.entries.length}</span>
          </h4>
          <ul className="dep-rows">
            {group.entries.map((entry) => (
              <li key={entry.node.node_id} className="dep-row">
                <SystemBadge nodeType={entry.node.node_type} />
                <a className="dep-row-name" href={nodeHref(entry.node.node_id)}>
                  {displayName(entry.node)}
                </a>
                <span className="muted dep-row-hops" title="steps along the dependency chain">
                  {hops(entry.depth)}
                </span>
                {entry.confidence !== 'exact' && <ConfidenceTag confidence={entry.confidence} />}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

/**
 * BI usage, grouped by the dashboard the cards sit on — the unit a reader
 * recognises. Groups start open; the deep links go straight to Metabase.
 */
export function DashboardGroups({ groups, empty }: { groups: DashboardUsage[]; empty: string }) {
  const { meta } = useStitch()
  if (groups.length === 0) return <p className="muted">{empty}</p>
  // A heavily used table is on 37 dashboards and 400 cards: opening every group by
  // default would rebuild the chip wall this replaced. A small usage list stays open.
  const cards = groups.reduce((total, group) => total + group.cards.length, 0)
  const open = groups.length <= 3 && cards <= 12
  return (
    <div className="bi-groups">
      {groups.map((group) => {
        const link = group.dashboard ? metabaseLink(meta.metabase_url, group.dashboard) : null
        return (
          <details key={group.dashboard?.node_id ?? 'no-dashboard'} className="bi-group" open={open}>
            <summary className="bi-group-head">
              <SystemBadge nodeType="mb_dashboard" />
              {group.dashboard ? (
                <a className="bi-group-name" href={nodeHref(group.dashboard.node_id)}>
                  {displayName(group.dashboard)}
                </a>
              ) : (
                <span className="bi-group-name muted" title="not pinned to any dashboard">
                  cards on no dashboard
                </span>
              )}
              <span className="muted bi-group-count">
                {group.cards.length} card{group.cards.length === 1 ? '' : 's'}
              </span>
              {link && (
                <a className="bi-group-link" href={link} target="_blank" rel="noreferrer">
                  open ↗
                </a>
              )}
            </summary>
            <ul className="bi-cards">
              {group.cards.map((card) => {
                const cardLink = metabaseLink(meta.metabase_url, card.node)
                return (
                  <li key={card.node.node_id} className="bi-card">
                    <SystemBadge nodeType="mb_card" />
                    <a className="bi-card-name" href={nodeHref(card.node.node_id)}>
                      {displayName(card.node)}
                    </a>
                    {card.confidence !== 'exact' && <ConfidenceTag confidence={card.confidence} />}
                    {cardLink && (
                      <a className="bi-card-link" href={cardLink} target="_blank" rel="noreferrer">
                        open ↗
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
