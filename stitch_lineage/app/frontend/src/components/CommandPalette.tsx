// Cmd/Ctrl+K overlay wrapping the shared SearchPanel.

import { useEffect } from 'react'
import { SearchPanel } from './SearchPanel'
import { copy } from '../copy'

export function CommandPalette({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="palette-backdrop" onMouseDown={onClose} role="dialog" aria-modal="true">
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <SearchPanel autoFocus placeholder={copy.palette.placeholder} onNavigate={onClose} />
        <div className="palette-hint">{copy.palette.hint}</div>
      </div>
    </div>
  )
}
