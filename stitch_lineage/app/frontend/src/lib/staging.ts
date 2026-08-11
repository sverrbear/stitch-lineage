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

export interface EditResult {
  relationship: StagedRelationship
  /**
   * The endpoints changed, so the store re-hashed the id and this is a different
   * entry than the one edited — the canvas has to restyle rather than patch (#71).
   */
  moved: boolean
}

/** Change a staged declaration in place (cardinality, or its endpoints). */
export async function editRelationship(
  id: string,
  request: StageRequest,
  fetcher: Fetcher = fetch,
): Promise<EditResult> {
  const response = await fetcher(`${ENDPOINT}/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ shape: 'simple', ...request }),
  })
  if (!response.ok) throw new StagingError(await errorMessage(response))
  const body = (await response.json()) as EditResult
  return { relationship: body.relationship, moved: body.moved === true }
}

export async function unstageRelationship(id: string, fetcher: Fetcher = fetch): Promise<void> {
  const response = await fetcher(`${ENDPOINT}/${encodeURIComponent(id)}`, { method: 'DELETE' })
  // 404 means it is already gone, which is the state the caller wanted
  if (!response.ok && response.status !== 404) {
    throw new StagingError(await errorMessage(response))
  }
}

// ---------------------------------------------------------------------------
// Staged descriptions (#70). Same store discipline as relationships: editing in
// the app changes nothing in the repo until `stitch apply` runs.

const DESCRIPTIONS = 'api/staged-descriptions'

export interface StagedDescription {
  id: string
  /** dbt model NAME; the writer resolves it to a schema file at apply time. */
  entity: string
  /** null is the model's OWN description, not a column's. */
  column: string | null
  new_description: string
  created_at?: string | null
}

export async function listStagedDescriptions(
  fetcher: Fetcher = fetch,
): Promise<StagedDescription[]> {
  const response = await fetcher(DESCRIPTIONS)
  if (!response.ok) throw new StagingError(await errorMessage(response))
  const body = (await response.json()) as { descriptions?: StagedDescription[] }
  return body.descriptions ?? []
}

export interface DescriptionResult {
  description: StagedDescription
  /** false when this entity+column was already staged — an edit replaces it. */
  created: boolean
}

export async function stageDescription(
  request: { entity: string; column: string | null; new_description: string },
  fetcher: Fetcher = fetch,
): Promise<DescriptionResult> {
  const response = await fetcher(DESCRIPTIONS, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
  if (!response.ok) throw new StagingError(await errorMessage(response))
  const body = (await response.json()) as DescriptionResult
  return { description: body.description, created: body.created !== false }
}

export async function unstageDescription(id: string, fetcher: Fetcher = fetch): Promise<void> {
  const response = await fetcher(`${DESCRIPTIONS}/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!response.ok && response.status !== 404) {
    throw new StagingError(await errorMessage(response))
  }
}

// ---------------------------------------------------------------------------
// Apply, from the app (#72). Preview first, then the same guards as the CLI —
// there is no force path here, by design.

/** A staged entry the apply engine could not (or need not) write, as it reports it. */
export interface ApplyProblem {
  /** The stored entry, plus `kind` and `label` the server derives for display. */
  entry: {
    kind?: 'relationship' | 'description'
    label?: string
    [key: string]: unknown
  }
  reason: string
  path?: string | null
}

export interface ApplyFile {
  path: string
  /** Unified diff, exactly as `stitch apply --dry-run` prints it. */
  diff: string
}

export interface ApplyPreview {
  write_to: string
  staged: { relationships: number; descriptions: number }
  files: ApplyFile[]
  unappliable: ApplyProblem[]
  unchanged: ApplyProblem[]
}

export interface ApplyOutcome {
  written: string[]
  /** Per-file refusals — a dirty target keeps its own file out, not the whole run. */
  refused: Array<{ path: string; reason: string }>
  applied: number
  still_staged: number
  unappliable: ApplyProblem[]
  graph: {
    patched: boolean
    edges_added?: number
    descriptions_updated?: number
    skipped?: string[]
    note?: string | null
  }
}

/**
 * Whether this build can apply. Same shape of question as `probeStaging`: an
 * explicit false is definitive, anything else is probed, because a serve without
 * an apply context has no such route and the button must simply not appear.
 */
export async function probeApply(
  applyEnabled: boolean | undefined,
  fetcher: Fetcher = fetch,
): Promise<boolean> {
  if (applyEnabled === false) return false
  try {
    // a HEAD-less probe: the preview route writes nothing, so asking is safe
    const response = await fetcher('api/apply/preview', { method: 'POST' })
    return response.status !== 404 && response.status !== 405
  } catch {
    return false
  }
}

export async function previewApply(fetcher: Fetcher = fetch): Promise<ApplyPreview> {
  const response = await fetcher('api/apply/preview', { method: 'POST' })
  if (!response.ok) throw new StagingError(await errorMessage(response))
  const body = (await response.json()) as Partial<ApplyPreview>
  return {
    write_to: body.write_to ?? '',
    staged: body.staged ?? { relationships: 0, descriptions: 0 },
    files: body.files ?? [],
    unappliable: body.unappliable ?? [],
    unchanged: body.unchanged ?? [],
  }
}

/**
 * Which models `stitch apply` could actually write into, asked once at load (#132).
 *
 * A model MISSING from the map is not a refusal: the endpoint only exists under a
 * serve with an apply context, and a build that cannot answer must not withhold
 * every affordance in the app. Absent means "no reason to think otherwise" and
 * apply keeps the final word — the behaviour that predates the endpoint.
 */
export type Writeability = ReadonlyMap<string, string>

export async function fetchWriteability(fetcher: Fetcher = fetch): Promise<Writeability> {
  const refusals = new Map<string, string>()
  try {
    const response = await fetcher('api/writeability')
    if (!response.ok) return refusals
    const body = (await response.json()) as {
      models?: Record<string, { writable?: boolean; reason?: string | null }>
    }
    for (const [model, item] of Object.entries(body.models ?? {})) {
      if (item?.writable === false) {
        refusals.set(model.toLowerCase(), item.reason ?? 'stitch cannot write this model’s file')
      }
    }
  } catch {
    // no route, no server, no opinion — every model stays offerable
  }
  return refusals
}

/** The reason this model cannot be written, or null. Names are matched case-insensitively. */
export function refusalFor(writeability: Writeability, model: string | null | undefined): string | null {
  if (!model) return null
  return writeability.get(model.toLowerCase()) ?? null
}

export async function applyStaged(fetcher: Fetcher = fetch): Promise<ApplyOutcome> {
  const response = await fetcher('api/apply', { method: 'POST' })
  if (!response.ok) throw new StagingError(await errorMessage(response))
  const body = (await response.json()) as Partial<ApplyOutcome>
  return {
    written: body.written ?? [],
    refused: body.refused ?? [],
    applied: body.applied ?? 0,
    still_staged: body.still_staged ?? 0,
    unappliable: body.unappliable ?? [],
    graph: body.graph ?? { patched: false },
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
