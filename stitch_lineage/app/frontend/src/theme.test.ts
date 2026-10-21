import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Exercise the real hook callbacks without adding a DOM renderer dependency.
const hook = vi.hoisted(() => ({ state: undefined as unknown, effects: [] as Array<() => void | (() => void)> }))
vi.mock('react', () => ({
  useState: (initial: unknown) => {
    if (hook.state === undefined) hook.state = typeof initial === 'function' ? initial() : initial
    return [hook.state, (next: unknown) => {
      hook.state = typeof next === 'function' ? next(hook.state) : next
    }]
  },
  useEffect: (effect: () => void | (() => void)) => { hook.effects.push(effect) },
}))

let dark: boolean
let onSystemChange: () => void
const storage = { getItem: vi.fn(), setItem: vi.fn() }
const media = { get matches() { return dark }, addEventListener: vi.fn(), removeEventListener: vi.fn() }

beforeEach(() => {
  vi.resetModules()
  hook.state = undefined
  hook.effects = []
  dark = false
  onSystemChange = () => {}
  storage.getItem.mockReset().mockReturnValue(null)
  storage.setItem.mockReset()
  media.addEventListener.mockReset().mockImplementation((_event, callback) => { onSystemChange = callback })
  media.removeEventListener.mockReset()
  vi.stubGlobal('localStorage', storage)
  vi.stubGlobal('window', { matchMedia: () => media })
  vi.stubGlobal('document', { documentElement: { dataset: {} } })
})

afterEach(() => { vi.unstubAllGlobals() })

async function mount() {
  const theme = await import('./theme')
  const result = theme.useTheme()
  const cleanups = hook.effects.map((effect) => effect())
  return { ...theme, result, cleanups }
}

describe('theme storage is optional', () => {
  it.each([false, true])('starts with the OS theme when reads are denied (dark=%s)', async (isDark) => {
    dark = isDark
    storage.getItem.mockImplementation(() => { throw new Error('SecurityError') })
    const { result } = await mount()
    expect(result[0]).toBe(isDark ? 'dark' : 'light')
    expect(document.documentElement.dataset.theme).toBe(result[0])
  })

  it('survives denial of access to the localStorage property itself', async () => {
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('SecurityError') } })
    const { result } = await mount()
    expect(result[0]).toBe('light')
    expect(() => result[1]()).not.toThrow()
    expect(hook.state).toBe('dark')
  })

  it('uses a valid stored preference and persists toggles when allowed', async () => {
    storage.getItem.mockReturnValue('dark')
    const { result, currentTheme } = await mount()
    expect(result[0]).toBe('dark')
    result[1]()
    expect(hook.state).toBe('light')
    expect(storage.setItem).toHaveBeenCalledWith('stitch-theme', 'light')
    expect(currentTheme()).toBe('light')
  })

  it('keeps a manual toggle in memory when saving fails, including after an OS change or remount', async () => {
    storage.getItem.mockReturnValue('light')
    storage.setItem.mockImplementation(() => { throw new Error('QuotaExceededError') })
    const { result, currentTheme, useTheme } = await mount()
    result[1]()
    expect(hook.state).toBe('dark')
    onSystemChange()
    expect(hook.state).toBe('dark')
    expect(currentTheme()).toBe('dark')
    hook.state = undefined
    expect(useTheme()[0]).toBe('dark')
  })

  it.each([null, 'invalid'])('follows OS changes without a valid override (%s)', async (stored) => {
    storage.getItem.mockReturnValue(stored)
    const { cleanups } = await mount()
    dark = true
    onSystemChange()
    expect(hook.state).toBe('dark')
    cleanups.forEach((cleanup) => cleanup?.())
    expect(media.removeEventListener).toHaveBeenCalledWith('change', onSystemChange)
  })

  it('follows OS changes when reads fail until the first manual toggle', async () => {
    storage.getItem.mockImplementation(() => { throw new Error('SecurityError') })
    storage.setItem.mockImplementation(() => { throw new Error('SecurityError') })
    const { result } = await mount()
    dark = true
    onSystemChange()
    expect(hook.state).toBe('dark')
    result[1]()
    expect(hook.state).toBe('light')
    onSystemChange()
    expect(hook.state).toBe('light')
  })
})

describe('pre-paint theme matches the runtime fallback', () => {
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
  const script = html.match(/<script>([\s\S]*?)<\/script>/)![1]
  it.each([false, true])('applies the OS theme before paint with denied storage (dark=%s)', (isDark) => {
    const document = { documentElement: { dataset: {} as { theme?: string } } }
    const context = { document, window: { matchMedia: () => ({ matches: isDark }) },
      get localStorage(): never { throw new Error('SecurityError') } }
    runInNewContext(script, context)
    expect(document.documentElement.dataset.theme).toBe(isDark ? 'dark' : 'light')
  })
})
