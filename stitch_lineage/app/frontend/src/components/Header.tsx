import { useStitch } from '../data'
import { buildStamp } from '../lib/coverage'
import { useRoute } from '../router'
import { useTheme } from '../theme'
import { copy } from '../copy'

/** Which nav entry owns the page you are on — the detail routes belong to Home. */
function currentNav(page: string): 'home' | 'erd' {
  return page === 'erd' ? 'erd' : 'home'
}

export function Header({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { meta, origin } = useStitch()
  const [theme, toggleTheme] = useTheme()
  const route = useRoute()
  const current = currentNav(route.page)
  // A stale graph is a wrong answer, so the build is on every page (principle 05).
  const built = buildStamp(meta.generated_at, new Date())

  return (
    <header className="app-header">
      {/* The wordmark stands alone: stitch is not a Snowflake or Metabase
          product, so their marks never appear in its own identity. They stay as
          badges ON their objects, which is nominative use (#66). */}
      <a className="app-brand" href="#/">
        stitch
      </a>
      {/* Existing routes only — the nav never offers a page that isn't there. */}
      <nav className="app-nav">
        <a className={current === 'home' ? 'current' : undefined} href="#/">
          {copy.header.home}
        </a>
        <a className={current === 'erd' ? 'current' : undefined} href="#/erd">
          {copy.header.erd}
        </a>
      </nav>
      <div className="app-header-right">
        <button
          type="button"
          className="header-action"
          onClick={onOpenPalette}
          title={copy.header.search}
        >
          ⌘K
        </button>
        <button
          type="button"
          className="header-action"
          onClick={toggleTheme}
          title={copy.header.themeToggle(theme === 'dark' ? 'light' : 'dark')}
        >
          {theme === 'dark' ? '☀' : '☾'}
        </button>
        {built && (
          <span
            className={`app-meta${built.stale ? ' stale' : ''}`}
            title={copy.header.builtAt(meta.generated_at, origin, built.stale)}
          >
            {built.text}
          </span>
        )}
      </div>
    </header>
  )
}
