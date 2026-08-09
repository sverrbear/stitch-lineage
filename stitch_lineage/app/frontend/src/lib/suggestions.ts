// Client for the suggestion API (#30): relationships stitch thinks exist but
// nobody has declared.
//
// A suggestion is a proposal, never a fact — accepting one goes through exactly
// the same staging path as drawing it by hand (there is no accept endpoint: the
// frontend POSTs the pair to /api/staged-relationships and the suggestion drops
// out server-side because staged pairs are excluded). Dismissing is permanent
// and persisted locally.
//
// Like staging, this only exists under `stitch serve`; the response/error
// mapping is pure and unit-tested against a fake fetch.

import { errorMessage, StagingError } from './staging'

export type SuggestionSource = 'implicit_join' | 'naming'

export interface Suggestion {
  id: string
  /** dbt model NAMES, matching the staging API — ids come from the same hash. */
  from_model: string
  from_column: string
  to_model: string
  to_column: string
  cardinality_guess: string
  source: SuggestionSource
  /** Witnessing cards for an implicit join; a weaker rank for a naming match. */
  score: number
  evidence?: Record<string, unknown>
}

const ENDPOINT = 'api/suggestions'

type Fetcher = typeof fetch

/**
 * Whether this build can suggest at all. Gated on `staging_enabled` first (a
 * static export has no server), then on the endpoint answering — a `serve` older
 * than the suggestion engine must show no suggestion layer rather than an empty
 * panel that looks like "stitch found nothing".
 */
export async function probeSuggestions(
  stagingEnabled: boolean | undefined,
  fetcher: Fetcher = fetch,
): Promise<boolean> {
  if (stagingEnabled === false) return false
  try {
    const response = await fetcher(ENDPOINT)
    return response.ok
  } catch {
    return false
  }
}

export async function listSuggestions(fetcher: Fetcher = fetch): Promise<Suggestion[]> {
  const response = await fetcher(ENDPOINT)
  if (!response.ok) throw new StagingError(await errorMessage(response))
  const body = (await response.json()) as { suggestions?: Suggestion[] }
  return body.suggestions ?? []
}

/** Permanent, and persisted: a dismissed suggestion never comes back. */
export async function dismissSuggestion(id: string, fetcher: Fetcher = fetch): Promise<void> {
  const response = await fetcher(`${ENDPOINT}/${encodeURIComponent(id)}/dismiss`, {
    method: 'POST',
  })
  // 404 means it is already gone — the state the caller wanted
  if (!response.ok && response.status !== 404) {
    throw new StagingError(await errorMessage(response))
  }
}

// ---------------------------------------------------------------------------
// Presentation

export const SOURCE_LABEL: Record<SuggestionSource, string> = {
  implicit_join: 'implicit join',
  naming: 'naming',
}

export const SOURCE_HELP: Record<SuggestionSource, string> = {
  implicit_join:
    'Metabase users already join through this field — stitch saw it in the cards below the score.',
  naming: 'The column names line up with another model’s key. Weaker evidence than a real join.',
}

/**
 * What the score means, in words. The number alone is meaningless across
 * sources: for an implicit join it counts the cards that witness it, for a
 * naming match it is only a rank.
 */
export function scoreLabel(suggestion: Suggestion): string {
  if (suggestion.source === 'implicit_join') {
    const n = Math.max(0, Math.round(suggestion.score))
    return n === 1 ? '1 card joins through it' : `${n} cards join through it`
  }
  return 'matched on name'
}

export type SourceFilter = SuggestionSource | 'all'

export interface SourceCounts {
  implicit_join: number
  naming: number
  all: number
}

export function countBySource(suggestions: Suggestion[]): SourceCounts {
  const counts: SourceCounts = { implicit_join: 0, naming: 0, all: suggestions.length }
  for (const entry of suggestions) counts[entry.source] += 1
  return counts
}

/**
 * The panel opens on implicit joins, because those are the ones with evidence:
 * on a real graph the naming heuristic proposes an order of magnitude more
 * candidates at a constant score, and 325 maybes would bury 15 knowns. The
 * filter shows both counts, so nothing is hidden without saying so.
 */
export function filterBySource(suggestions: Suggestion[], filter: SourceFilter): Suggestion[] {
  return filter === 'all' ? suggestions : suggestions.filter((entry) => entry.source === filter)
}

/** Strongest first, then stable by id so the panel never reshuffles on refresh. */
export function rankSuggestions(suggestions: Suggestion[]): Suggestion[] {
  return [...suggestions].sort(
    (a, b) =>
      b.score - a.score ||
      a.source.localeCompare(b.source) ||
      a.id.localeCompare(b.id),
  )
}
