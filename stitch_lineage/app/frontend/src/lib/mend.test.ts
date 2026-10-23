import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyMend, columnOptions, loadMend, MendRequestError, outcomeLabel, previewMend, selectedRenames, snapshotTime, type MendCardOutcome } from './mend'

afterEach(() => { vi.unstubAllGlobals() })

describe('browser Mend targets', () => {
  const columns = [
    { id: 'model.a.orders::amount_usd', label: 'orders.amount_usd', bound: true },
    { id: 'model.b.orders::amount_usd', label: 'orders.amount_usd', bound: false },
    { id: 'model.a.orders::region', label: 'orders.region', bound: true },
  ]
  it('declares only exact unique columns; ambiguous and free text cannot become renames', () => {
    expect(selectedRenames({ old: 'orders.amount_usd' }, columns)).toEqual({ renames: {}, invalid: true })
    expect(selectedRenames({ old: 'amount' }, columns).invalid).toBe(true)
    expect(selectedRenames({ old: 'orders.region' }, columns)).toEqual({ renames: { old: columns[2].id }, invalid: false })
    expect(selectedRenames({ old: '' }, columns)).toEqual({ renames: {}, invalid: false })
  })
  it('makes duplicate display labels selectable with unambiguous values', () => {
    const options = columnOptions(columns)
    expect(options[0].value).toBe(columns[0].id)
    expect(options[1].value).toBe(columns[1].id)
    expect(options[2].value).toBe(columns[2].label)
    expect(selectedRenames({ old: options[1].value }, columns)).toEqual({ renames: { old: columns[1].id }, invalid: false })
  })
  it('formats snapshot dates and admits absent or invalid dates', () => {
    expect(snapshotTime('2026-08-11T00:00:00Z')).toContain('2026')
    expect(snapshotTime(null)).toBe('Build time unavailable')
    expect(snapshotTime('invalid')).toBe('Build time unavailable')
  })
})

describe('browser Mend HTTP contract', () => {
  it('sends declared mappings/action selections only when planning, and exact ID/confirmation only when applying', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetcher)
    await previewMend({ old: 'new' }, ['repoint'])
    const previewBody = JSON.parse(fetcher.mock.calls[0][1].body)
    expect(previewBody).toEqual({ renames: { old: 'new' }, actions: ['repoint'] })
    await applyMend('server-held-preview')
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ preview_id: 'server-held-preview', confirmed: true })
    expect(fetcher.mock.calls[1][1].method).toBe('POST')
  })
  it('retains a definite HTTP refusal distinct from transport failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 410, json: async () => ({ detail: 'Expired; review again.' }) }))
    await expect(applyMend('expired')).rejects.toEqual(new MendRequestError('Expired; review again.', 410))
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Network interrupted')))
    await expect(applyMend('unknown')).rejects.toBeInstanceOf(TypeError)
  })
  it('never probes or applies from a static export even when hosted next to an API', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    vi.stubGlobal('window', { __STITCH_GRAPH__: { nodes: [], edges: [] } })
    await expect(loadMend()).rejects.toThrow('read-only')
    await expect(previewMend({}, ['repoint'])).rejects.toThrow('read-only')
    await expect(applyMend('id')).rejects.toThrow('read-only')
    expect(fetcher).not.toHaveBeenCalled()
  })
})

describe('truthful result labels', () => {
  const result: MendCardOutcome = { card_id: 1, name: 'Report', action: 'repoint', status: 'failed', detail: null, reverted: false, dashcards_written: 0 }
  it('distinguishes restored query, rollback failure, and partial dashboard failure', () => {
    expect(outcomeLabel({ ...result, reverted: true })).toBe('Reverted after query failure')
    expect(outcomeLabel({ ...result, detail: 'COULD NOT REVERT: synthetic' })).toBe('Rollback failed — action required')
    expect(outcomeLabel({ ...result, detail: 'dashboard write failed', dashcards_written: 1 })).toBe('Failed — check details')
    expect(outcomeLabel({ ...result, status: 'applied' })).toBe('Updated · query ran')
    expect(outcomeLabel({ ...result, status: 'archived' })).toBe('Archived · not executed')
  })
})
