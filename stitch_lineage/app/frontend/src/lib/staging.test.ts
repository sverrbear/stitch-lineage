import { describe, expect, it, vi } from 'vitest'
import {
  StagingError,
  applyStaged,
  cardinalitySentence,
  editRelationship,
  errorMessage,
  groupStagedByTarget,
  listStaged,
  listStagedDescriptions,
  previewApply,
  probeApply,
  probeStaging,
  stageDescription,
  stageRelationship,
  unstageDescription,
  unstageRelationship,
  type StagedRelationship,
} from './staging'

function respond(status: number, body?: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (body === undefined) throw new SyntaxError('not json')
      return body
    },
  } as Response
}

const ENTRY = {
  id: 'abc123',
  from_model: 'fct_orders',
  from_column: 'customer_id',
  to_model: 'dim_customers',
  to_column: 'customer_id',
  cardinality: 'many-to-one',
  shape: 'simple',
  created_at: '2026-08-09T00:00:00+00:00',
}

describe('probeStaging', () => {
  it('is a definitive no when the build says staging is off', async () => {
    const fetcher = vi.fn()
    expect(await probeStaging(false, fetcher as unknown as typeof fetch)).toBe(false)
    expect(fetcher).not.toHaveBeenCalled() // no request worth making
  })

  it('probes once when the flag is absent — an older serve predates it', async () => {
    const fetcher = vi.fn(async () => respond(200, { relationships: [] }))
    expect(await probeStaging(undefined, fetcher as unknown as typeof fetch)).toBe(true)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('degrades to read-only when the endpoint is missing or the fetch throws', async () => {
    const missing = vi.fn(async () => respond(404))
    expect(await probeStaging(true, missing as unknown as typeof fetch)).toBe(false)
    const broken = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    expect(await probeStaging(true, broken as unknown as typeof fetch)).toBe(false)
  })
})

describe('listStaged', () => {
  it('unwraps the relationships envelope', async () => {
    const fetcher = vi.fn(async () => respond(200, { relationships: [ENTRY] }))
    expect(await listStaged(fetcher as unknown as typeof fetch)).toEqual([ENTRY])
  })

  it('treats a body with no relationships key as empty, not as a crash', async () => {
    const fetcher = vi.fn(async () => respond(200, {}))
    expect(await listStaged(fetcher as unknown as typeof fetch)).toEqual([])
  })
})

describe('stageRelationship', () => {
  const request = {
    from_model: 'fct_orders',
    from_column: 'customer_id',
    to_model: 'dim_customers',
    to_column: 'customer_id',
    cardinality: 'many-to-one',
  }

  it('posts the declaration with the default simple shape', async () => {
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) =>
      respond(201, { relationship: ENTRY, created: true }),
    )
    const result = await stageRelationship(request, fetcher as unknown as typeof fetch)
    expect(result.created).toBe(true)
    expect(result.relationship).toEqual(ENTRY)
    const init = fetcher.mock.calls[0][1] as RequestInit
    expect(JSON.parse(init.body as string)).toEqual({ shape: 'simple', ...request })
  })

  it('reports a re-stage as a dedupe rather than a creation', async () => {
    const fetcher = vi.fn(async () => respond(200, { relationship: ENTRY, created: false }))
    expect((await stageRelationship(request, fetcher as unknown as typeof fetch)).created).toBe(false)
  })

  it('surfaces the server’s 422 message verbatim', async () => {
    const detail = "from column 'ghost_id' is not a column of model 'fct_orders'"
    const fetcher = vi.fn(async () => respond(422, { detail }))
    await expect(stageRelationship(request, fetcher as unknown as typeof fetch)).rejects.toThrow(
      new StagingError(detail),
    )
  })
})

describe('unstageRelationship', () => {
  it('accepts 204', async () => {
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => respond(204))
    await expect(unstageRelationship('abc', fetcher as unknown as typeof fetch)).resolves.toBeUndefined()
    expect(fetcher.mock.calls[0][0]).toBe('api/staged-relationships/abc')
  })

  it('treats 404 as success — the entry is already gone', async () => {
    const fetcher = vi.fn(async () => respond(404, { detail: 'no staged relationship' }))
    await expect(unstageRelationship('abc', fetcher as unknown as typeof fetch)).resolves.toBeUndefined()
  })

  it('raises on a real failure', async () => {
    const fetcher = vi.fn(async () => respond(503, { detail: 'store is corrupt' }))
    await expect(unstageRelationship('abc', fetcher as unknown as typeof fetch)).rejects.toThrow(
      'store is corrupt',
    )
  })
})

describe('errorMessage', () => {
  it('prefers the detail string', async () => {
    expect(await errorMessage(respond(422, { detail: 'nope' }))).toBe('nope')
  })

  it('reads pydantic’s list form', async () => {
    expect(await errorMessage(respond(422, { detail: [{ msg: 'field required' }] }))).toBe(
      'field required',
    )
  })

  it('falls back to the status when the body is not JSON', async () => {
    expect(await errorMessage(respond(500))).toBe('request failed (HTTP 500)')
  })
})

