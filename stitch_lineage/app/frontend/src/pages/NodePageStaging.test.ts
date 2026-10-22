import { beforeEach, describe, expect, it, vi } from 'vitest'
import { buildIndex } from '../lib/graph'
import { useStaging } from './NodePage'
import type { StagedDescription } from '../lib/staging'

// Exercise the hook's observable snapshots across real async completions. The
// small harness supplies React lifecycle ordering without requiring a browser.
const h = vi.hoisted(() => ({
  slots: [] as any[], cursor: 0, effects: [] as Array<() => void>, active: true,
  descriptions: vi.fn(),
}))
function same(a: unknown[] | undefined, b: unknown[] | undefined) {
  return !!a && !!b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]))
}
vi.mock('react', async () => ({
  ...await vi.importActual('react'),
  useContext: () => ({ nodeId: 'model.demo.orders', summaryActive: h.active }),
  useState: (initial: unknown) => {
    const i = h.cursor++
    if (!(i in h.slots)) h.slots[i] = initial
    return [h.slots[i], (value: unknown) => { h.slots[i] = value }]
  },
  useRef: (value: unknown) => {
    const i = h.cursor++
    return h.slots[i] ??= { current: value }
  },
  useCallback: (fn: unknown, deps: unknown[]) => {
    const i = h.cursor++
    if (!same(h.slots[i]?.deps, deps)) h.slots[i] = { deps, value: fn }
    return h.slots[i].value
  },
  useMemo: (fn: () => unknown) => fn(),
  useEffect: (fn: () => (() => void) | void, deps: unknown[]) => {
    const i = h.cursor++
    if (same(h.slots[i]?.deps, deps)) return
    h.effects.push(() => {
      h.slots[i]?.cleanup?.()
      h.slots[i] = { deps, cleanup: fn() }
    })
  },
}))
vi.mock('../data', () => ({ useStitch: () => ({ meta: { staging_enabled: true } }) }))
vi.mock('../lib/staging', async () => ({
  ...await vi.importActual('../lib/staging'),
  probeStaging: async () => true,
  fetchWriteability: async () => new Map(),
  listStagedDescriptions: h.descriptions,
}))

const initialIndex = buildIndex({ schema_version: 1, nodes: [], edges: [] })
function render(active = true, index = initialIndex) {
  h.active = active
  h.cursor = 0
  h.effects = []
  const result = useStaging(index)
  for (const effect of h.effects) effect()
  return result
}
const edit = (id: string): StagedDescription => ({ id, entity: 'orders', column: null, new_description: id })
async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve()
}

beforeEach(() => {
  h.slots = []
  h.active = true
  h.descriptions.mockReset().mockResolvedValue([])
})

describe('retained Summary staging snapshots', () => {
  it('refreshes after staged edits and graph reloads without a remount', async () => {
    h.descriptions.mockResolvedValue([edit('before')])
    render()
    await settle()
    expect(render().descriptions.map((entry) => entry.id)).toEqual(['before'])
    render(false)
    h.descriptions.mockResolvedValue([edit('after')])
    render(true)
    await settle()
    expect(render().descriptions.map((entry) => entry.id)).toEqual(['after'])
    h.descriptions.mockResolvedValue([])
    const newIndex = buildIndex({ schema_version: 1, nodes: [], edges: [] })
    render(true, newIndex)
    await settle()
    expect(render(true, newIndex).descriptions).toEqual([])
  })

  it('does not let an old hidden Summary request overwrite the new active snapshot', async () => {
    let finishOld!: (value: StagedDescription[]) => void
    h.descriptions.mockImplementationOnce(() => new Promise<StagedDescription[]>((resolve) => { finishOld = resolve }))
    render()
    await settle()
    render(false)
    h.descriptions.mockResolvedValue([edit('current')])
    render(true)
    await settle()
    expect(render().descriptions.map((entry) => entry.id)).toEqual(['current'])
    finishOld([edit('stale')])
    await settle()
    expect(render().descriptions.map((entry) => entry.id)).toEqual(['current'])
  })
})
