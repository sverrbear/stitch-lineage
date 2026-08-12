// Running `stitch apply` from the app (#72): preview, confirm, results.
//
// This is the commit screen. Four states, in one order, no shortcuts:
//   planning  — asking the engine what it would write;
//   review    — the per-file diffs it planned, plus anything it cannot write;
//   applying  — the same guards as the CLI, running server-side;
//   refreshing— the writes landed; re-reading the graph they changed;
//   done      — what was written, and per-file refusals with the reason the engine
//               gave (a dirty file keeps itself out; its clean siblings still land).
//
// The phase is explicit rather than one `busy` flag because the reader is owed
// different words in each of them (#160): a single flag made the Apply button read
// "Applying…" while the dialog was still only planning, and left the results screen
// sitting behind a disabled Close for as long as the graph reload took, with nothing
// on screen admitting that anything was still running.
//
// There is deliberately no force: the CLI owns that, because overriding a
// dirty-file guard should cost a terminal.

import { useEffect, useState, type ReactNode } from 'react'
import {
  StagingError,
  applyStaged,
  previewApply,
  type ApplyOutcome,
  type ApplyPreview,
  type ApplyProblem,
} from '../lib/staging'
import {
  applyButtonLabel,
  applyStatus,
  isWorking,
  outcomeSummary,
  type ApplyPhase,
} from '../lib/workspace'
import { Spinner } from './bits'
import { DiffView } from './DiffView'
import { copy } from '../copy'

/** One line saying what is running, with the spinner beside it. */
function Working({ children }: { children: ReactNode }) {
  return (
    <p className="apply-status muted" role="status" aria-live="polite">
      <Spinner />
      {children}
    </p>
  )
}

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
  const [phase, setPhase] = useState<ApplyPhase>('planning')
  const [error, setError] = useState<string | null>(null)
  const busy = isWorking(phase)

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
        if (!cancelled) setPhase('review')
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
    setPhase('applying')
    setError(null)
    // a local, not the outcome state: this needs to know whether the WRITES got
    // through, and the setState above is not visible in this scope
    let written: ApplyOutcome | null = null
    try {
      written = await applyStaged()
      // the writes are done here; onApplied re-reads the graph, which is the long half
      setOutcome(written)
      setPhase('refreshing')
      await onApplied(written)
      setPhase('done')
    } catch (problem) {
      setError(problem instanceof StagingError ? problem.message : String(problem))
      // back to review when the apply itself failed; a refresh that failed still
      // wrote the files, so the results stand and only the canvas is out of date
      setPhase(written ? 'done' : 'review')
    }
  }

  const files = preview?.files ?? []
  const staged = preview?.staged
  const total = staged ? staged.relationships + staged.descriptions : 0
  const status = applyStatus(phase, files.length)

  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <div
        className="modal apply-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="apply-modal-title"
        aria-busy={busy}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="apply-modal-title">{outcome ? copy.apply.titleDone : copy.apply.title}</h2>

        {!outcome && (
          <>
            {phase === 'planning' && !preview && <Working>{status}</Working>}
            {preview && (
              <>
                <p className="apply-lead">
                  {copy.apply.lead(
                    total,
                    staged?.relationships ?? 0,
                    staged?.descriptions ?? 0,
                    files.length,
                    copy.apply.writeForm[preview.write_to as keyof typeof copy.apply.writeForm]?.() ?? (
                      <code>{preview.write_to}</code>
                    ),
                  )}
                </p>
                <div className="apply-scroll">
                  <DiffView files={files} />
                  <ProblemList title={copy.apply.unappliable} problems={preview.unappliable} />
                  <ProblemList title={copy.apply.unchanged} problems={preview.unchanged} />
                </div>
                {phase === 'applying' ? (
                  <Working>{status}</Working>
                ) : (
                  <p className="muted modal-note">{copy.apply.note}</p>
                )}
              </>
            )}
          </>
        )}

        {outcome && (
          <div className="apply-scroll">
            <p className="apply-lead">{outcomeSummary(outcome)}</p>
            {/* the writes have landed, but the canvas behind this dialog has not caught
                up yet and Close is still held — say so rather than looking stuck */}
            {phase === 'refreshing' && <Working>{status}</Working>}
            {outcome.written.length > 0 && (
              <section>
                <h3 className="subhead">{copy.apply.written}</h3>
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
                <h3 className="subhead">{copy.apply.refused(outcome.refused.length)}</h3>
                <ul className="apply-file-rows">
                  {outcome.refused.map((refusal) => (
                    <li key={refusal.path} className="apply-refused">
                      <span className="apply-warn">!</span>
                      <code>{refusal.path}</code>
                      <span className="muted apply-problem-reason">{refusal.reason}</span>
                    </li>
                  ))}
                </ul>
                <p className="muted">{copy.apply.refusedNote()}</p>
              </section>
            )}
            <ProblemList title={copy.apply.couldNotWrite} problems={outcome.unappliable} />
            {outcome.graph.patched && (
              <p className="muted">
                {copy.apply.graphPatched(
                  outcome.graph.edges_added ?? 0,
                  outcome.graph.descriptions_updated ?? 0,
                )}
              </p>
            )}
          </div>
        )}

        {error && <p className="modal-error">{error}</p>}

        <div className="modal-actions">
          <button type="button" className="ghost-button" onClick={onClose} disabled={busy}>
            {outcome ? copy.apply.close : copy.apply.cancel}
          </button>
          {!outcome && (
            <button
              type="button"
              className={phase === 'applying' ? 'button is-working' : 'button'}
              onClick={() => void run()}
              disabled={busy || !preview || (files.length === 0 && preview.unappliable.length === 0)}
              title={
                files.length === 0
                  ? copy.apply.nothingToWrite
                  : copy.apply.writeTitle
              }
            >
              {phase === 'applying' && <Spinner />}
              {applyButtonLabel(phase, files.length)}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