describe('groupStagedByTarget', () => {
  const entry = (id: string, from: string, to: string, column = 'user_id'): StagedRelationship => ({
    id,
    from_model: from,
    from_column: column,
    to_model: to,
    to_column: column,
    cardinality: 'many-to-one',
    shape: 'simple',
  })

  it('groups by the model each entry points at, biggest group first', () => {
    const groups = groupStagedByTarget([
      entry('1', 'fct_orders', 'dim_users'),
      entry('2', 'fct_sessions', 'dim_dates', 'day_date'),
      entry('3', 'fct_events', 'dim_users'),
    ])
    expect(groups.map((group) => [group.target, group.entries.length])).toEqual([
      ['dim_users', 2],
      ['dim_dates', 1],
    ])
  })

  it('sorts inside a group by source model and column', () => {
    const groups = groupStagedByTarget([
      entry('1', 'fct_orders', 'dim_users', 'buyer_id'),
      entry('2', 'fct_events', 'dim_users'),
      entry('3', 'fct_orders', 'dim_users', 'agent_id'),
    ])
    expect(groups[0].entries.map((e) => `${e.from_model}.${e.from_column}`)).toEqual([
      'fct_events.user_id',
      'fct_orders.agent_id',
      'fct_orders.buyer_id',
    ])
  })

  it('breaks ties between equal-sized groups by name, so the panel never reshuffles', () => {
    const groups = groupStagedByTarget([
      entry('1', 'fct_a', 'dim_zones'),
      entry('2', 'fct_b', 'dim_apps'),
    ])
    expect(groups.map((group) => group.target)).toEqual(['dim_apps', 'dim_zones'])
  })

  it('returns nothing for nothing', () => {
    expect(groupStagedByTarget([])).toEqual([])
  })
})

describe('cardinalitySentence', () => {
  const shape = {
    fromModel: 'fct_match_activity_daily',
    fromColumn: 'match_id',
    toModel: 'dim_matches',
    toColumn: 'match_id',
    cardinality: 'many-to-one',
  }

  it('says which end is the one and which is the many', () => {
    expect(cardinalitySentence(shape)).toBe(
      'One dim_matches.match_id can have many matching rows in fct_match_activity_daily.',
    )
  })

  it('turns the sentence around for one-to-many', () => {
    expect(cardinalitySentence({ ...shape, cardinality: 'one-to-many' })).toBe(
      'One fct_match_activity_daily.match_id can have many matching rows in dim_matches.',
    )
  })

  it('says both ends are unique for one-to-one', () => {
    expect(cardinalitySentence({ ...shape, cardinality: 'one-to-one' })).toContain('at most one row')
  })

  it('falls back to the many-to-one reading rather than saying nothing', () => {
    expect(cardinalitySentence({ ...shape, cardinality: 'nonsense' })).toBe(
      cardinalitySentence(shape),
    )
  })
})

// --- editing, descriptions and apply (#70 / #71 / #72) -----------------------

describe('editRelationship', () => {
  it('PUTs the change and reports a re-hash as a move', async () => {
    const fetcher = vi.fn(async () => respond(200, { relationship: ENTRY, moved: true }))
    const result = await editRelationship(
      'old-id',
      {
        from_model: 'fct_orders',
        from_column: 'customer_id',
        to_model: 'dim_customers',
        to_column: 'customer_id',
        cardinality: 'one-to-one',
      },
      fetcher as unknown as typeof fetch,
    )
    expect(result.moved).toBe(true)
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('api/staged-relationships/old-id')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toMatchObject({ shape: 'simple', cardinality: 'one-to-one' })
  })

  it('treats an unchanged-endpoint edit as staying put', async () => {
    const fetcher = vi.fn(async () => respond(200, { relationship: ENTRY }))
    const result = await editRelationship(
      ENTRY.id,
      { ...ENTRY },
      fetcher as unknown as typeof fetch,
    )
    expect(result.moved).toBe(false)
  })

  it('surfaces the server’s refusal verbatim', async () => {
    const fetcher = vi.fn(async () => respond(422, { detail: "column 'ghost' is not a column" }))
    await expect(
      editRelationship('x', { ...ENTRY }, fetcher as unknown as typeof fetch),
    ).rejects.toThrow("column 'ghost' is not a column")
  })
})

