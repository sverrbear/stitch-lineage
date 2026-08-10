// Client for the staging API (#27) that the ERD's drawing gesture writes to (#24).
//
// Drawing stages a declaration in `.stitch/staged_relationships.yml`; nothing
// touches the dbt repo until somebody runs `stitch apply`. The API only exists
// under `stitch serve` — a static export has no server at all — so every entry
// point here degrades to read-only rather than erroring at the user.
//
// The response/error mapping is pure and unit-tested against a fake fetch.

/** One staged declaration, as the API stores it. */
export interface StagedRelationship {
  id: string
  /** dbt model NAME, not a unique_id — the writer resolves it at apply time. */
  from_model: string
  from_column: string
  to_model: string
  to_column: string
  cardinality: string
  shape: string
  created_at?: string | null
}

export interface StageRequest {
  from_model: string
  from_column: string
  to_model: string
  to_column: string
  cardinality: string
  shape?: string
}

export const CARDINALITIES = ['many-to-one', 'one-to-many', 'one-to-one'] as const
export type Cardinality = (typeof CARDINALITIES)[number]

const ENDPOINT = 'api/staged-relationships'

/** A refusal the user should read — the server's own message, not a status code. */
export class StagingError extends Error {}

/**
 * The message behind a non-OK response. FastAPI puts a validation refusal in
 * `detail`; anything else falls back to the status so the modal never shows an
 * empty error.
 */
export async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    const detail = body?.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown }
      if (typeof first?.msg === 'string') return first.msg
    }
  } catch {
    // not JSON — fall through to the status line
  }
  return `request failed (HTTP ${response.status})`
}

type Fetcher = typeof fetch

/**
 * Whether this build can stage at all. `staging_enabled: false` is a definitive
 * no (static export); otherwise the endpoint is probed once, because an older
 * `stitch serve` predates the flag and a missing endpoint must read as
 * read-only rather than as a broken canvas.
 */
export async function probeStaging(
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

export async function listStaged(fetcher: Fetcher = fetch): Promise<StagedRelationship[]> {
  const response = await fetcher(ENDPOINT)
  if (!response.ok) throw new StagingError(await errorMessage(response))
  const body = (await response.json()) as { relationships?: StagedRelationship[] }
  return body.relationships ?? []
}

export interface StageResult {
  relationship: StagedRelationship
  /** false when this exact column pair was already staged — a dedupe, not an error. */
  created: boolean
}

export async function stageRelationship(
  request: StageRequest,
  fetcher: Fetcher = fetch,
): Promise<StageResult> {
  const response = await fetcher(ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ shape: 'simple', ...request }),
  })
  if (!response.ok) throw new StagingError(await errorMessage(response))
  const body = (await response.json()) as StageResult
  return { relationship: body.relationship, created: body.created !== false }
}

export async function unstageRelationship(id: string, fetcher: Fetcher = fetch): Promise<void> {
  const response = await fetcher(`${ENDPOINT}/${encodeURIComponent(id)}`, { method: 'DELETE' })
  // 404 means it is already gone, which is the state the caller wanted
  if (!response.ok && response.status !== 404) {
    throw new StagingError(await errorMessage(response))
  }
}

// ---------------------------------------------------------------------------
// Presentation

export interface StagedGroup {
  /** The dbt model every entry in this group points at. */
  target: string
  entries: StagedRelationship[]
}

/**
 * Staged entries grouped by the model they point at (#61). A real session stages
 * a dozen FKs at once and most of them land on the same handful of dimensions —
 * "everything that joins to dim_users" is the unit a reader scans for, and a flat
 * list of seventeen is the unit they cannot. Biggest group first; ties by name,
 * so the panel never reshuffles between refreshes.
 */
export function groupStagedByTarget(entries: StagedRelationship[]): StagedGroup[] {
  const byTarget = new Map<string, StagedRelationship[]>()
  for (const entry of entries) {
    const group = byTarget.get(entry.to_model)
    if (group) group.push(entry)
    else byTarget.set(entry.to_model, [entry])
  }
  const groups: StagedGroup[] = [...byTarget.entries()].map(([target, group]) => ({
    target,
    entries: group.sort(
      (a, b) =>
        a.from_model.localeCompare(b.from_model) ||
        a.from_column.localeCompare(b.from_column) ||
        a.id.localeCompare(b.id),
    ),
  }))
  groups.sort((a, b) => b.entries.length - a.entries.length || a.target.localeCompare(b.target))
  return groups
}

export interface RelationshipShape {
  fromModel: string
  fromColumn: string
  toModel: string
  toColumn: string
  cardinality: string
}

/**
 * The declaration in plain English (#73). Cardinality is the one thing people
 * reliably get backwards, and "many-to-one" does not tell you which end is
 * which — a sentence with the real names does, which makes drawing the
 * relationship the wrong way round obvious before it is staged.
 */
export function cardinalitySentence(shape: RelationshipShape): string {
  const from = `${shape.fromModel}.${shape.fromColumn}`
  const to = `${shape.toModel}.${shape.toColumn}`
  switch (shape.cardinality) {
    case 'one-to-many':
      return `One ${from} can have many matching rows in ${shape.toModel}.`
    case 'one-to-one':
      return `Each ${from} matches at most one row in ${shape.toModel} (and the other way round).`
    default:
      return `One ${to} can have many matching rows in ${shape.fromModel}.`
  }
}
