// The search core (spec §9: search-first, keyboard-first). Used inline on the
// home page and inside the Cmd/Ctrl+K palette. Results grouped by node type,
// arrow keys + enter to navigate.

import { useEffect, useMemo, useRef, useState } from 'react'
import { useStitch } from '../data'
import { groupHits, type SearchHit } from '../lib/search'
import { NODE_TYPE_NAME, displayName } from '../lib/present'
import { navigate, nodeHref } from '../router'
import { SystemBadge } from './badges'

export interface SearchPanelProps {
  autoFocus?: boolean
  placeholder?: string
  onNavigate?: () => void
  /** Exposes the input so a global '/' handler can focus it. */
  inputRef?: React.RefObject<HTMLInputElement | null>
  /** The home page's field: the tall one the whole screen is built around (7a). */
  hero?: boolean
}

export function SearchPanel({
  autoFocus,
  placeholder,
  onNavigate,
  inputRef,
  hero,
}: SearchPanelProps) {
  const { search } = useStitch()
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const localRef = useRef<HTMLInputElement | null>(null)
  const ref = inputRef ?? localRef
  const listRef = useRef<HTMLDivElement | null>(null)

  const hits = useMemo(() => search.search(query, 40), [search, query])
  const groups = useMemo(() => groupHits(hits), [hits])
  const flat = useMemo(() => groups.flatMap((g) => g.hits), [groups])

  useEffect(() => setCursor(0), [query])

  useEffect(() => {
    const el = listRef.current?.querySelector('[data-active="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  const go = (hit: SearchHit) => {
    navigate(nodeHref(hit.node.node_id))
    onNavigate?.()
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setCursor((c) => Math.min(c + 1, flat.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCursor((c) => Math.max(c - 1, 0))
    } else if (e.key === 'Enter' && flat[cursor]) {
      e.preventDefault()
      go(flat[cursor])
    }
  }

  let flatIndex = -1
  return (
    <div className={`search-panel${hero ? ' hero' : ''}`}>
      <input
        ref={ref}
        className="search-input"
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder ?? 'Search models, columns, cards, dashboards…'}
        autoFocus={autoFocus}
        spellCheck={false}
        autoComplete="off"
        aria-label="Search the lineage graph"
      />
      {query.trim() !== '' && (
        <div className="search-results" ref={listRef}>
          {flat.length === 0 && <p className="muted search-empty">No matches.</p>}
          {groups.map((group) => (
            <div key={group.type} className="search-group">
              <div className="search-group-label">{group.label}</div>
              {group.hits.map((hit) => {
                flatIndex += 1
                const active = flatIndex === cursor
                const idx = flatIndex
                return (
                  <button
                    key={hit.node.node_id}
                    type="button"
                    className={`search-hit${active ? ' active' : ''}`}
                    data-active={active || undefined}
                    onMouseEnter={() => setCursor(idx)}
                    onClick={() => go(hit)}
                    title={hit.node.node_id}
                  >
                    <SystemBadge nodeType={hit.node.node_type} />
                    <span className="search-hit-name">{displayName(hit.node)}</span>
                    <span className="search-hit-type">{NODE_TYPE_NAME[hit.node.node_type]}</span>
                    {hit.context && <span className="search-hit-context">{hit.context}</span>}
                    {hit.matchedField !== 'name' && (
                      <span className="search-hit-field">via {hit.matchedField}</span>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
