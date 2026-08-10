// Confirm a relationship drawn on the ERD before staging it (#24).
//
// The modal is deliberately explicit about what happens next: drawing stages a
// declaration locally, and only `stitch apply` writes it into the dbt repo. The
// server's own 422 message is shown verbatim — it names the exact problem
// ("from column 'ghost_id' is not a column of model 'fct_orders'") far better
// than anything this component could infer.

import { useEffect, useState } from 'react'
import { displayModelName } from '../lib/present'
import { CARDINALITIES, cardinalitySentence, type Cardinality } from '../lib/staging'

export interface StageTarget {
  /**
   * Set when an ALREADY staged declaration is being revisited (#71): the same
   * decision, so the same modal, and the save is a PUT rather than a POST.
   */
  id?: string
  fromModel: string
  fromColumn: string
  toModel: string
  toColumn: string
  /** Prefilled when the pair came from a suggestion rather than a drag. */
  cardinality?: string
  /** Why stitch proposed it — shown so accepting is a judgement, not a reflex. */
  provenance?: string
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
          {target.id
            ? 'Edit this staged relationship'
            : target.provenance
              ? 'Accept this relationship'
              : 'Stage a relationship'}
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

        {target.provenance && <p className="modal-provenance">{target.provenance}</p>}

        <label className="modal-field" htmlFor="stage-cardinality">
          Cardinality
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

        <p className="muted modal-note">
          This stages the declaration in <code>.stitch/</code> only. Run{' '}
          <code>stitch apply</code> to write it into the model’s <code>_schema.yml</code> — nothing
          touches the repo before that.
        </p>

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
            Cancel
          </button>
          <button type="button" className="button" onClick={submit} disabled={busy}>
            {busy
              ? target.id
                ? 'Saving…'
                : 'Staging…'
              : target.id
                ? 'Save edit'
                : 'Stage relationship'}
          </button>
        </div>
      </div>
    </div>
  )
}
