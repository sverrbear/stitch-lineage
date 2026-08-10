// Running `stitch apply` from the app (#72): preview, confirm, results.
//
// This is the commit screen. Three states, in one order, no shortcuts:
//   review  — the per-file diffs the engine planned, plus anything it cannot write;
//   applying— the same guards as the CLI, running server-side;
//   done    — what was written, and per-file refusals with the reason the engine
//             gave (a dirty file keeps itself out; its clean siblings still land).
//
// There is deliberately no force: the CLI owns that, because overriding a
// dirty-file guard should cost a terminal.

import { useEffect, useState } from 'react'
import {
  StagingError,
  applyStaged,
  previewApply,
  type ApplyOutcome,
  type ApplyPreview,
  type ApplyProblem,
} from '../lib/staging'
import { outcomeSummary } from '../lib/workspace'
import { DiffView } from './DiffView'

function ProblemList({ title, problems }: { title: string; problems: readonly ApplyProblem[] }) {
  if (problems.length === 0) return null
  return (
    <section className="apply-problems">
      <h3 className="subhead">
        {title} — {problems.length}
      </h3>
      <ul className="apply-problem-rows">
        {problems.map((problem, i) => (
          <li key={i} className="apply-problem">
            <span className="staged-kind">{problem.entry.kind ?? 'change'}</span>
            <code className="apply-problem-label">
              {String(problem.entry.label ?? problem.path ?? 'entry')}
            </code>
            <span className="muted apply-problem-reason">{problem.reason}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function ApplyDialog({
  onClose,
  onApplied,
}: {
  onClose: () => void
  /** Fired once, after a successful apply, so the caller can re-read everything. */
  onApplied: (outcome: ApplyOutcome) => Promise<void> | void
}) {
  const [preview, setPreview] = useState<ApplyPreview | null>(null)
  const [outcome, setOutcome] = useState<ApplyOutcome | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void previewApply()
      .then((result) => {
        if (!cancelled) setPreview(result)
      })
      .catch((problem: unknown) => {
        if (!cancelled) {
          setError(problem instanceof StagingError ? problem.message : String(problem))
        }
      })
      .finally(() => {
        if (!cancelled) setBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await applyStaged()
      setOutcome(result)
      await onApplied(result)
    } catch (problem) {
      setError(problem instanceof StagingError ? problem.message : String(problem))
    } finally {
      setBusy(false)
    }
  }

  const files = preview?.files ?? []
  const staged = preview?.staged
  const total = staged ? staged.relationships + staged.descriptions : 0

  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <div
        className="modal apply-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="apply-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="apply-modal-title">{outcome ? 'Applied' : 'Apply staged changes'}</h2>

        {!outcome && (
          <>
            {busy && !preview && <p className="muted">Planning the writes…</p>}
            {preview && (
              <>
                <p className="apply-lead">
                  {total} staged change{total === 1 ? '' : 's'} ({staged?.relationships ?? 0}{' '}
                  relationship{staged?.relationships === 1 ? '' : 's'},{' '}
                  {staged?.descriptions ?? 0} description edit
                  {staged?.descriptions === 1 ? '' : 's'}) →{' '}
                  {files.length} file{files.length === 1 ? '' : 's'}. Written as{' '}
                  <code>{preview.write_to}</code>.
                </p>
                <div className="apply-scroll">
                  <DiffView files={files} />
                  <ProblemList title="Cannot be written" problems={preview.unappliable} />
                  <ProblemList title="Already true in the repo" problems={preview.unchanged} />
                </div>
                <p className="muted modal-note">
                  This writes the files above in your dbt repo. A file with uncommitted changes is
                  refused and reported — forcing stays in the CLI.
                </p>
              </>
            )}
          </>
        )}

        {outcome && (
          <div className="apply-scroll">
            <p className="apply-lead">{outcomeSummary(outcome)}</p>
            {outcome.written.length > 0 && (
              <section>
                <h3 className="subhead">Written</h3>
                <ul className="apply-file-rows">
                  {outcome.written.map((path) => (
                    <li key={path} className="apply-written">
                      <span className="apply-ok">✓</span>
                      <code>{path}</code>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {outcome.refused.length > 0 && (
              <section>
                <h3 className="subhead">Refused — {outcome.refused.length}</h3>
                <ul className="apply-file-rows">
                  {outcome.refused.map((refusal) => (
                    <li key={refusal.path} className="apply-refused">
                      <span className="apply-warn">!</span>
                      <code>{refusal.path}</code>
                      <span className="muted apply-problem-reason">{refusal.reason}</span>
                    </li>
                  ))}
                </ul>
                <p className="muted">
                  Those changes are still staged. Commit or stash the file and apply again — or run{' '}
                  <code>stitch apply --force</code> in a terminal.
                </p>
              </section>
            )}
            <ProblemList title="Could not be written" problems={outcome.unappliable} />
            {outcome.graph.patched && (
              <p className="muted">
                Graph updated in place: {outcome.graph.edges_added ?? 0} relationship
                {outcome.graph.edges_added === 1 ? '' : 's'},{' '}
                {outcome.graph.descriptions_updated ?? 0} description
                {outcome.graph.descriptions_updated === 1 ? '' : 's'} — the canvas and the panels
                already show it.
              </p>
            )}
          </div>
        )}

        {error && <p className="modal-error">{error}</p>}

        <div className="modal-actions">
          <button type="button" className="ghost-button" onClick={onClose} disabled={busy}>
            {outcome ? 'Close' : 'Cancel'}
          </button>
          {!outcome && (
            <button
              type="button"
              className="button"
              onClick={() => void run()}
              disabled={busy || !preview || (files.length === 0 && preview.unappliable.length === 0)}
              title={
                files.length === 0
                  ? 'nothing to write — the repo already says all of this'
                  : 'write these files in the dbt repo'
              }
            >
              {busy ? 'Applying…' : `Apply ${files.length} file${files.length === 1 ? '' : 's'}`}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
