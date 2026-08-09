// Confirm a relationship drawn on the ERD before staging it (#24).
//
// The modal is deliberately explicit about what happens next: drawing stages a
// declaration locally, and only `stitch apply` writes it into the dbt repo. The
// server's own 422 message is shown verbatim — it names the exact problem
// ("from column 'ghost_id' is not a column of model 'fct_orders'") far better
// than anything this component could infer.

import { useEffect, useState } from 'react'
import { CARDINALITIES, type Cardinality } from '../lib/staging'

export interface StageTarget {
  fromModel: string
  fromColumn: string
  toModel: string
  toColumn: string
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
  const [cardinality, setCardinality] = useState<Cardinality>('many-to-one')
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
        <h2 id="stage-modal-title">Stage a relationship</h2>
        <p className="modal-endpoints">
          <code>
            {target.fromModel}.{target.fromColumn}
          </code>
          <span className="rel-arrow"> → </span>
          <code>
            {target.toModel}.{target.toColumn}
          </code>
        </p>

        <label className="modal-field" htmlFor="stage-cardinality">
          Cardinality
        </label>
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

        {error && <p className="modal-error">{error}</p>}

        <div className="modal-actions">
          <button type="button" className="ghost-button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="button" onClick={submit} disabled={busy}>
            {busy ? 'Staging…' : 'Stage relationship'}
          </button>
        </div>
      </div>
    </div>
  )
}
