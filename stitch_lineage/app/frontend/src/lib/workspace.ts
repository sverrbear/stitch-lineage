// The staged workspace's presentation logic (#70 / #72). Pure TS, unit-tested.
//
// A modelling session ends here: everything staged, grouped by what it is, with
// one place to preview the exact YAML it will write and one button that writes it.
// So this module answers the three questions the panel asks — what is staged, how
// does each entry read, and what does a unified diff look like as rows — and the
// components stay presentation only.

import { groupStagedByTarget, type StagedDescription, type StagedGroup } from './staging'
import type { StagedRelationship } from './staging'
import { displayModelName } from './present'

export interface DescriptionGroup {
  /** The dbt model the edits belong to. */
  entity: string
  entries: StagedDescription[]
}

export interface WorkspaceView {
  total: number
  relationships: StagedGroup[]
  descriptions: DescriptionGroup[]
}

/** How a staged description edit reads: `dim_users.user_id`, or the model itself. */
export function descriptionLabel(entry: StagedDescription): string {
  const model = displayModelName(entry.entity)
  return entry.column ? `${model}.${entry.column}` : model
}

/** First line of the staged text, for a one-row preview of a long edit. */
export function descriptionPreview(entry: StagedDescription, limit = 90): string {
  const text = entry.new_description.trim().split('\n')[0] ?? ''
  if (text.length <= limit) return text
  return `${text.slice(0, limit - 1).trimEnd()}…`
}

/**
 * Everything staged, grouped by type: relationships by the model they point at
 * (the unit a reader scans for, #61), description edits by the model they belong
 * to, model-level edit first and then its columns by name.
 */
export function workspaceView(
  relationships: readonly StagedRelationship[],
  descriptions: readonly StagedDescription[],
): WorkspaceView {
  const byEntity = new Map<string, StagedDescription[]>()
  for (const entry of descriptions) {
    const group = byEntity.get(entry.entity)
    if (group) group.push(entry)
    else byEntity.set(entry.entity, [entry])
  }
  const grouped: DescriptionGroup[] = [...byEntity.entries()]
    .map(([entity, entries]) => ({
      entity,
      entries: [...entries].sort(
        (a, b) =>
          Number(a.column !== null) - Number(b.column !== null) ||
          (a.column ?? '').localeCompare(b.column ?? '') ||
          a.id.localeCompare(b.id),
      ),
    }))
    .sort((a, b) => a.entity.localeCompare(b.entity))

  return {
    total: relationships.length + descriptions.length,
    relationships: groupStagedByTarget([...relationships]),
    descriptions: grouped,
  }
}

export type DiffLineKind = 'meta' | 'hunk' | 'add' | 'del' | 'context'

export interface DiffLine {
  kind: DiffLineKind
  text: string
}

/**
 * A unified diff as rows to render. Read-only: the app shows exactly what
 * `stitch apply --dry-run` prints, so what is reviewed here is what lands.
 */
export function diffLines(diff: string): DiffLine[] {
  const rows: DiffLine[] = []
  for (const text of diff.replace(/\n$/, '').split('\n')) {
    if (text.startsWith('@@')) rows.push({ kind: 'hunk', text })
    else if (text.startsWith('+++') || text.startsWith('---') || text.startsWith('diff ')) {
      rows.push({ kind: 'meta', text })
    } else if (text.startsWith('+')) rows.push({ kind: 'add', text })
    else if (text.startsWith('-')) rows.push({ kind: 'del', text })
    else rows.push({ kind: 'context', text })
  }
  return rows
}

/** `+3 / −1`, the shape of a change at a glance. */
export function diffStat(diff: string): { added: number; removed: number } {
  let added = 0
  let removed = 0
  for (const row of diffLines(diff)) {
    if (row.kind === 'add') added++
    else if (row.kind === 'del') removed++
  }
  return { added, removed }
}

/**
 * What an apply attempt amounted to, in one sentence — the line a commit screen
 * owes the reader after the fact. Refusals lead, because they are the reason to
 * look further.
 */
export function outcomeSummary(outcome: {
  written: string[]
  refused: Array<{ path: string }>
  applied: number
  still_staged: number
}): string {
  const files = `${outcome.written.length} file${outcome.written.length === 1 ? '' : 's'}`
  const changes = `${outcome.applied} change${outcome.applied === 1 ? '' : 's'}`
  if (outcome.refused.length > 0) {
    const refused = `${outcome.refused.length} file${outcome.refused.length === 1 ? '' : 's'}`
    return `Wrote ${changes} to ${files}; ${refused} refused, ${outcome.still_staged} still staged.`
  }
  if (outcome.written.length === 0) {
    return outcome.applied > 0
      ? `Nothing to write — the repo already said all ${changes}; staged entries cleared.`
      : 'Nothing to write.'
  }
  return `Wrote ${changes} to ${files}. ${outcome.still_staged} still staged.`
}
