import { useStitch } from '../data'
import { useTheme } from '../theme'
import { MetabaseMark, SnowflakeMark } from './badges'

export function Header({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { meta, origin } = useStitch()
  const [theme, toggleTheme] = useTheme()

  return (
    <header className="app-header">
      <a className="app-brand" href="#/">
        <span className="app-brand-marks">
          <SnowflakeMark size={16} />
          <MetabaseMark size={16} />
        </span>
        stitch
      </a>
      <nav className="app-nav">
        <a href="#/">Search</a>
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
