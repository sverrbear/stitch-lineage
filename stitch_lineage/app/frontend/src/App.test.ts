import { createElement, type ReactNode } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'

vi.mock('./data', () => ({
  DataProvider: ({ children }: { children: ReactNode }) => children,
  useStitch: () => ({ meta: { generated_at: null }, origin: 'static' }),
}))

afterEach(() => { vi.unstubAllGlobals() })

describe('malformed link recovery', () => {
  it.each(['#/node/%', '#/lineage/%FF', '#/erd/schema/%2'])('renders navigation and a recoverable error for %s', (hash) => {
    vi.stubGlobal('localStorage', { getItem: () => null })
    vi.stubGlobal('window', { location: { hash }, matchMedia: () => ({ matches: false }) })
    const html = renderToStaticMarkup(createElement(App))
    expect(html).toContain('Page not found')
    expect(html).toContain('This link is invalid.')
    expect(html).toContain('class="app-header"')
    expect(html).toContain('class="button" href="#/">Home</a>')
    expect(html).toContain('Switch to dark theme')
  })
})
