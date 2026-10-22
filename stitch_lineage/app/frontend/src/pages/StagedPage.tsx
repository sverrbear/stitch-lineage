import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApplyDialog } from '../components/ApplyDialog'
import { StageRelationshipModal, type StageTarget } from '../components/StageRelationshipModal'
import { StagedWorkspace } from '../components/StagedWorkspace'
import { useStitch } from '../data'
import { copy } from '../copy'
import { fullName } from '../lib/present'
import {
  editRelationship, listStaged, listStagedDescriptions, probeApply, probeStaging,
  unstageDescription, unstageRelationship,
  type StagedDescription, type StagedRelationship,
} from '../lib/staging'
import { workspaceView } from '../lib/workspace'
import { navigate, nodeHref } from '../router'

/** Review existing staged changes without requiring a relationship canvas. */
export function StagedPage() {
  const { index, meta, reload } = useStitch()
  const [relationships, setRelationships] = useState<StagedRelationship[]>([])
  const [descriptions, setDescriptions] = useState<StagedDescription[]>([])
  const [loading, setLoading] = useState(true)
  const [canStage, setCanStage] = useState(false)
  const [canApply, setCanApply] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [applyOpen, setApplyOpen] = useState(false)
  const [draft, setDraft] = useState<StageTarget | null>(null)
  const refresh = useCallback(async () => {
    const [nextRelationships, nextDescriptions] = await Promise.all([listStaged(), listStagedDescriptions()])
    setRelationships(nextRelationships)
    setDescriptions(nextDescriptions)
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const enabled = await probeStaging(meta.staging_enabled)
        if (cancelled) return
        setCanStage(enabled)
        if (enabled) {
          const [nextRelationships, nextDescriptions, apply] = await Promise.all([
            listStaged(), listStagedDescriptions(), probeApply(meta.apply_enabled),
          ])
          if (cancelled) return
          setRelationships(nextRelationships)
          setDescriptions(nextDescriptions)
          setCanApply(apply)
        }
      } catch (error) {
        if (!cancelled) setNotice(String(error))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [meta.staging_enabled, meta.apply_enabled])

  const view = useMemo(() => workspaceView(relationships, descriptions), [relationships, descriptions])
  const discard = async (id: string, kind: 'relationship' | 'description') => {
    setBusy(true)
    setNotice(null)
    try {
      await (kind === 'relationship' ? unstageRelationship(id) : unstageDescription(id))
      await refresh()
    } catch (error) {
      setNotice(String(error))
    } finally {
      setBusy(false)
    }
  }
  const openDescription = (entry: StagedDescription) => {
    const model = index.nodes.find((node) =>
      (node.node_type === 'model' || node.node_type === 'source') &&
      fullName(node).toLowerCase() === entry.entity.toLowerCase(),
    )
    const target = entry.column && model
      ? index.nodes.find((node) => node.node_type === 'column' &&
        node.node_id.startsWith(`${model.node_id}::`) &&
        (node.column ?? node.name).toLowerCase() === entry.column!.toLowerCase())
      : model
    if (!target) {
      setNotice(`${entry.entity}${entry.column ? `.${entry.column}` : ''} is not in this graph — open it after the next build`)
      return
    }
    navigate(nodeHref(target.node_id))
  }

  return (
    <main className="detail-page staged-page">
      {loading ? <p className="muted">{copy.app.loadingView}</p> : !canStage ? (
        <article className="panel"><h1>{copy.staged.pageTitle}</h1><p>{copy.staged.readOnly}</p><a href="#/">{copy.header.home}</a></article>
      ) : (
        <StagedWorkspace view={view} canApply={canApply} busy={busy || applyOpen || draft !== null}
          notice={notice} onClose={() => navigate('#/')}
          onEditRelationship={(entry) => setDraft({ id: entry.id, fromModel: entry.from_model,
            fromColumn: entry.from_column, toModel: entry.to_model, toColumn: entry.to_column,
            cardinality: entry.cardinality })}
          onDiscardRelationship={(id) => { void discard(id, 'relationship') }}
          onEditDescription={openDescription}
          onDiscardDescription={(id) => { void discard(id, 'description') }}
          onApply={() => setApplyOpen(true)} />
      )}
      {draft && <StageRelationshipModal target={draft} onCancel={() => setDraft(null)}
        onConfirm={async (cardinality) => {
          try {
            await editRelationship(draft.id, { from_model: draft.fromModel, from_column: draft.fromColumn,
              to_model: draft.toModel, to_column: draft.toColumn, cardinality })
            await refresh()
            setDraft(null)
            return null
          } catch (error) { return String(error) }
        }} />}
      {applyOpen && <ApplyDialog onClose={() => setApplyOpen(false)} onApplied={async () => {
        await reload()
        await refresh()
      }} />}
    </main>
  )
}
