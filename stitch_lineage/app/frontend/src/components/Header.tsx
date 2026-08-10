import { useStitch } from '../data'
import { useTheme } from '../theme'

export function Header({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { meta, origin } = useStitch()
  const [theme, toggleTheme] = useTheme()

  return (
    <header className="app-header">
      {/* The wordmark stands alone: stitch is not a Snowflake or Metabase
          product, so their marks never appear in its own identity. They stay as
          badges ON their objects, which is nominative use (#66). */}
      <a className="app-brand" href="#/">
        stitch
      </a>
      {/* the ERD is the canvas — the global pipeline map is gone (#83) */}
      <nav className="app-nav">
        <a href="#/">Home</a>
        <a href="#/erd">ERD</a>
      </nav>
      <div className="app-header-right">
        <button type="button" className="ghost-button" onClick={onOpenPalette} title="Search (Cmd/Ctrl+K)">
          ⌘K
        </button>
        <button
          type="button"
          className="ghost-button"
          onClick={toggleTheme}
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        >
          {theme === 'dark' ? '☀' : '☾'}
        </button>
        <span className="app-meta" title={`data source: ${origin}`}>
          {meta.generated_at ? `graph @ ${meta.generated_at.slice(0, 16).replace('T', ' ')}` : ''}
        </span>
      </div>
    </header>
  )
}