describe('staged descriptions', () => {
  const DESCRIPTION = {
    id: 'desc-1',
    entity: 'fct_orders',
    column: 'customer_id',
    new_description: 'The customer.',
  }

  it('unwraps the descriptions envelope', async () => {
    const fetcher = vi.fn(async () => respond(200, { descriptions: [DESCRIPTION] }))
    expect(await listStagedDescriptions(fetcher as unknown as typeof fetch)).toEqual([DESCRIPTION])
  })

  it('treats a missing envelope as nothing staged', async () => {
    const fetcher = vi.fn(async () => respond(200, {}))
    expect(await listStagedDescriptions(fetcher as unknown as typeof fetch)).toEqual([])
  })

  it('PUTs an edit and says whether it replaced one', async () => {
    const fetcher = vi.fn(async () => respond(200, { description: DESCRIPTION, created: false }))
    const result = await stageDescription(
      { entity: 'fct_orders', column: 'customer_id', new_description: 'The customer.' },
      fetcher as unknown as typeof fetch,
    )
    expect(result.created).toBe(false)
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('api/staged-descriptions')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(String(init.body))).toEqual({
      entity: 'fct_orders',
      column: 'customer_id',
      new_description: 'The customer.',
    })
  })

  it('sends a model-level edit with an explicit null column', async () => {
    const fetcher = vi.fn(async () => respond(201, { description: DESCRIPTION, created: true }))
    await stageDescription(
      { entity: 'fct_orders', column: null, new_description: 'Orders.' },
      fetcher as unknown as typeof fetch,
    )
    const [, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(String(init.body)).column).toBeNull()
  })

  it('treats discarding an already-gone edit as done', async () => {
    const fetcher = vi.fn(async () => respond(404))
    await expect(
      unstageDescription('desc-1', fetcher as unknown as typeof fetch),
    ).resolves.toBeUndefined()
  })

  it('raises anything else', async () => {
    const fetcher = vi.fn(async () => respond(503, { detail: 'staged store is unreadable' }))
    await expect(
      unstageDescription('desc-1', fetcher as unknown as typeof fetch),
    ).rejects.toThrow(StagingError)
  })
})

describe('probeApply', () => {
  it('is a definitive no when the build says so', async () => {
    const fetcher = vi.fn()
    expect(await probeApply(false, fetcher as unknown as typeof fetch)).toBe(false)
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('accepts a refusal as proof the route exists', async () => {
    // 422 means "this apply is invalid", which still means apply is available
    const fetcher = vi.fn(async () => respond(422, { detail: 'nothing staged' }))
    expect(await probeApply(undefined, fetcher as unknown as typeof fetch)).toBe(true)
  })

  it('hides the button when the route is absent or the fetch throws', async () => {
    expect(await probeApply(true, (async () => respond(404)) as unknown as typeof fetch)).toBe(false)
    const broken = async () => {
      throw new TypeError('Failed to fetch')
    }
    expect(await probeApply(true, broken as unknown as typeof fetch)).toBe(false)
  })
})

describe('previewApply / applyStaged', () => {
  it('reads a preview as the panel needs it', async () => {
    const fetcher = vi.fn(async () =>
      respond(200, {
        write_to: 'meta',
        staged: { relationships: 2, descriptions: 1 },
        files: [{ path: 'models/_schema.yml', diff: '--- a\n+++ b\n' }],
        unappliable: [{ entry: { kind: 'relationship', label: 'a → b' }, reason: 'no schema file' }],
        unchanged: [],
      }),
    )
    const preview = await previewApply(fetcher as unknown as typeof fetch)
    expect(preview.files).toHaveLength(1)
    expect(preview.unappliable[0].entry.kind).toBe('relationship')
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('api/apply/preview')
    expect(init.method).toBe('POST')
  })

  it('fills in a partial preview rather than crashing the panel', async () => {
    const fetcher = vi.fn(async () => respond(200, {}))
    const preview = await previewApply(fetcher as unknown as typeof fetch)
    expect(preview).toEqual({
      write_to: '',
      staged: { relationships: 0, descriptions: 0 },
      files: [],
      unappliable: [],
      unchanged: [],
    })
  })

  it('reads an apply outcome, refusals included', async () => {
    const fetcher = vi.fn(async () =>
      respond(200, {
        written: ['models/a.yml'],
        refused: [{ path: 'models/b.yml', reason: 'has uncommitted changes' }],
        applied: 3,
        still_staged: 1,
        unappliable: [],
        graph: { patched: true, edges_added: 2, descriptions_updated: 1 },
      }),
    )
    const outcome = await applyStaged(fetcher as unknown as typeof fetch)
    expect(outcome.written).toEqual(['models/a.yml'])
    expect(outcome.refused[0].reason).toBe('has uncommitted changes')
    expect(outcome.graph.patched).toBe(true)
  })

  it('surfaces a 503 from either route', async () => {
    const broken = async () => respond(503, { detail: 'manifest is unreadable' })
    await expect(previewApply(broken as unknown as typeof fetch)).rejects.toThrow(
      'manifest is unreadable',
    )
    await expect(applyStaged(broken as unknown as typeof fetch)).rejects.toThrow(
      'manifest is unreadable',
    )
  })
})
