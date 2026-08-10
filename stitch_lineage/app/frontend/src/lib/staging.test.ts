import { describe, expect, it, vi } from 'vitest'
import {
  StagingError,
  errorMessage,
  listStaged,
  probeStaging,
  stageRelationship,
  unstageRelationship,
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
