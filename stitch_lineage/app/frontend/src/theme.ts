// Light/dark theming: prefers-color-scheme default, manual toggle persisted
// to localStorage. index.html applies the same logic pre-paint.

import { useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'
const STORAGE_KEY = 'stitch-theme'
// A manual choice still lasts for this page session when storage is unavailable.
let sessionTheme: Theme | null = null

function systemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function preferredTheme(): Theme | null {
  if (sessionTheme) return sessionTheme
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // Sandboxed exports and browser privacy settings can deny even a read.
  }
  return null
}

export function currentTheme(): Theme {
  return preferredTheme() ?? systemTheme()
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(currentTheme)

  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  // Follow OS changes while no manual override is stored.
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => {
      if (!preferredTheme()) setTheme(systemTheme())
    }
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  const toggle = () => {
    setTheme((prev) => {
      const next: Theme = prev === 'dark' ? 'light' : 'dark'
      sessionTheme = next
      try {
        localStorage.setItem(STORAGE_KEY, next)
      } catch {
        // The in-memory choice remains usable without persistence.
      }
      return next
    })
  }
  return [theme, toggle]
}
