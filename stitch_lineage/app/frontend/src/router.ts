// Hand-rolled hash router. Hash routing means the server needs NO SPA
// fallback route and the static export works from file:// URLs.

import { useEffect, useState } from 'react'

import type { CoverageListKind } from './lib/coverage'
import type { Grain } from './lib/rollup'

export type Route =
  | { page: 'home' }
  | { page: 'node'; nodeId: string }
  | { page: 'lineage'; nodeId: string; grain: Grain }
  | { page: 'erd'; scopeKind?: 'schema' | 'tag'; scopeValue?: string }
  | { page: 'overview' }
  | { page: 'coverage'; kind: CoverageListKind }

const COVERAGE_KINDS: CoverageListKind[] = [
  'unbound-models',
  'untraced-columns',
  'unresolved-cards',
]

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, '')
  const segments = path.split('/').map(decodeURIComponent)
  if (segments[0] === 'node' && segments[1]) return { page: 'node', nodeId: segments[1] }
  if (segments[0] === 'lineage' && segments[1]) {
    return { page: 'lineage', nodeId: segments[1], grain: segments[2] === 'table' ? 'table' : 'column' }
  }
  if (segments[0] === 'overview') return { page: 'overview' }
  if (segments[0] === 'coverage' && COVERAGE_KINDS.includes(segments[1] as CoverageListKind)) {
    return { page: 'coverage', kind: segments[1] as CoverageListKind }
  }
  if (segments[0] === 'erd') {
    if ((segments[1] === 'schema' || segments[1] === 'tag') && segments[2]) {
      return { page: 'erd', scopeKind: segments[1], scopeValue: segments[2] }
    }
    return { page: 'erd' }
  }
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

export function overviewHref(): string {
  return '#/overview'
}

export function coverageHref(kind: CoverageListKind): string {
  return `#/coverage/${kind}`
}

export function erdHref(scopeKind?: 'schema' | 'tag', scopeValue?: string): string {
  if (scopeKind && scopeValue) return `#/erd/${scopeKind}/${encodeURIComponent(scopeValue)}`
  return '#/erd'
}

export function navigate(href: string): void {
  window.location.hash = href.startsWith('#') ? href.slice(1) : href
}
