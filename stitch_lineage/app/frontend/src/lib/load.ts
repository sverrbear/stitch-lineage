// Data acquisition: static-export globals first, then the local API, then a
// dev-only fixture. All fetch paths are relative so the app works under any
// URL prefix (vite base './').

import type { StitchGraph, StitchMeta } from '../types'

const DEFAULT_META: StitchMeta = { metabase_url: null, generated_at: null, schema_version: 1 }

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`)
  return (await response.json()) as T
}

export interface LoadedData {
  graph: StitchGraph
  meta: StitchMeta
  /** Where the data came from, for the footer/status line. */
  origin: 'inline' | 'api' | 'dev-fixture'
}

export async function loadData(): Promise<LoadedData> {
  // Static-export mode: graph + meta inlined into index.html.
  if (window.__STITCH_GRAPH__) {
    const graph = window.__STITCH_GRAPH__
    const meta = window.__STITCH_META__ ?? {
      ...DEFAULT_META,
      generated_at: graph.generated_at ?? null,
      schema_version: graph.schema_version ?? 1,
    }
    return { graph, meta, origin: 'inline' }
  }

  // Served mode: the stitch serve local API.
  try {
    const graph = await fetchJson<StitchGraph>('api/graph')
    let meta = DEFAULT_META
    try {
      meta = await fetchJson<StitchMeta>('api/meta')
    } catch {
      meta = { ...DEFAULT_META, generated_at: graph.generated_at ?? null }
    }
    return { graph, meta, origin: 'api' }
  } catch (error) {
    // Dev convenience: `npm run dev` without a local API falls back to the
    // gitignored dev graph (see scripts/make-dev-graph.mjs).
    if (import.meta.env.DEV) {
      try {
        const graph = await fetchJson<StitchGraph>('dev-graph.json')
        return {
          graph,
          meta: { ...DEFAULT_META, generated_at: graph.generated_at ?? null },
          origin: 'dev-fixture',
        }
      } catch {
        throw error
      }
    }
    throw error
  }
}
