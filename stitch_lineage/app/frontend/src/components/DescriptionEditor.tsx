// Click-to-edit description on a column or model panel (#70).
//
// The edit is STAGED, never written: it lands in `.stitch/staged_descriptions.yml`
// and only `stitch apply` puts it in the repo's `_schema.yml`. So the surface has
// to keep three states apart at a glance — what the repo says, what you have
// staged over it, and what is being typed — because the whole value of the staged
// model is that you can see the difference before committing to it.
//
// SPEC §12.2: description editing rides the existing staged store and earns no
// surface beyond this panel and the staged workspace. There is no bulk editor.

import { useEffect, useState } from 'react'
import { erdHref } from '../router'
import {
  StagingError,
  stageDescription,
  unstageDescription,
  type StagedDescription,
} from '../lib/staging'

export function DescriptionEditor({
  /** dbt model NAME (prefix and all) — what the staging API speaks. */
  entity,
  /** null edits the model's own description. */
  column,
  /** What the graph says today, i.e. what the repo says. */
  applied,
  /** The staged edit sitting over it, if any. */
  staged,
  /** Whether this build can stage at all (a static export cannot). */
  canStage,
  /**
   * Why `stitch apply` could never write this model's schema file, or null when it
   * can (#132). Known BEFORE anything is staged, so the affordance is withheld
   * rather than the edit taken and bounced at apply time.
   */
  refusal,
  onChanged,
}: {
  entity: string
  column: string | null
  applied: string | null | undefined
  staged: StagedDescription | null
  canStage: boolean
  refusal?: string | null
  onChanged: () => Promise<void> | void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const current = staged?.new_description ?? applied ?? ''
  useEffect(() => {
    if (!editing) setDraft(current)
  }, [current, editing])

  const open = () => {
    setError(null)
    setDraft(current)
    setEditing(true)
  }

  const save = async () => {
    const text = draft.trim()
    if (!text) {
      setError('A description cannot be empty — discard the edit instead.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await stageDescription({ entity, column, new_description: text })
      await onChanged()
      setEditing(false)
    } catch (problem) {
      setError(problem instanceof StagingError ? problem.message : String(problem))
    } finally {
      setBusy(false)
    }
  }

  const discard = async () => {
    if (!staged) return
    setBusy(true)
    setError(null)
    try {
      await unstageDescription(staged.id)
      await onChanged()
      setEditing(false)
    } catch (problem) {
      setError(problem instanceof StagingError ? problem.message : String(problem))
    } finally {
      setBusy(false)
    }
  }

  if (editing) {
    return (
      <div className="desc-editor">
        <textarea
          className="desc-input"
          value={draft}
          rows={Math.min(10, Math.max(3, draft.split('\n').length + 1))}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setEditing(false)
            // ⌘/Ctrl-Enter saves: a description is multi-line, so Enter cannot
            if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') void save()
          }}
          disabled={busy}
          autoFocus
          aria-label="Description"
        />
        <p className="muted desc-hint">
          Staged in <code>.stitch/</code> only — <code>stitch apply</code> writes it into the
          model’s <code>_schema.yml</code>. ⌘/Ctrl-Enter saves, Esc cancels.
        </p>
        {error && <p className="modal-error">{error}</p>}
        <div className="desc-actions">
          <button type="button" className="button" onClick={() => void save()} disabled={busy}>
            {busy ? 'Staging…' : 'Stage edit'}
          </button>
          <button
            type="button"
            className="ghost-button"
            onClick={() => setEditing(false)}
            disabled={busy}
          >
            Cancel
          </button>
          {staged && (
            <button
              type="button"
              className="ghost-button desc-discard"
              onClick={() => void discard()}
              disabled={busy}
              title="drop the staged edit and go back to what the repo says"
            >
              Discard staged edit
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className={`desc-shown${staged ? ' staged' : ''}`}>
      {current ? (
        <p className="panel-description desc-text">{current}</p>
      ) : (
        <p className="muted desc-text">No description.</p>
      )}
      <div className="desc-meta">
        {staged && (
          <>
            <span className="staged-badge" title="not in the repo until `stitch apply`">
              staged edit
            </span>
            <a className="desc-workspace-link" href={erdHref()}>
              in the staged workspace →
            </a>
            {applied ? (
              <details className="desc-was">
                <summary>what the repo says</summary>
                <p className="muted desc-text">{applied}</p>
              </details>
            ) : (
              <span className="muted">the repo has none yet</span>
            )}
          </>
        )}
        {canStage && !refusal && (
          <button type="button" className="ghost-button desc-edit" onClick={open}>
            {current ? 'Edit description' : 'Add a description'}
          </button>
        )}
        {canStage && refusal && (
          // Shown, not hidden: a missing button reads as a bug, and the reason is
          // something the reader can act on in their own repo.
          <span className="desc-unwritable" title={refusal}>
            not editable here
          </span>
        )}
      </div>
    </div>
  )
}
