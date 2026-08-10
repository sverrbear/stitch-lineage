// Search-first home (spec §9) with an overview under it (#48): what this build
// covers, how big it is, how old it is, and where to start. '/' and ⌘K are
// untouched — the search box is still the first thing focused.

import { useMemo, type RefObject } from 'react'
import { MetabaseMark, SnowflakeMark } from '../components/badges'
import { NodeChip } from '../components/bits'
import { SearchPanel } from '../components/SearchPanel'
import { useStitch } from '../data'
import { coverageTiles, graphStats, startingPoints, type CoverageTile } from '../lib/coverage'
import { NODE_TYPE_NAME } from '../lib/present'
import { coverageHref, erdHref, overviewHref } from '../router'

const STALE_DAYS = 7

function Tile({ tile }: { tile: CoverageTile }) {
  const pct = tile.total ? Math.round((tile.value / tile.total) * 100) : null
  return (
    <div className="tile" title={tile.hint}>
      <div className="tile-value">
        {tile.value.toLocaleString()}
        {tile.total !== null && <span className="tile-total">/{tile.total.toLocaleString()}</span>}
      </div>
      <div className="tile-label">{tile.label}</div>
      {pct !== null && (
        <div className="tile-bar" aria-hidden="true">
          <div className="tile-bar-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
      {tile.list ? (
        <a className="tile-link" href={coverageHref(tile.list)}>
          {tile.listLabel} →
        </a>
      ) : (
        <span className="tile-link muted">complete</span>
      )}
    </div>
  )
}

export function HomePage({ searchInputRef }: { searchInputRef: RefObject<HTMLInputElement | null> }) {
  const { index, meta } = useStitch()
  const stats = useMemo(() => graphStats(index.graph, new Date()), [index])
  const tiles = useMemo(() => coverageTiles(index.graph.coverage), [index])
  const starts = useMemo(() => startingPoints(index), [index])
  const generatedAt = stats.generatedAt ?? meta.generated_at

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
        <p className="home-hint">
          <kbd>/</kbd> to search · <kbd>⌘K</kbd> palette
        </p>
      </div>

      <section className="home-section">
        <h2 className="home-section-title">Coverage</h2>
        <div className="tile-row">
          {tiles.map((tile) => (
            <Tile key={tile.key} tile={tile} />
          ))}
        </div>
      </section>

      <section className="home-section">
        <h2 className="home-section-title">Start here</h2>
        <div className="entry-row">
          <a className="entry-card" href={overviewHref()}>
            <span className="entry-card-title">Pipeline map</span>
            <span className="entry-card-text">
              Every model and source at table grain, laid out by dependency order, with Metabase
              consumption aggregated on the right.
            </span>
          </a>
          <a className="entry-card" href={erdHref()}>
            <span className="entry-card-title">ERD</span>
            <span className="entry-card-text">
              Tables and their declared relationships, one schema or dbt tag at a time.
            </span>
          </a>
        </div>
        <div className="start-lists">
          <div className="start-list">
            <h3 className="subhead">most consumed models</h3>
            {starts.mostConsumedModels.length === 0 ? (
              <p className="muted">no Metabase consumption in this graph</p>
            ) : (
              <ul className="start-items">
                {starts.mostConsumedModels.map(({ node, count }) => (
                  <li key={node.node_id}>
                    <NodeChip node={node} />
                    <span className="muted">{count} cards</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="start-list">
            <h3 className="subhead">biggest dashboards</h3>
            {starts.biggestDashboards.length === 0 ? (
              <p className="muted">no dashboards in this graph</p>
            ) : (
              <ul className="start-items">
                {starts.biggestDashboards.map(({ node, count }) => (
                  <li key={node.node_id}>
                    <NodeChip node={node} />
                    <span className="muted">{count} cards</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      <section className="home-section">
        <h2 className="home-section-title">This graph</h2>
        <p className="home-stats">
          {stats.byType
            .map((entry) => `${entry.count.toLocaleString()} ${NODE_TYPE_NAME[entry.type]}s`)
            .join(' · ')}{' '}
          · {stats.edgeCount.toLocaleString()} edges
        </p>
        <p className="muted home-generated">
          {generatedAt ? `built ${generatedAt}` : 'build time unknown'}
          {stats.ageDays !== null && (
            <>
              {' · '}
              {stats.ageDays === 0 ? 'today' : `${stats.ageDays} day${stats.ageDays === 1 ? '' : 's'} old`}
              {stats.ageDays >= STALE_DAYS && (
                <span className="stale-hint"> — run `stitch build` to refresh</span>
              )}
            </>
          )}
        </p>
      </section>
    </main>
  )
}
