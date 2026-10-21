import { useMemo, type ReactNode } from 'react'
import { copy, plural } from '../copy'
import { useStitch } from '../data'
import { dashboardCount, dashboardGroups } from '../lib/fanout'
import type { Reach } from '../lib/graph'
import { DashboardGroups } from './fanout'

/** Complete consequence counts lead; the dashboard rows are a bounded sample. */
export function BiImpact({
  cards,
  counts,
  truncated,
  uncertainty,
}: {
  cards: Reach[]
  counts: { cards: number; dashboards: number }
  truncated: boolean
  uncertainty?: ReactNode
}) {
  const { index } = useStitch()
  const groups = useMemo(() => dashboardGroups(index, cards), [index, cards])
  const partial = truncated || cards.length < counts.cards || dashboardCount(groups) < counts.dashboards
  return (
    <section className="panel-section impact-section">
      <h3>{copy.node.impact}</h3>
      <p className="impact-headline">
        {counts.cards > 0
          ? copy.node.impactSummary(plural(counts.cards, 'card'), plural(counts.dashboards, 'dashboard'))
          : copy.node.noImpact}
      </p>
      <p className="muted impact-scope">{copy.node.impactScope}</p>
      {uncertainty && <p className="chain-gap">{uncertainty}</p>}
      {partial && <p className="muted">{copy.node.listTruncated}</p>}
      {(counts.cards > 0 || partial) && (
        <DashboardGroups groups={groups} empty={copy.node.noDisplayedEntries} />
      )}
    </section>
  )
}
