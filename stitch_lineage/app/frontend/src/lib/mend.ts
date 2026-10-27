import { copy } from '../copy'
import { errorMessage } from './staging'

export type MendAction = 'repoint' | 'strip' | 'archive' | 'notify'
export interface MendColumn { id: string; label: string; bound?: boolean }
export interface MendRenameSuggestion {
  column: string
  candidates: { id: string; label: string; evidence: string[]; also_suggested_for: string[] }[]
  ambiguous: boolean
  total_candidates: number
}
export interface MendContext {
  state: 'ready' | 'missing-baseline' | 'no-removed-columns'
  current: { file: string; generated_at: string | null }
  baseline: { file: string; generated_at: string | null } | null
  removed: MendColumn[]
  replacements: MendColumn[]
  suggestions: MendRenameSuggestion[]
  allowed_actions: MendAction[]
  type_changes: number
}
export interface MendCard {
  card_id: number; name: string; action: MendAction; reason: string
  collection: string | null; dashboards: string[]
  repointed: string[]; removed_clauses: string[]
  before: Record<string, unknown> | null; after: Record<string, unknown> | null
  dead_refs: { column: string; rename_to: string | null }[]
  dashcards: { dashboard_id: number; dashboard_name: string; dashcard_id: number;
    before: unknown[]; after: unknown[] }[]
}
export interface MendPreview {
  preview_id: string; expires_at: string; writing_count: number
  plan: { cards: MendCard[]; unresolved_renames: string[] }
  diffs: Record<string, string>
  reviews?: Record<string, MendExplanation>
}
export interface MendExplanation {
  changes: { label: string; before: string; after: string; plain: boolean }[]
  query_diff: string
  dashboards: { dashboard_id: number; dashcard_id: number; before: unknown[]; after: unknown[] }[]
}
export interface MendCardOutcome {
  card_id: number; name: string; action: MendAction
  status: 'applied' | 'archived' | 'notify' | 'stale' | 'skipped' | 'failed'
  detail: string | null; reverted: boolean; dashcards_written: number
}
export interface MendResult {
  preview_id: string; outcome: { cards: MendCardOutcome[] }
  recovery?: Record<string, Pick<MendCard, 'before' | 'after' | 'dashcards'>>
}

/** A complete server response is a definite refusal, unlike a lost transport. */
export class MendRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message) }
}

async function request<T>(path: string, body?: unknown, fetcher: typeof fetch = fetch): Promise<T> {
  // Static exports must never probe/mutate a server even when hosted beside one.
  if (typeof window !== 'undefined' && window.__STITCH_GRAPH__) throw new Error(copy.mend.readOnly)
  const response = await fetcher(`api/mend${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) throw new MendRequestError(await errorMessage(response), response.status)
  return response.json() as Promise<T>
}

export const loadMend = () => request<MendContext>('')
export const previewMend = (renames: Record<string, string>, actions: MendAction[]) =>
  request<MendPreview>('/preview', { renames, actions })
export const applyMend = (preview_id: string) =>
  request<MendResult>('/apply', { preview_id, confirmed: true })
export const mendResult = (id: string) => request<MendResult>(`/results/${encodeURIComponent(id)}`)

export function columnOptions(columns: MendColumn[]): (MendColumn & { value: string })[] {
  const counts = new Map<string, number>()
  for (const column of columns) counts.set(column.label, (counts.get(column.label) ?? 0) + 1)
  return columns.map((column) => ({ ...column, value: counts.get(column.label) === 1 ? column.label : column.id }))
}

export function snapshotTime(value: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return copy.mend.unknownTime
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

export function outcomeLabel(card: MendCardOutcome): string {
  if (card.status === 'failed') {
    if (card.reverted) return copy.mend.reverted
    if (card.detail?.includes('COULD NOT REVERT')) return copy.mend.rollbackFailed
    return copy.mend.failed
  }
  return copy.mend.status[card.status]
}

/** Only exact, unique targets can become a declared rename; free text cannot be sent. */
export function selectedRenames(drafts: Record<string, string>, columns: MendColumn[]):
  { renames: Record<string, string>; invalid: boolean } {
  const renames: Record<string, string> = {}
  let invalid = false
  for (const [old, text] of Object.entries(drafts)) {
    if (!text.trim()) continue
    const matches = columns.filter((c) => c.label === text || c.id === text)
    if (matches.length !== 1) invalid = true
    else renames[old] = matches[0].id
  }
  return { renames, invalid }
}

/** A click selects one exact current target; rendering hints never changes the drafts. */
export function chooseRenameSuggestion(drafts: Record<string, string>, old: string, target: string, columns: MendColumn[]): Record<string, string> {
  const option = columnOptions(columns).find((column) => column.id === target)
  return option ? { ...drafts, [old]: option.value } : drafts
}

/** A reload may change labels, but must never silently change the chosen target ID. */
export function reconcileRenameDrafts(drafts: Record<string, string>, previous: MendColumn[], fresh: Pick<MendContext, 'removed' | 'replacements'>): Record<string, string> {
  const currentOptions = columnOptions(fresh.replacements)
  return Object.fromEntries(Object.entries(drafts)
    .filter(([old]) => fresh.removed.some((column) => column.id === old))
    .map(([old, value]) => {
      const target = selectedRenames({ [old]: value }, previous).renames[old]
      return [old, target ? currentOptions.find((column) => column.id === target)?.value ?? target : value]
    }))
}
