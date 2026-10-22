import { useEffect, useState } from 'react'
import { displayModelName } from '../lib/present'
import { CARDINALITIES, cardinalitySentence, type Cardinality } from '../lib/staging'
import { copy } from '../copy'

export interface StageTarget {
  /**
   * Set when an ALREADY staged declaration is being revisited (#71): the same
   * decision, so the same modal, and the save is a PUT rather than a POST.
   */
  id: string
  fromModel: string
  fromColumn: string
  toModel: string
  toColumn: string
  cardinality?: string
}

export function StageRelationshipModal({
  target,
  onCancel,
  onConfirm,
}: {
  target: StageTarget
  onCancel: () => void
  onConfirm: (cardinality: Cardinality) => Promise<string | null>
}) {
  const initial = CARDINALITIES.find((value) => value === target.cardinality) ?? 'many-to-one'
  const [cardinality, setCardinality] = useState<Cardinality>(initial)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const submit = async () => {
    setBusy(true)
    setError(null)
    const message = await onConfirm(cardinality)
    setBusy(false)
    if (message) setError(message)
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="stage-modal-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="stage-modal-title">
          {copy.stage.titleEdit}
        </h2>
        <p className="modal-endpoints">
          <code>
            {displayModelName(target.fromModel)}.{target.fromColumn}
          </code>
          <span className="rel-arrow"> → </span>
          <code>
            {displayModelName(target.toModel)}.{target.toColumn}
          </code>
        </p>

        <label className="modal-field" htmlFor="stage-cardinality">
          {copy.stage.cardinality}
        </label>
        {/* the same declaration in words, live: "many-to-one" does not say which
            end is which, and this is where somebody catches a backwards FK (#73) */}
        <select
          id="stage-cardinality"
          className="scope-select"
          value={cardinality}
          onChange={(event) => setCardinality(event.target.value as Cardinality)}
          disabled={busy}
        >
          {CARDINALITIES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>

        <p className="muted modal-note">{copy.stage.note()}</p>

        <p className="modal-sentence" aria-live="polite">
          {cardinalitySentence({
            fromModel: displayModelName(target.fromModel),
            fromColumn: target.fromColumn,
            toModel: displayModelName(target.toModel),
            toColumn: target.toColumn,
            cardinality,
          })}
        </p>

        {error && <p className="modal-error">{error}</p>}

        <div className="modal-actions">
          <button type="button" className="ghost-button" onClick={onCancel} disabled={busy}>
            {copy.stage.cancel}
          </button>
          <button type="button" className="button" onClick={submit} disabled={busy}>
            {busy ? copy.stage.saving : copy.stage.saveEdit}
          </button>
        </div>
      </div>
    </div>
  )
}
