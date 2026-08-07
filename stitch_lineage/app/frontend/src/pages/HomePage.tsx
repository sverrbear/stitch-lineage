// Search-first home (spec §9): one search box, '/' focuses it.

import type { RefObject } from 'react'
import { MetabaseMark, SnowflakeMark } from '../components/badges'
import { SearchPanel } from '../components/SearchPanel'
import { useStitch } from '../data'

export function HomePage({ searchInputRef }: { searchInputRef: RefObject<HTMLInputElement | null> }) {
  const { index } = useStitch()
  const counts = new Map<string, number>()
  for (const node of index.graph.nodes) {
    counts.set(node.node_type, (counts.get(node.node_type) ?? 0) + 1)
  }
  const stat = (label: string, n?: number) => (n ? `${n.toLocaleString()} ${label}` : null)
  const stats = [
    stat('models', counts.get('model')),
    stat('sources', counts.get('source')),
    stat('columns', counts.get('column')),
    stat('fields', counts.get('mb_field')),
    stat('cards', counts.get('mb_card')),
    stat('dashboards', counts.get('mb_dashboard')),
  ].filter(Boolean)

  return (
    <main className="home">
      <div className="home-hero">
        <h1 className="home-title">
          <SnowflakeMark size={26} />
          <span>stitch</span>
          <MetabaseMark size={26} />
        </h1>
        <p className="home-subtitle">
          dbt ↔ Metabase column lineage — search anything, follow it end to end.
        </p>
        <SearchPanel inputRef={searchInputRef} autoFocus />
        <p className="home-stats">{stats.join(' · ')}</p>
        <p className="home-hint">
          <kbd>/</kbd> to search · <kbd>⌘K</kbd> palette · <a href="#/erd">browse the ERD</a>
        </p>
      </div>
    </main>
  )
}
