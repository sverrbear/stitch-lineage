// The 7a home (#108): one 620px column, vertically centred — the question, the
// field that answers it, three real identifiers to start from, the two canvases,
// and what this build does NOT know. Nothing else. A lineage tool is opened in
// the middle of a problem, so the home page is a way in, not a dashboard.
//
// '/' and ⌘K are untouched — the search box is still the first thing focused.

import { useMemo, type RefObject } from 'react'
import { SearchPanel } from '../components/SearchPanel'
import { useStitch } from '../data'
import {
  coveragePercent,
  coverageRows,
  homeExamples,
  startingPoints,
  type CoverageRow,
} from '../lib/coverage'
import { coverageHref, erdHref, lineageHref, nodeHref } from '../router'

function Row({ row }: { row: CoverageRow }) {
  const value = (
    <span className="coverage-row-value">
      {row.value.toLocaleString()}
      {row.total !== null && ` / ${row.total.toLocaleString()}`}
    </span>
  )
  const body = (
    <>
      <span className="coverage-row-main">
        <span className="coverage-row-label">{row.label}</span>
        {/* the caveat travels with the number, not in a footnote nobody reads */}
        {row.note && (
          <span className="coverage-row-note" title={row.noteHint ?? undefined}>
            {row.note}
          </span>
        )}
      </span>
      <span className="coverage-row-right">
        {/* the gap sits beside the number it qualifies, never on its own page */}
        {row.gapLabel && <span className="coverage-row-gap">{row.gapLabel}</span>}
        {value}
      </span>
    </>
  )
  // Only a row with something missing is a link — it leads to what is missing.
  return row.list ? (
    <a className="coverage-row" href={coverageHref(row.list)} title={row.hint}>
      {body}
    </a>
  ) : (
    <div className="coverage-row" title={row.hint}>
      {body}
    </div>
  )
}

export function HomePage({ searchInputRef }: { searchInputRef: RefObject<HTMLInputElement | null> }) {
  const { index } = useStitch()
  const rows = useMemo(() => coverageRows(index.graph.coverage), [index])
  const percent = useMemo(() => coveragePercent(index.graph.coverage), [index])
  const examples = useMemo(() => homeExamples(index), [index])
  // the lineage entry needs somewhere to start: the model the BI layer leans on most
  const busiest = useMemo(() => startingPoints(index, 1).mostConsumedModels[0] ?? null, [index])

  return (
    <main className="home">
      <div className="home-column">
        <div className="home-hero">
          <h1 className="home-title">Trace a column</h1>
          <SearchPanel
            inputRef={searchInputRef}
            autoFocus
            hero
            placeholder="Model, column, card or dashboard"
          />
          {examples.length > 0 && (
            <div className="home-examples">
              {examples.map((example, i) => (
                <span key={example.node.node_id} className="home-example-slot">
                  {i > 0 && <span className="home-example-sep">·</span>}
                  <a
                    className="home-example"
                    href={nodeHref(example.node.node_id)}
                    title={example.node.node_id}
                  >
                    {example.label}
                  </a>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* The two canvases stitch keeps, as rows rather than cards: a link is a
            link. The global pipeline map is gone (#83). */}
        <div className="home-links">
          <a
            className="home-link"
            href={busiest ? lineageHref(busiest.node.node_id) : coverageHref('unbound-models')}
          >
            <span>Lineage</span>
            <span className="home-link-chevron" aria-hidden="true">
              ›
            </span>
          </a>
          <a className="home-link" href={erdHref()}>
            <span>ERD</span>
            <span className="home-link-chevron" aria-hidden="true">
              ›
            </span>
          </a>
        </div>

        <div className="coverage-block">
          <div className="coverage-head">
            <span>Coverage</span>
            {percent !== null && <span className="coverage-percent">{percent}%</span>}
          </div>
          {percent !== null && (
            <div className="coverage-bar" aria-hidden="true">
              <div className="coverage-bar-fill" style={{ width: `${percent}%` }} />
            </div>
          )}
          <div className="coverage-rows">
            {rows.map((row) => (
              <Row key={row.key} row={row} />
            ))}
          </div>
        </div>
      </div>
    </main>
  )
}
