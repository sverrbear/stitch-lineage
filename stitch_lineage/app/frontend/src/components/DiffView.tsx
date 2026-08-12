// Read-only unified diff, one file per block (#72).
//
// This is the review surface: what it shows is exactly what `stitch apply
// --dry-run` prints, so nothing is summarised, re-wrapped or interpreted. The only
// thing added is colour and a `+n / −n` count per file.

import type { ApplyFile } from '../lib/staging'
import { diffLines, diffStat } from '../lib/workspace'
import { copy } from '../copy'

export function DiffView({ files }: { files: readonly ApplyFile[] }) {
  if (files.length === 0) {
    return <p className="muted">{copy.diff.empty}</p>
  }
  return (
    <div className="diff-files">
      {files.map((file) => {
        const stat = diffStat(file.diff)
        return (
          <section key={file.path} className="diff-file">
            <header className="diff-file-head">
              <code className="diff-file-path">{file.path}</code>
              <span className="diff-stat">
                <span className="diff-added">+{stat.added}</span>
                <span className="diff-removed">−{stat.removed}</span>
              </span>
            </header>
            <pre className="diff-body">
              {diffLines(file.diff).map((row, i) => (
                <span key={i} className={`diff-line diff-${row.kind}`}>
                  {row.text || ' '}
                </span>
              ))}
            </pre>
          </section>
        )
      })}
    </div>
  )
}
