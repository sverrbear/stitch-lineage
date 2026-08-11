import { describe, expect, it, vi } from 'vitest'
import {
  countBySource,
  defaultScopeFilter,
  defaultSourceFilter,
  dismissSuggestion,
  filterBySource,
  listSuggestions,
  probeSuggestions,
  rankSuggestions,
  scoreLabel,
  type Suggestion,
} from './suggestions'

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

const suggestion = (over: Partial<Suggestion> = {}): Suggestion => ({
  id: 'sug1',
  from_model: 'fct_orders',
  from_column: 'customer_id',
  to_model: 'dim_customers',
  to_column: 'customer_id',
  cardinality_guess: 'many-to-one',
  source: 'implicit_join',
  score: 7,
  ...over,
})

describe('probeSuggestions', () => {
  it('never asks when the build has no server at all', async () => {
    const fetcher = vi.fn()
    expect(await probeSuggestions(false, fetcher as unknown as typeof fetch)).toBe(false)
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('hides the whole layer when the endpoint is absent', async () => {
    // a `stitch serve` older than the suggestion engine: staging works, this does not
    const fetcher = vi.fn(async () => respond(404))
    expect(await probeSuggestions(true, fetcher as unknown as typeof fetch)).toBe(false)
  })

  it('enables the layer when the endpoint answers', async () => {
    const fetcher = vi.fn(async () => respond(200, { suggestions: [] }))
    expect(await probeSuggestions(true, fetcher as unknown as typeof fetch)).toBe(true)
  })
})

describe('listSuggestions', () => {
  it('unwraps the suggestions envelope', async () => {
    const entry = suggestion()
    const fetcher = vi.fn(async () => respond(200, { suggestions: [entry] }))
    expect(await listSuggestions(fetcher as unknown as typeof fetch)).toEqual([entry])
  })

  it('treats a body with no suggestions key as empty', async () => {
    const fetcher = vi.fn(async () => respond(200, {}))
    expect(await listSuggestions(fetcher as unknown as typeof fetch)).toEqual([])
  })

  it('surfaces a server error message', async () => {
    const fetcher = vi.fn(async () => respond(503, { detail: 'layout store is corrupt' }))
    await expect(listSuggestions(fetcher as unknown as typeof fetch)).rejects.toThrow(
      'layout store is corrupt',
    )
  })
})

describe('dismissSuggestion', () => {
  it('posts to the dismiss path', async () => {
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => respond(204))
    await dismissSuggestion('sug1', fetcher as unknown as typeof fetch)
    expect(fetcher.mock.calls[0][0]).toBe('api/suggestions/sug1/dismiss')
    expect((fetcher.mock.calls[0][1] as RequestInit).method).toBe('POST')
  })

  it('treats 404 as success — already dismissed is the state we wanted', async () => {
    const fetcher = vi.fn(async () => respond(404, { detail: 'unknown suggestion' }))
    await expect(dismissSuggestion('sug1', fetcher as unknown as typeof fetch)).resolves.toBeUndefined()
  })

  it('raises on a real failure', async () => {
    const fetcher = vi.fn(async () => respond(500, { detail: 'boom' }))
    await expect(dismissSuggestion('sug1', fetcher as unknown as typeof fetch)).rejects.toThrow('boom')
  })
})

describe('presentation', () => {
  it('says what a score means, because the number alone does not', () => {
    expect(scoreLabel(suggestion({ score: 7 }))).toBe('7 cards join through it')
    expect(scoreLabel(suggestion({ score: 1 }))).toBe('1 card joins through it')
    expect(scoreLabel(suggestion({ source: 'naming', score: 3 }))).toBe('matched on name')
  })

  it('ranks strongest first, and stably', () => {
    const ranked = rankSuggestions([
      suggestion({ id: 'b', score: 2 }),
      suggestion({ id: 'a', score: 9 }),
      suggestion({ id: 'c', score: 2 }),
    ])
    expect(ranked.map((s) => s.id)).toEqual(['a', 'b', 'c'])
  })

  it('does not mutate the list it was given', () => {
    const input = [suggestion({ id: 'b', score: 1 }), suggestion({ id: 'a', score: 5 })]
    rankSuggestions(input)
    expect(input.map((s) => s.id)).toEqual(['b', 'a'])
  })
})

describe('source filter', () => {
  // the real graph's shape: a handful of evidenced joins, hundreds of name guesses
  const many = [
    suggestion({ id: 'j1', source: 'implicit_join', score: 204 }),
    suggestion({ id: 'j2', source: 'implicit_join', score: 12 }),
    ...Array.from({ length: 20 }, (_, i) =>
      suggestion({ id: `n${i}`, source: 'naming', score: 0.5 }),
    ),
  ]

  it('counts each source and the total', () => {
    expect(countBySource(many)).toEqual({ implicit_join: 2, naming: 20, all: 22 })
  })

  it('counts zero sources without inventing keys', () => {
    expect(countBySource([])).toEqual({ implicit_join: 0, naming: 0, all: 0 })
  })

  it('filters to one source, or lets everything through', () => {
    expect(filterBySource(many, 'implicit_join').map((s) => s.id)).toEqual(['j1', 'j2'])
    expect(filterBySource(many, 'naming')).toHaveLength(20)
    expect(filterBySource(many, 'all')).toHaveLength(22)
  })

  it('keeps rank order inside a filtered source', () => {
    const ranked = rankSuggestions(filterBySource(many, 'implicit_join'))
    expect(ranked.map((s) => s.score)).toEqual([204, 12])
  })
})

// #121: the panel's default tab was "implicit join (0)" even where "naming (25)"
// had content — the first thing every reader saw was an empty list.
describe('defaultSourceFilter', () => {
  it('still leads with implicit joins when they have anything', () => {
    expect(defaultSourceFilter({ implicit_join: 15, naming: 325, all: 340 })).toBe('implicit_join')
  })

  it('opens on naming rather than on an empty implicit-join tab', () => {
    expect(defaultSourceFilter({ implicit_join: 0, naming: 25, all: 25 })).toBe('naming')
  })

  it('opens on the lead tab when there is genuinely nothing anywhere', () => {
    expect(defaultSourceFilter({ implicit_join: 0, naming: 0, all: 0 })).toBe('implicit_join')
  })
})

// #121: from the default scope the panel showed 0 of 271 and a sentence about
// why — a dead end at the exact moment the tool is supposed to earn its keep.
describe('defaultScopeFilter', () => {
  it('opens on every candidate when this scope holds none of them', () => {
    expect(defaultScopeFilter(0, 271)).toBe('all')
  })

  it('stays on the scope when the scope has its own to work through', () => {
    expect(defaultScopeFilter(25, 271)).toBe('scope')
  })

  it('does not claim other scopes have something when nothing does', () => {
    expect(defaultScopeFilter(0, 0)).toBe('scope')
  })
})
