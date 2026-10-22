import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { CommandPalette } from './components/CommandPalette'
import { Header } from './components/Header'
import { DataProvider, useStitch } from './data'
import { CoveragePage } from './pages/CoveragePage'
import { HomePage } from './pages/HomePage'
import { ObjectWorkspace } from './pages/ObjectWorkspace'
import { useRoute } from './router'
import { copy } from './copy'

// The React Flow canvases are the heavy chunk; keep search/detail first-load light.
const StagedPage = lazy(() => import('./pages/StagedPage').then((m) => ({ default: m.StagedPage })))

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return (
    target.tagName === 'INPUT' ||
    target.tagName === 'TEXTAREA' ||
    target.tagName === 'SELECT' ||
    target.isContentEditable
  )
}

function Shell() {
  const route = useRoute()
  const [paletteOpen, setPaletteOpen] = useState(false)
  const homeSearchRef = useRef<HTMLInputElement | null>(null)
  const { origin } = useStitch()

  // Global keys: '/' focuses search (home) or opens the palette; Cmd/Ctrl+K palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((open) => !open)
        return
      }
      if (e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey && !isTypingTarget(e.target)) {
        e.preventDefault()
        if (route.page === 'home' && homeSearchRef.current) homeSearchRef.current.focus()
        else setPaletteOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [route.page])

  const selectedObject = 'nodeId' in route ? route.nodeId : route.page
  // Switching views keeps the reader on the same object. A different object starts at the top.
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [selectedObject])

  return (
    <div className="app">
      <Header onOpenPalette={() => setPaletteOpen(true)} />
      <Suspense fallback={<div className="app-loading">{copy.app.loadingView}</div>}>
        {route.page === 'home' && <HomePage searchInputRef={homeSearchRef} />}
        {route.page === 'not-found' && (
          <main className="detail-page">
            <article className="panel">
              <h2>{copy.app.invalidLinkTitle}</h2>
              <p className="muted">{copy.app.invalidLinkDetail}</p>
              <a className="button" href="#/">{copy.header.home}</a>
            </article>
          </main>
        )}
        {(route.page === 'node' || route.page === 'lineage') && (
          <ObjectWorkspace key={route.nodeId} route={route} />
        )}
        {route.page === 'coverage' && (
          <main className="detail-page">
            <CoveragePage kind={route.kind} />
          </main>
        )}
        {route.page === 'staged' && <StagedPage />}
      </Suspense>
      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} />}
      {origin === 'dev-fixture' && <div className="dev-banner">{copy.app.devFixtureBanner}</div>}
    </div>
  )
}

export function App() {
  return (
    <DataProvider
      fallback={({ loading, error }) =>
        loading ? (
          <div className="app-loading">{copy.app.loadingGraph}</div>
        ) : (
          <div className="app-error">
            <h1>{copy.app.loadErrorTitle}</h1>
            <p>
              <code>{error}</code>
            </p>
            <p>{copy.app.loadErrorDetail()}</p>
          </div>
        )
      }
    >
      <Shell />
    </DataProvider>
  )
}
