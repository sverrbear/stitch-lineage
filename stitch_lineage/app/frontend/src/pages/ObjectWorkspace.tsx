import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { NodeIdentity, ObjectWorkspaceContext } from '../components/NodeIdentity'
import { copy } from '../copy'
import { useStitch } from '../data'
import { relationshipScope, relationshipTable } from '../lib/objectWorkspace'
import type { Grain } from '../lib/rollup'
import { lineageHref, nodeHref, relationshipsHref, type ObjectRoute, type RelationshipView } from '../router'
import { NodePage } from './NodePage'

// Object identity and summary stay light; only the selected canvas loads its chunk.
const LineagePage = lazy(() => import('./LineagePage').then((m) => ({ default: m.LineagePage })))
const ErdPage = lazy(() => import('./ErdPage').then((m) => ({ default: m.ErdPage })))

export function ObjectWorkspace({ route }: { route: ObjectRoute }) {
  const { index } = useStitch()
  const node = index.nodesById.get(route.nodeId)
  const table = node ? relationshipTable(index, node) : null
  const initialScope = useMemo(() => table ? relationshipScope(index, table) : null, [index, table])
  const [summaryVisited, setSummaryVisited] = useState(route.page === 'node')
  const summaryRef = useRef<HTMLDivElement | null>(null)
  const summaryScroll = useRef(0)
  useLayoutEffect(() => {
    if (route.page === 'node' && summaryRef.current) summaryRef.current.scrollTop = summaryScroll.current
  }, [route.page])
  const [lastGrain, setLastGrain] = useState<Grain>('column')
  const [lastRelationships, setLastRelationships] = useState<RelationshipView>({})
  useEffect(() => {
    if (route.page === 'node') setSummaryVisited(true)
    if (route.page === 'lineage') setLastGrain(route.grain)
    if (route.page === 'relationships') {
      setLastRelationships({ scopeKind: route.scopeKind, scopeValue: route.scopeValue, focusId: route.focusId })
    }
  }, [route])

  if (!node) return <main className="detail-page"><NodePage nodeId={route.nodeId} /></main>
  const relationshipView = route.page === 'relationships' ? route : lastRelationships
  const scopeKind = relationshipView.scopeKind ?? initialScope?.kind
  const scopeValue = relationshipView.scopeValue ?? initialScope?.value
  const focusId = relationshipView.focusId === undefined ? table?.node_id ?? null : relationshipView.focusId
  const active = route.page === 'node' ? 'summary' : route.page
  const links = [
    { view: 'summary', label: copy.workspace.summary, href: nodeHref(node.node_id) },
    { view: 'lineage', label: copy.workspace.lineage, href: lineageHref(node.node_id, route.page === 'lineage' ? route.grain : lastGrain) },
    ...(table ? [{ view: 'relationships', label: copy.workspace.relationships, href: relationshipsHref(node.node_id, lastRelationships) }] : []),
  ]
  return (
    <main className={`object-workspace${active === 'summary' ? ' object-summary' : ' object-graph'}`}>
      <header className="object-header">
        <NodeIdentity node={node} />
        <nav className="object-views" aria-label={copy.workspace.views}>
          {links.map((link) => <a key={link.view} href={link.href} aria-current={active === link.view ? 'page' : undefined}>{link.label}</a>)}
        </nav>
      </header>
      <Suspense fallback={<div className="app-loading">{copy.app.loadingView}</div>}>
        {(route.page === 'node' || summaryVisited) && (
          <div className="object-content" hidden={route.page !== 'node'} ref={summaryRef}
            onScroll={(event) => { if (route.page === 'node') summaryScroll.current = event.currentTarget.scrollTop }}>
            <ObjectWorkspaceContext value={{ nodeId: node.node_id, summaryActive: route.page === 'node' }}><NodePage nodeId={node.node_id} /></ObjectWorkspaceContext>
          </div>
        )}
        {route.page === 'lineage' && <LineagePage nodeId={node.node_id} grain={route.grain} embedded />}
        {route.page === 'relationships' && (
          table && scopeKind && scopeValue
            ? <ErdPage scopeKind={scopeKind} scopeValue={scopeValue} workspaceNodeId={node.node_id} focusId={focusId} />
            : <div className="object-content"><p className="muted">{table ? copy.workspace.noRelationshipScope : copy.workspace.noRelationships}</p></div>
        )}
      </Suspense>
    </main>
  )
}
