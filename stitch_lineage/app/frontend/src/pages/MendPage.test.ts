import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { MendPage, MendReport, MendResults } from './MendPage'
import type { MendCard, MendResult } from '../lib/mend'

vi.mock('../data', () => ({ useStitch: () => ({ origin: 'inline', meta: { mend_enabled: true } }) }))

const card: MendCard = { card_id: 1, name: 'Monthly revenue', action: 'repoint', reason: 'Declared rename', collection: 'Finance', dashboards: ['Revenue'], repointed: [], removed_clauses: [], before: { field: 1 }, after: { field: 2 }, dead_refs: [{ column: 'orders.amount', rename_to: 'orders.amount_usd' }], dashcards: [{ dashboard_id: 3, dashboard_name: 'Revenue', dashcard_id: 4, before: [{ field: 1 }], after: [{ field: 2 }] }] }

describe('Mend review content', () => {
  it('shows plain-language rename and dashboard changes before the expandable query diff', () => {
    const html = renderToStaticMarkup(createElement(MendReport, { card, diff: '- field: 1\n+ field: 2', url: 'https://metabase.example.test' }))
    expect(html).toContain('orders.amount_usd')
    expect(html).toContain('Update filter wiring on Revenue')
    expect(html.indexOf('orders.amount_usd')).toBeLessThan(html.indexOf('Query before / after'))
    expect(html).toContain('https://metabase.example.test/question/1')
    expect(html).toContain('<details>')
  })
  it('warns about changed results for strips and hidden reports for archives', () => {
    expect(renderToStaticMarkup(createElement(MendReport, { card: { ...card, action: 'strip' }, url: null }))).toContain('change the numbers and meaning')
    expect(renderToStaticMarkup(createElement(MendReport, { card: { ...card, action: 'archive' }, url: null }))).toContain('Archiving hides reports')
  })
  it('static exports remain read-only even if an enabled flag is accidentally embedded', () => {
    const html = renderToStaticMarkup(createElement(MendPage))
    expect(html).toContain('This view is read-only')
    expect(html).not.toContain('type="submit"')
    expect(html).not.toContain('checkbox')
  })
  it('never counts dashboard failures or reverted changes as completed repairs', () => {
    const result: MendResult = { preview_id: 'id', outcome: { cards: [
      { card_id: 1, name: 'Partial dashboard repair', action: 'repoint', status: 'failed', detail: 'Dashboard write failed; card query ran', reverted: false, dashcards_written: 1 },
      { card_id: 2, name: 'Restored', action: 'repoint', status: 'failed', detail: 'Validation failed', reverted: true, dashcards_written: 0 },
      { card_id: 3, name: 'Manual SQL', action: 'notify', status: 'notify', detail: 'Native SQL', reverted: false, dashcards_written: 0 },
    ] } }
    const html = renderToStaticMarkup(createElement(MendResults, { result, url: null }))
    expect(html).toContain('0 completed changes · 2 need attention · 1 manual')
    expect(html).toContain('Reverted after query failure')
    expect(html).toContain('Failed — check details')
    expect(html).toContain('does not prove')
  })
  it('keeps the exact reviewed original query and dashboard wiring available after rollback failure', () => {
    const result: MendResult = { preview_id: 'id', outcome: { cards: [
      { card_id: 1, name: 'Revenue', action: 'repoint', status: 'failed', detail: 'COULD NOT REVERT', reverted: false, dashcards_written: 0 },
    ] }, recovery: { '1': { before: { original_field: 101 }, after: { new_field: 201 }, dashcards: card.dashcards } } }
    const html = renderToStaticMarkup(createElement(MendResults, { result, url: null }))
    expect(html).toContain('Original query for recovery')
    expect(html).toContain('original_field')
    expect(html).toContain('101')
    expect(html).toContain('Original filter wiring · Revenue')
    expect(html).toContain('until the next completed apply or server restart')
  })
})
