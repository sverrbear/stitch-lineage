// The gap behind a coverage tile, in the app (#48). These lists used to exist
// only behind `stitch doctor --unbound` / `--untraced`, which is the wrong place
// for them: the thing you want after reading "109/239 bound" is the other 130,
// clickable.
//
// The untraced list gets its own view (#147), because a flat list of ids is a
// dead end: it groups by WHY, biggest cluster first, since one undocumented
// upstream takes its whole downstream subtree untraced with it. Reading the top
// row tells you which single fix buys the most.

import { useMemo, useState } from 'react'

import { NodeChip } from '../components/bits'
import { useStitch } from '../data'
import {
  coverageList,
  untracedGroups,
  type CoverageListKind,
  type UntracedGroup,
  type UntracedGrouping,
} from '../lib/coverage'
import { idTail } from '../lib/graph'
import { nodeHref } from '../router'

function Orphan({ nodeId }: { nodeId: string }) {
  // in the coverage block but not in the graph: name it from the id
  return (
    <span className="coverage-orphan">
      <code>{idTail(nodeId)}</code>
      <span className="muted">not in this graph</span>
    </span>
  )
}

const GROUPINGS: Array<{ value: UntracedGrouping; label: string }> = [
  { value: 'reason', label: 'reason' },
  { value: 'model', label: 'model' },
]

function UntracedGroupBlock({
  group,
  grouping,
  open,
  onToggle,
}: {
  group: UntracedGroup
  grouping: UntracedGrouping
  open: boolean
  onToggle: () => void
}) {
  return (
    <section className="untraced-group">
      <button
        type="button"
        className="untraced-group-head"
        aria-expanded={open}
        onClick={onToggle}
        title={group.hint ?? undefined}
      >
        <span className="untraced-group-caret" aria-hidden="true">
          {open ? '▾' : '▸'}
        </span>
        <span className="untraced-group-label">{group.label}</span>
        <span className="untraced-group-count">{group.entries.length.toLocaleString()}</span>
      </button>
      {/* the explanation sits with the cluster, because it IS the instruction */}
      {open && group.hint && <p className="untraced-group-hint">{group.hint}</p>}
      {open && group.node && (
        <p className="untraced-group-link">
          <a href={nodeHref(group.node.node_id)}>Open {group.label} →</a>
        </p>
      )}
      {open && (
        <ul className="coverage-list">
          {group.entries.map((entry) => (
            <li key={entry.nodeId}>
              {entry.node ? (
                // grouped by model, the heading already names it: repeating it on
                // every chip is noise, so the row carries the reason instead
                <NodeChip node={entry.node} context={grouping === 'model' ? null : undefined} />
              ) : (
                <Orphan nodeId={entry.nodeId} />
              )}
              {grouping === 'model' && entry.reason && (
                <span className="untraced-reason" title={entry.reason.hint}>
                  {entry.reason.label}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function UntracedColumns() {
  const { index } = useStitch()
  const [grouping, setGrouping] = useState<UntracedGrouping>('reason')
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const groups = useMemo(() => untracedGroups(index, grouping), [index, grouping])
  const total = groups.reduce((sum, group) => sum + group.entries.length, 0)

  const toggle = (key: string) =>
    setCollapsed((previous) => {
      const next = new Set(previous)
      if (!next.delete(key)) next.add(key)
      return next
    })

  return (
    <>
      <div className="untraced-controls">
        <span className="muted">Group by</span>
        {GROUPINGS.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`untraced-toggle${grouping === option.value ? ' is-active' : ''}`}
            aria-pressed={grouping === option.value}
            onClick={() => {
              setGrouping(option.value)
              // group keys mean something different per grouping
              setCollapsed(new Set())
            }}
          >
            {option.label}
          </button>
        ))}
        <span className="untraced-controls-summary">
          {groups.length.toLocaleString()} {grouping === 'reason' ? 'reasons' : 'models'} ·{' '}
          {total.toLocaleString()} columns
        </span>
      </div>
      <div className="untraced-groups">
        {groups.map((group) => (
          <UntracedGroupBlock
            key={group.key}
            group={group}
            grouping={grouping}
            open={!collapsed.has(group.key)}
            onToggle={() => toggle(group.key)}
          />
        ))}
      </div>
    </>
  )
}

export function CoveragePage({ kind }: { kind: CoverageListKind }) {
  const { index } = useStitch()
  const list = coverageList(index, kind)
  const untraced = kind === 'untraced-columns' && list.entries.length > 0

  return (
    <article className="panel">
      <div className="panel-header">
        {/* the count leads: the size of the gap IS the finding (principle 03) */}
        <div className="panel-title">
          <span className="panel-count">{list.entries.length.toLocaleString()}</span>
          <h2>{list.title}</h2>
        </div>
        <p className="panel-description">{list.description}</p>
        <div className="panel-actions">
          <a className="button" href="#/">
            ← Overview
          </a>
        </div>
      </div>

      {list.entries.length === 0 ? (
        <p className="muted panel-empty">Nothing here — this build has full coverage.</p>
      ) : untraced ? (
        <UntracedColumns />
      ) : (
        <ul className="coverage-list">
          {list.entries.map((entry) => (
            <li key={entry.nodeId}>
              {entry.node ? <NodeChip node={entry.node} /> : <Orphan nodeId={entry.nodeId} />}
            </li>
          ))}
        </ul>
      )}
    </article>
  )
}
