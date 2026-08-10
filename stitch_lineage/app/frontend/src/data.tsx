// Loads the graph once, builds the index + search index once, and provides
// them app-wide via context.

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { buildIndex, type GraphIndex } from './lib/graph'
import { loadData, type LoadedData } from './lib/load'
import { setStripModelPrefixes, setTablePrefixes } from './lib/present'
import { GraphSearch } from './lib/search'
import type { StitchMeta } from './types'

export interface StitchData {
  index: GraphIndex
  search: GraphSearch
  meta: StitchMeta
  origin: LoadedData['origin']
  /**
   * Re-read the graph and rebuild the index. `stitch apply` from the app patches
   * `graph.json` server-side (#68), so this is how the canvas and the panels show
   * the result without a page reload (#72).
   */
  reload: () => Promise<void>
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: LoadedData }

const DataContext = createContext<StitchData | null>(null)

export function useStitch(): StitchData {
  const data = useContext(DataContext)
  if (!data) throw new Error('useStitch outside DataProvider')
  return data
}

export function DataProvider({
  children,
  fallback,
}: {
  children: ReactNode
  fallback: (state: { loading: boolean; error?: string }) => ReactNode
}) {
  const [state, setState] = useState<LoadState>({ status: 'loading' })

  useEffect(() => {
    let cancelled = false
    loadData()
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data })
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({ status: 'error', message: error instanceof Error ? error.message : String(error) })
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  // A reload keeps the last good graph on failure: an apply that wrote the repo
  // must not leave the app on an error screen because the re-read hiccuped.
  const reload = useCallback(async () => {
    try {
      const data = await loadData()
      setState({ status: 'ready', data })
    } catch {
      // keep what is on screen
    }
  }, [])

  const value = useMemo<StitchData | null>(() => {
    if (state.status !== 'ready') return null
    // display names first: the search index is built from them (#69)
    setStripModelPrefixes(state.data.meta.strip_model_prefixes)
    setTablePrefixes(state.data.meta.table_prefixes)
    const index = buildIndex(state.data.graph)
    return {
      index,
      search: new GraphSearch(index),
      meta: state.data.meta,
      origin: state.data.origin,
      reload,
    }
  }, [state, reload])

  if (state.status === 'loading') return <>{fallback({ loading: true })}</>
  if (state.status === 'error') return <>{fallback({ loading: false, error: state.message })}</>
  return <DataContext.Provider value={value}>{children}</DataContext.Provider>
}
