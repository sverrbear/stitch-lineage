// The gap behind a coverage tile, in the app (#48). These lists used to exist
// only behind `stitch doctor --unbound` / `--untraced`, which is the wrong place
// for them: the thing you want after reading "109/239 bound" is the other 130,
// clickable.

import { NodeChip, Section } from '../components/bits'
import { useStitch } from '../data'
import { coverageList, type CoverageListKind } from '../lib/coverage'
import { idTail } from '../lib/graph'

export function CoveragePage({ kind }: { kind: CoverageListKind }) {
  const { index } = useStitch()
  const list = coverageList(index, kind)

  return (
    <article className="panel">
      <div className="panel-header">
        <div className="panel-title">
          <h2>{list.title}</h2>
          <span className="panel-type">{list.entries.length}</span>
        </div>
        <p className="panel-description">{list.description}</p>
        <div className="panel-actions">
          <a className="button" href="#/">
            ← Overview
          </a>
        </div>
      </div>

      <Section title={`${list.entries.length} entries`}>
        {list.entries.length === 0 ? (
          <p className="muted">Nothing here — this build has full coverage.</p>
        ) : (
          <ul className="coverage-list">
            {list.entries.map((entry) => (
              <li key={entry.nodeId}>
                {entry.node ? (
                  <NodeChip node={entry.node} />
                ) : (
                  // in the coverage block but not in the graph: name it from the id
                  <span className="coverage-orphan">
                    <code>{idTail(entry.nodeId)}</code>
                    <span className="muted">not in this graph</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </article>
  )
}
