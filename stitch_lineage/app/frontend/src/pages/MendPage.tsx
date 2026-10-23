import { useEffect, useMemo, useRef, useState } from 'react'
import { DiffView } from '../components/DiffView'
import { copy } from '../copy'
import { useStitch } from '../data'
import {
  applyMend, columnOptions, loadMend, mendResult, MendRequestError, outcomeLabel, previewMend, selectedRenames, snapshotTime,
  type MendAction, type MendCard, type MendContext, type MendExplanation, type MendPreview, type MendResult,
} from '../lib/mend'

const text = copy.mend
const message = (error: unknown) => error instanceof Error ? error.message : String(error)

function ReportLink({ id, url }: { id: number; url: string | null }) {
  if (!url || !/^https?:\/\//i.test(url)) return null
  return <a href={`${url.replace(/\/$/, '')}/question/${id}`} target="_blank" rel="noreferrer">{text.openReport}</a>
}

export function MendReport({ card, diff, review, url }: { card: MendCard; diff?: string; review?: MendExplanation; url: string | null }) {
  return <article className={`mend-report mend-action-${card.action}`}>
    <header className="mend-report-head">
      <div><span className="mend-action">{text.actions[card.action]}</span><h3>{card.name}</h3></div>
      <ReportLink id={card.card_id} url={url} />
    </header>
    {card.collection && <p className="muted">{card.collection}</p>}
    {card.dead_refs.filter((ref) => ref.rename_to).map((ref) =>
      <p className="mend-rename" key={ref.column}><code>{ref.column}</code><span>→</span><code>{ref.rename_to}</code></p>)}
    <p>{card.reason}</p>
    {review && review.changes.length > 0 && <section className="mend-changes" aria-label={text.changes}>
      <h4>{text.changes}</h4>
      {review.changes.map((change, i) => <div className="mend-change" key={`${change.label}-${i}`}>
        <h5>{change.label}</h5>
        <div className="mend-change-pair">
          <div><span>{text.before}</span><pre>{change.before}</pre></div>
          <div><span>{text.after}</span><pre>{change.after}</pre></div>
        </div>
        {!change.plain && <p className="muted">{text.customExpression}</p>}
      </div>)}
    </section>}
    {card.action === 'strip' && <p className="mend-warning">{text.stripWarning}</p>}
    {card.action === 'archive' && <p className="mend-warning">{text.archiveWarning}</p>}
    {card.removed_clauses.length > 0 && <ul>{card.removed_clauses.map((clause) => <li key={clause}>{clause}</li>)}</ul>}
    {card.dashcards.map((edit) => {
      const named = review?.dashboards.find((item) => item.dashboard_id === edit.dashboard_id && item.dashcard_id === edit.dashcard_id)
      return <section key={`${edit.dashboard_id}-${edit.dashcard_id}`} className="mend-dashboard">
      <p>{text.dashboardEdit(edit.dashboard_name)}</p>
      <details><summary>{text.dashboardDiff}</summary><div className="mend-json-pair">
        <div><h4>{text.before}</h4><pre>{JSON.stringify(named?.before ?? edit.before, null, 2)}</pre></div>
        <div><h4>{text.after}</h4><pre>{JSON.stringify(named?.after ?? edit.after, null, 2)}</pre></div>
      </div>
      {named && <details><summary>{text.rawDashboard}</summary><div className="mend-json-pair">
        <div><h4>{text.before}</h4><pre>{JSON.stringify(edit.before, null, 2)}</pre></div>
        <div><h4>{text.after}</h4><pre>{JSON.stringify(edit.after, null, 2)}</pre></div>
      </div></details>}
      </details>
    </section>})}
    {review && <details><summary>{text.namedDiff}</summary><p className="muted">{text.namedDiffNote}</p>
      <DiffView files={[{ path: card.name, diff: review.query_diff }]} />
    </details>}
    {diff ? <details><summary>{review ? text.rawDiff : text.queryDiff}</summary>
      <DiffView files={[{ path: card.name, diff }]} />
    </details> : card.before && <details><summary>{text.queryBefore}</summary><pre>{JSON.stringify(card.before, null, 2)}</pre></details>}
  </article>
}

export function MendResults({ result, url }: { result: MendResult; url: string | null }) {
  const cards = result.outcome.cards
  const good = cards.filter((c) => c.status === 'applied' || c.status === 'archived').length
  const manual = cards.filter((c) => c.status === 'notify').length
  return <section className="mend-results" aria-live="polite">
    <h2>{text.results}</h2><p className="mend-summary">{text.resultSummary(good, cards.length - good - manual, manual)}</p>
    <p className="muted">{text.validationNote}</p>
    {[...cards].sort((a, b) => Number(b.status === 'failed') - Number(a.status === 'failed')).map((card) =>
      <article className={`mend-report mend-result-${card.status}`} key={card.card_id}>
        <header className="mend-report-head"><div><span className="mend-action">{outcomeLabel(card)}</span><h3>{card.name}</h3></div>
          <ReportLink id={card.card_id} url={url} /></header>
        {card.detail && <p>{card.detail}</p>}
        {card.dashcards_written > 0 && <p>{text.dashboardWrites(card.dashcards_written)}</p>}
        {result.recovery?.[String(card.card_id)] && <section className="mend-recovery">
          <details><summary>{text.recoveryQuery}</summary>
            <p>{text.recoveryNote}</p>
            <pre>{JSON.stringify(result.recovery[String(card.card_id)].before, null, 2)}</pre>
          </details>
          <details><summary>{text.recoveryAfter}</summary>
            <pre>{JSON.stringify(result.recovery[String(card.card_id)].after, null, 2)}</pre>
          </details>
          {result.recovery[String(card.card_id)].dashcards.map((edit) => <details key={`${edit.dashboard_id}-${edit.dashcard_id}`}>
            <summary>{text.recoveryDashboard(edit.dashboard_name)}</summary>
            <pre>{JSON.stringify(edit.before, null, 2)}</pre>
          </details>)}
        </section>}
      </article>)}
    <p className="muted">{text.resultRefresh}</p>
  </section>
}

export function MendPage() {
  const { meta, origin } = useStitch()
  const enabled = origin === 'api' && meta.mend_enabled === true
  const [context, setContext] = useState<MendContext | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [extra, setExtra] = useState<MendAction[]>([])
  const [preview, setPreview] = useState<MendPreview | null>(null)
  const [result, setResult] = useState<MendResult | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [phase, setPhase] = useState<'idle' | 'planning' | 'applying'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [interruptedId, setInterruptedId] = useState<string | null>(null)
  const inFlight = useRef(false)
  const reviewRef = useRef<HTMLDivElement>(null)
  const resultRef = useRef<HTMLDivElement>(null)
  const options = useMemo(() => columnOptions(context?.replacements ?? []), [context])

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    void loadMend().then((data) => { if (!cancelled) setContext(data) })
      .catch((err) => { if (!cancelled) setError(message(err)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [enabled])

  useEffect(() => {
    const target = result ? resultRef.current : preview ? reviewRef.current : null
    target?.focus()
    target?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [preview, result])

  const invalidate = () => {
    if (preview) setNotice(text.changedInputs)
    setPreview(null); setConfirmed(false); setError(null)
  }
  const reload = async () => {
    if (inFlight.current) return
    inFlight.current = true; invalidate(); setLoading(true)
    try { setContext(await loadMend()); setDrafts({}); setExtra([]); setNotice(null) }
    catch (err) { setError(message(err)) }
    finally { setLoading(false); inFlight.current = false }
  }
  const selected = selectedRenames(drafts, context?.replacements ?? [])
  const busy = phase !== 'idle' || loading
  const review = async () => {
    if (inFlight.current || selected.invalid || !context) return
    inFlight.current = true; invalidate(); setNotice(null); setPhase('planning'); setResult(null); setInterruptedId(null)
    try {
      const actions: MendAction[] = [...(context.allowed_actions.includes('repoint') ? ['repoint' as const] : []), ...extra]
      setPreview(await previewMend(selected.renames, actions))
    } catch (err) { setError(message(err)) }
    finally { inFlight.current = false; setPhase('idle') }
  }
  const apply = async () => {
    if (inFlight.current || !confirmed || !preview) return
    inFlight.current = true
    const id = preview.preview_id
    setPhase('applying'); setError(null); setConfirmed(false)
    try { setResult(await applyMend(id)) }
    catch (err) {
      if (err instanceof MendRequestError && err.status < 500) setError(message(err))
      else {
        try { setResult(await mendResult(id)) }
        catch { setError(`${message(err)} ${text.interrupted}`); setInterruptedId(id) }
      }
    } finally { setPreview(null); setPhase('idle'); inFlight.current = false }
  }

  return <main className="detail-page mend-page">
    <header className="mend-heading"><p className="eyebrow">{text.title}</p><h1>{text.headline}</h1><p>{text.intro}</p></header>
    {!enabled ? <article className="panel"><p>{text.readOnly}</p></article> : <>
      {error && <p className="mend-error" role="alert">{error}</p>}
      {notice && <p className="muted" role="status">{notice}</p>}
      {loading && <p role="status">{text.loading}</p>}
      {!context && !loading && <button className="button" onClick={() => { void reload() }}>{text.reload}</button>}
      {context && <>
        <section className="mend-snapshots" aria-label={text.snapshots}>
          <div><span>{text.baseline}</span><strong>{snapshotTime(context.baseline?.generated_at ?? null)}</strong><code>{context.baseline?.file ?? '—'}</code></div>
          <span className="mend-snapshot-arrow">→</span>
          <div><span>{text.current}</span><strong>{snapshotTime(context.current.generated_at)}</strong><code>{context.current.file}</code></div>
          <button className="button" onClick={() => { void reload() }} disabled={busy}>{text.reload}</button>
        </section>
        <details className="mend-context"><summary>{text.snapshots}</summary><p>{text.freshness}</p><p>{text.coverage}</p></details>
        {context.state === 'missing-baseline' ? <article className="panel"><h2>{text.missingTitle}</h2><p>{text.missing}</p></article>
          : context.state === 'no-removed-columns' ? <article className="panel"><h2>{text.noRemoved}</h2><p>{text.noRemovedDetail}</p>
            {context.type_changes > 0 && <p>{text.typeChanges(context.type_changes)}</p>}</article>
          : <form onSubmit={(event) => { event.preventDefault(); void review() }}>
            <fieldset disabled={busy} className="mend-fields">
              {context.type_changes > 0 && <p className="mend-warning">{text.typeChanges(context.type_changes)}</p>}
              <div className="mend-mapping-head"><span>{text.changed}</span><span>{text.replacement}</span></div>
              {context.removed.map((old, i) => {
                const value = drafts[old.id] ?? ''
                const target = context.replacements.find((n) => n.id === selected.renames[old.id])
                const suggestions = options.filter((n) => `${n.label} ${n.id}`.toLowerCase().includes(value.toLowerCase())).slice(0, 80)
                return <div className="mend-mapping" key={old.id}>
                  <code>{old.label}</code>
                  <div><input aria-label={text.replacementFor(old.label)} list={`replacement-${i}`} value={value}
                    placeholder={text.replacementPlaceholder} autoComplete="off"
                    onChange={(event) => { invalidate(); setDrafts({ ...drafts, [old.id]: event.target.value }) }} />
                    <datalist id={`replacement-${i}`}>{suggestions.map((n) => <option key={n.id} value={n.value}>{n.label} · {n.id}</option>)}</datalist>
                    {target && !target.bound && <p className="mend-warning">{text.unsynced}</p>}
                  </div>
                </div>
              })}
              <p className="muted">{text.replacementHint}</p>
              {selected.invalid && <p className="mend-warning">{text.invalidTarget}</p>}
              <p className="muted">{text.repointOnly}</p>
              {!context.allowed_actions.includes('repoint') && <p className="mend-warning">{text.repointDisabled}</p>}
              <details className="mend-options"><summary>{text.options}</summary>
                {(['strip', 'archive'] as const).map((action) => <div key={action}>
                  <label><input type="checkbox" checked={extra.includes(action)} disabled={!context.allowed_actions.includes(action)}
                    onChange={(event) => { invalidate(); setExtra(event.target.checked ? [...extra, action] : extra.filter((a) => a !== action)) }} />{text[action]}</label>
                  <p className="muted">{text[`${action}Warning`]}</p>
                  {!context.allowed_actions.includes(action) && <p className="muted">{text.disabledAction}</p>}
                </div>)}
              </details>
              <button className="button mend-primary" type="submit" disabled={selected.invalid}>{text.review}</button>
            </fieldset>
          </form>}
      </>}
      {phase !== 'idle' && <p className="mend-progress" role="status">{phase === 'planning' ? text.planning : text.applying}</p>}
      {preview && <div className="mend-review" ref={reviewRef} tabIndex={-1}>
        <h2>{text.previewTitle(preview.plan.cards.length)}</h2><p className="muted">{text.previewNote}</p>
        {preview.plan.unresolved_renames.map((warning) => <p className="mend-warning" key={warning}>{warning}</p>)}
        {preview.plan.cards.length === 0 && <p>{text.nothing}</p>}
        {preview.writing_count > 0 && <section><h3 className="mend-group-title">{text.ready(preview.writing_count)}</h3>
          {preview.plan.cards.filter((c) => c.action !== 'notify').map((card) => <MendReport key={card.card_id} card={card} diff={preview.diffs[String(card.card_id)]} review={preview.reviews?.[String(card.card_id)]} url={meta.metabase_url} />)}
        </section>}
        {preview.writing_count > 0 && <section className="mend-approval">
          <p>{text.validationNote}</p>
          {preview.plan.cards.length > preview.writing_count && <p>{text.manualUnchanged(preview.plan.cards.length - preview.writing_count)}</p>}
          <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} />{text.confirm(preview.writing_count)}</label>
          <button className="button mend-primary" disabled={!confirmed || busy} onClick={() => { void apply() }}>{text.apply(preview.writing_count)}</button>
        </section>}
        {preview.plan.cards.some((c) => c.action === 'notify') && <section><h3 className="mend-group-title">{text.manual(preview.plan.cards.filter((c) => c.action === 'notify').length)}</h3>
          {preview.plan.cards.filter((c) => c.action === 'notify').map((card) => <MendReport key={card.card_id} card={card} url={meta.metabase_url} />)}
        </section>}
      </div>}
      {interruptedId && <button className="button" disabled={busy} onClick={() => {
        void mendResult(interruptedId).then((data) => { setResult(data); setError(null); setInterruptedId(null) }).catch((err) => setError(message(err)))
      }}>{text.checkResult}</button>}
      {result && <div ref={resultRef} tabIndex={-1}><MendResults result={result} url={meta.metabase_url} /></div>}
    </>}
  </main>
}
