// Hand-rolled hash router. Hash routing means the server needs NO SPA
// fallback route and the static export works from file:// URLs.

import { useEffect, useState } from 'react'

import type { CoverageListKind } from './lib/coverage'
import type { Grain } from './lib/rollup'

export type ObjectRoute =
  | { page: 'node'; nodeId: string }
  | { page: 'lineage'; nodeId: string; grain: Grain }

export type Route =
  | { page: 'home' }
  | { page: 'not-found' }
  | ObjectRoute
  | { page: 'staged' }
  | { page: 'mend' }
  | { page: 'coverage'; kind: CoverageListKind }

const COVERAGE_KINDS: CoverageListKind[] = [
  'unbound-models',
  'untraced-columns',
  'unresolved-cards',
]

export function parseHash(hash: string): Route {
  const [path, query = ''] = hash.replace(/^#\/?/, '').split('?')
  let segments: string[]
  try {
    segments = path.split('/').map(decodeURIComponent)
    // URLSearchParams tolerates malformed escapes; reject them consistently with paths.
    decodeURIComponent(query.replace(/\+/g, ' '))
  } catch {
    return { page: 'not-found' }
  }
  if (segments[0] === 'staged') return { page: 'staged' }
  if (segments[0] === 'mend') return { page: 'mend' }
  // Retired object relationship bookmarks retain their selected object.
  if (segments[0] === 'node' && segments[1]) return { page: 'node', nodeId: segments[1] }
  if (segments[0] === 'lineage' && segments[1]) {
    return { page: 'lineage', nodeId: segments[1], grain: segments[2] === 'table' ? 'table' : 'column' }
  }
  if (segments[0] === 'coverage' && COVERAGE_KINDS.includes(segments[1] as CoverageListKind)) {
    return { page: 'coverage', kind: segments[1] as CoverageListKind }
  }
  // Retired global ERD bookmarks return to search.
  return { page: 'home' }
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function nodeHref(nodeId: string): string {
  return `#/node/${encodeURIComponent(nodeId)}`
}

export function lineageHref(nodeId: string, grain: Grain = 'column'): string {
  const base = `#/lineage/${encodeURIComponent(nodeId)}`
  return grain === 'table' ? `${base}/table` : base
}

export function coverageHref(kind: CoverageListKind): string {
  return `#/coverage/${kind}`
}

export function stagedHref(): string {
  return '#/staged'
}

export function navigate(href: string): void {
  window.location.hash = href.startsWith('#') ? href.slice(1) : href
}
