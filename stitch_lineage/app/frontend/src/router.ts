// Hand-rolled hash router. Hash routing means the server needs NO SPA
// fallback route and the static export works from file:// URLs.

import { useEffect, useState } from 'react'

export type Route =
  | { page: 'home' }
  | { page: 'node'; nodeId: string }
  | { page: 'lineage'; nodeId: string }
  | { page: 'erd'; scopeKind?: 'schema' | 'tag'; scopeValue?: string }

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, '')
  const segments = path.split('/').map(decodeURIComponent)
  if (segments[0] === 'node' && segments[1]) return { page: 'node', nodeId: segments[1] }
  if (segments[0] === 'lineage' && segments[1]) return { page: 'lineage', nodeId: segments[1] }
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

export function lineageHref(nodeId: string): string {
  return `#/lineage/${encodeURIComponent(nodeId)}`
}

export function erdHref(scopeKind?: 'schema' | 'tag', scopeValue?: string): string {
  if (scopeKind && scopeValue) return `#/erd/${scopeKind}/${encodeURIComponent(scopeValue)}`
  return '#/erd'
}

export function navigate(href: string): void {
  window.location.hash = href.startsWith('#') ? href.slice(1) : href
}
