// Copy lives in one file, and this is what keeps it there (#177).
//
// The rule: no user-facing text anywhere but `src/copy.tsx`. Prose that leaks back
// into a component is invisible in review — it reads like every other line — so the
// check is mechanical and over a real AST rather than a regex: JSX text nodes, and
// the attributes a person actually reads (title, placeholder, aria-label, alt).
// Class names, node ids and technical tokens are not copy and are left alone.
//
// The parser is rolldown's (`rolldown/parseAst`), which is what this Vite already
// builds with. The `typescript` package on this project is the native port and ships
// no JS compiler API at all — `require('typescript')` has two keys, both version
// strings — so the TS AST is not available to us.
//
// When this fails it prints file:line and the offending string. The fix is to move it
// into copy.tsx, not to widen ALLOWED.

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseAst } from 'rolldown/parseAst'
import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))

/** Attributes whose string value a reader sees. `className` and friends are not copy. */
const READABLE_ATTRS = new Set(['title', 'placeholder', 'aria-label', 'alt'])

/**
 * Not copy, however prose-shaped it looks: typography and glyphs that carry no words.
 * Anything added here needs a reason that is not "it was easier".
 */
const ALLOWED = new Set(['·', '↗', '✓', '→', '—', '…', '+', '/', ':', ',', '(', ')'])

/** Files that are not product surface: the copy module itself, and fixtures. */
const SKIP = new Set(['copy.tsx', 'copy.enforce.test.ts', 'fixture.ts'])

function sourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) {
      out.push(...sourceFiles(path))
      continue
    }
    if (!/\.tsx?$/.test(entry) || entry.includes('.test.') || SKIP.has(entry)) continue
    out.push(path)
  }
  return out
}

/**
 * Text a reader would recognise as a sentence or label rather than a token.
 *
 * The single-word case is the one worth getting right, because it is where the copy
 * a reader most often wants to edit lives: `Home`, `ERD`, `Cancel`, `Close`. Those
 * must be caught. What must NOT be caught is the technical vocabulary the app
 * displays verbatim — a node id, a filename, a config value — and the tell between
 * them is shape: a path or an all-lowercase value is data, a capitalised word is a
 * label somebody wrote.
 */
export function looksLikeCopy(raw: string): boolean {
  const text = raw.trim()
  if (!text || ALLOWED.has(text)) return false
  // no two adjacent letters: punctuation, glyphs, numbers
  if (!/[A-Za-z]{2}/.test(text)) return false
  if (!/\s/.test(text)) {
    // a path, id, filename or dotted name: `api/graph`, `schema.yml`, `a::b`
    if (/[._/:]/.test(text)) return false
    // an all-lowercase value: `column`, `many-to-one`, `relationships_test`
    if (text === text.toLowerCase()) return false
  }
  return true
}

interface Offender {
  where: string
  kind: string
  text: string
}

interface Node {
  type?: string
  start?: number
  [key: string]: unknown
}

function offendersIn(path: string): Offender[] {
  const source = readFileSync(path, 'utf8')
  const ast = parseAst(source, { lang: path.endsWith('.tsx') ? 'tsx' : 'ts' }) as unknown as Node
  const found: Offender[] = []
  const lineOf = (start: number) => source.slice(0, start).split('\n').length
  const record = (start: number | undefined, text: string, kind: string) => {
    if (!looksLikeCopy(text)) return
    found.push({
      where: `${relative(SRC, path)}:${start === undefined ? '?' : lineOf(start)}`,
      kind,
      text: text.trim().replace(/\s+/g, ' '),
    })
  }

  const walk = (node: unknown) => {
    if (!node || typeof node !== 'object') return
    if (Array.isArray(node)) {
      node.forEach(walk)
      return
    }
    const current = node as Node
    if (current.type === 'JSXText') {
      record(current.start, String(current.value ?? ''), 'jsx text')
    } else if (current.type === 'JSXAttribute') {
      const name = (current.name as Node | undefined)?.name
      if (typeof name === 'string' && READABLE_ATTRS.has(name)) {
        // the plain cases: a literal, or a ternary of literals
        const fromValue = (value: unknown): void => {
          const v = value as Node | null
          if (!v || typeof v !== 'object') return
          if (v.type === 'Literal' && typeof v.value === 'string') {
            record(v.start, v.value, name)
          } else if (v.type === 'JSXExpressionContainer') {
            fromValue(v.expression)
          } else if (v.type === 'ConditionalExpression') {
            fromValue(v.consequent)
            fromValue(v.alternate)
          } else if (v.type === 'TemplateLiteral' && Array.isArray(v.quasis)) {
            for (const quasi of v.quasis as Node[]) {
              const cooked = (quasi.value as { cooked?: string } | undefined)?.cooked
              if (typeof cooked === 'string') record(quasi.start, cooked, `${name} template`)
            }
          }
        }
        fromValue(current.value)
      }
    }
    for (const key of Object.keys(current)) {
      if (key !== 'type') walk(current[key])
    }
  }
  walk(ast)
  return found
}

describe('every user-facing string lives in copy.tsx (#177)', () => {
  it('leaves no readable text inline in a component', () => {
    const offenders = sourceFiles(SRC).flatMap(offendersIn)
    const report = offenders.map((o) => `${o.where}  [${o.kind}]  ${JSON.stringify(o.text)}`)
    expect(
      report,
      `${report.length} user-facing string(s) still inline — move them into src/copy.tsx:\n${report.join('\n')}\n`,
    ).toEqual([])
  })

  it('recognises copy without flagging technical tokens', () => {
    // the heuristic itself, so a future loosening of it is a visible change
    expect(looksLikeCopy('Reset view')).toBe(true)
    expect(looksLikeCopy('drag from a column’s edge onto another column')).toBe(true)
    expect(looksLikeCopy('no cards')).toBe(true)
    expect(looksLikeCopy('Home')).toBe(true)
    expect(looksLikeCopy('ERD')).toBe(true)
    expect(looksLikeCopy('Cancel')).toBe(true)
    expect(looksLikeCopy('column')).toBe(false)
    expect(looksLikeCopy('schema.yml')).toBe(false)
    expect(looksLikeCopy('api/graph')).toBe(false)
    expect(looksLikeCopy('relationships_test')).toBe(false)
    expect(looksLikeCopy('many-to-one')).toBe(false)
    expect(looksLikeCopy('model.demo.fct_orders::user_id')).toBe(false)
    expect(looksLikeCopy('·')).toBe(false)
    expect(looksLikeCopy('  ')).toBe(false)
    expect(looksLikeCopy('42')).toBe(false)
  })
})
