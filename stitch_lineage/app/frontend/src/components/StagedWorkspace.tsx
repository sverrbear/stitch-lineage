// The staged workspace (#70 / #72): everything pending, in one place, with the
// button that writes it.
//
// This grew out of the staged-relationships drawer (#61). The drawer answered
// "what have I drawn"; the workspace answers "what is about to change in my repo",
// which is a different question the moment a second change type exists. So it
// groups by type, gives every entry the same two verbs (edit, discard), and ends
// in Preview → Apply — the shape of a commit screen, because that is what the end
// of a modelling session is.

import type { StagedDescription, StagedRelationship } from '../lib/staging'
import { displayModelName } from '../lib/present'
import { descriptionLabel, descriptionPreview, type WorkspaceView } from '../lib/workspace'
import { copy } from '../copy'

export function StagedWorkspace({
  view,
  unresolvedIds,
  canApply,
  busy,
  notice,
  onClose,
  onEditRelationship,
  onDiscardRelationship,
  onEditDescription,
  onDiscardDescription,
  onApply,
}: {
  view: WorkspaceView
  /** Staged relationships whose models are not in this graph — cannot be drawn. */
  unresolvedIds: readonly string[]
  canApply: boolean
  busy: boolean
  notice: string | null
  onClose: () => void
  onEditRelationship: (entry: StagedRelationship) => void
  onDiscardRelationship: (id: string) => void
  onEditDescription: (entry: StagedDescription) => void
  onDiscardDescription: (id: string) => void
  onApply: () => void
}) {
  return (
    <aside className="staged-panel" aria-label={copy.staged.panelLabel}>
      <div className="staged-panel-head">
        <span className="staged-panel-title">{copy.staged.title(view.total)}</span>
        <button
          type="button"
          className="ghost-button"
          onClick={onClose}
          aria-label={copy.staged.hide}
        >
          ✕
        </button>
      </div>

      <div className="staged-panel-body">
        {view.total === 0 ? (
          <p className="muted staged-empty">{copy.staged.empty}</p>
        ) : (
          <>
            {view.relationships.length > 0 && (
              <section className="staged-section">
                <h3 className="staged-section-head">
                  {copy.staged.relationships}
                  <span className="muted">
                    {view.relationships.reduce((n, group) => n + group.entries.length, 0)}
                  </span>
                </h3>
                {view.relationships.map((group) => (
                  <section key={group.target} className="staged-group">
                    {/* the unit a reader scans for is "everything that joins to dim_users" */}
                    <h4 className="staged-group-head">
                      → {displayModelName(group.target)}{' '}
                      <span className="muted">({group.entries.length})</span>
                    </h4>
                    <ul className="staged-rows">
                      {group.entries.map((entry) => (
                        <li key={entry.id} className="staged-row">
                          <code
                            className="staged-pair"
                            title={`${entry.from_model}.${entry.from_column} → ${entry.to_model}.${entry.to_column}`}
                          >
                            {displayModelName(entry.from_model)}.{entry.from_column} →{' '}
                            {entry.to_column}
                          </code>
                          <span className="muted staged-cardinality">{entry.cardinality}</span>
                          {unresolvedIds.includes(entry.id) && (
                            <span className="muted" title={copy.staged.unresolvedTitle}>
                              {copy.staged.unresolved}
                            </span>
                          )}
                          <button
                            type="button"
                            className="ghost-button staged-edit"
                            onClick={() => onEditRelationship(entry)}
                            disabled={busy}
                            title={copy.staged.editRelationshipTitle}
                            aria-label={copy.staged.editRelationshipLabel(entry.from_model, entry.from_column)}
                          >
                            {copy.staged.edit}
                          </button>
                          <button
                            type="button"
                            className="ghost-button staged-remove"
                            onClick={() => onDiscardRelationship(entry.id)}
                            disabled={busy}
                            title={copy.staged.discardRelationshipTitle}
                            aria-label={copy.staged.discardRelationshipLabel(entry.from_model, entry.from_column)}
                          >
                            ✕
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </section>
            )}

            {view.descriptions.length > 0 && (
              <section className="staged-section">
                <h3 className="staged-section-head">
                  {copy.staged.descriptions}
                  <span className="muted">
                    {view.descriptions.reduce((n, group) => n + group.entries.length, 0)}
                  </span>
                </h3>
                {view.descriptions.map((group) => (
                  <section key={group.entity} className="staged-group">
                    <h4 className="staged-group-head">{displayModelName(group.entity)}</h4>
                    <ul className="staged-rows">
                      {group.entries.map((entry) => (
                        <li key={entry.id} className="staged-row staged-row-desc">
                          <code className="staged-pair" title={descriptionLabel(entry)}>
                            {entry.column ?? '(the table itself)'}
                          </code>
                          <span className="staged-desc-preview" title={entry.new_description}>
                            {descriptionPreview(entry)}
                          </span>
                          <button
                            type="button"
                            className="ghost-button staged-edit"
                            onClick={() => onEditDescription(entry)}
                            disabled={busy}
                            title={copy.staged.editDescriptionTitle}
                          >
                            {copy.staged.edit}
                          </button>
                          <button
                            type="button"
                            className="ghost-button staged-remove"
                            onClick={() => onDiscardDescription(entry.id)}
                            disabled={busy}
                            title={copy.staged.discardDescriptionTitle}
                            aria-label={copy.staged.discardDescriptionLabel(descriptionLabel(entry))}
                          >
                            ✕
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </section>
            )}
          </>
        )}
      </div>

      <div className="staged-panel-foot">
        {notice && <p className="staged-notice">{notice}</p>}
        {canApply ? (
          <>
            <button
              type="button"
              className="button staged-apply"
              onClick={onApply}
              disabled={busy || view.total === 0}
              title={
                view.total === 0
                  ? copy.staged.applyNothing
                  : copy.staged.applyTitle
              }
            >
              {copy.staged.apply}
            </button>
            <span className="muted">{copy.staged.applyReassurance}</span>
          </>
        ) : (
          <span className="muted">{copy.staged.cliOnly()}</span>
        )}
      </div>
    </aside>
  )
}
