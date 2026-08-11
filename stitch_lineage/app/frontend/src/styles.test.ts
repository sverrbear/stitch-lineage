// The cascade has no runtime signal (#131).
//
// `.apply-modal` sat in the stylesheet *above* the `.modal` base it meant to
// widen, at equal specificity — so the base won on source order and the apply
// dialog rendered at 520px while the source read `min(860px, 100%)`. Nothing
// failed: no console warning, no type error, no test. The only way to catch
// that class of bug is to ask the stylesheet who actually wins.

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

// Comments go first: this file comments almost every rule, and a comment sitting
// above a selector would otherwise be read as part of it.
const CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'styles.css'),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '')

type Rule = { selector: string; body: string; at: number }

/** Every rule declared at the top level, in source order (at-rule bodies skipped). */
function topLevelRules(css: string): Rule[] {
  const rules: Rule[] = []
  let depth = 0
  let head = ''
  let headAt = 0
  for (let i = 0; i < css.length; i++) {
    const char = css[i]
    if (char === '{') {
      depth += 1
      if (depth === 1) {
        const close = matchingBrace(css, i)
        const selector = head.trim()
        // `@media`/`@keyframes` wrap rules rather than declaring any themselves.
        if (!selector.startsWith('@')) {
          rules.push({ selector, body: css.slice(i + 1, close), at: headAt })
        }
        i = close
        depth = 0
        head = ''
      }
    } else if (char === '}') {
      depth = Math.max(0, depth - 1)
      head = ''
    } else {
      if (head.trim() === '') headAt = i
      head += char
    }
  }
  return rules
}

function matchingBrace(css: string, open: number): number {
  let depth = 0
  for (let i = open; i < css.length; i++) {
    if (css[i] === '{') depth += 1
    else if (css[i] === '}') {
      depth -= 1
      if (depth === 0) return i
    }
  }
  return css.length
}

function declaration(body: string, property: string): string | null {
  const match = body.match(new RegExp(`(?:^|[;{])\\s*${property}\\s*:([^;]*)`))
  return match ? match[1].trim() : null
}

/** This file uses classes only, so specificity is just how many a selector names. */
function classCount(selector: string): number {
  return (selector.match(/\./g) ?? []).length
}

/**
 * Of the rules that set `property` on an element carrying every class in
 * `classes`, the one the cascade actually applies: highest specificity, then
 * latest in source.
 */
function winning(property: string, classes: readonly string[]): Rule | null {
  const applicable = topLevelRules(CSS).filter((rule) => {
    if (declaration(rule.body, property) === null) return false
    return rule.selector.split(',').some((one) => {
      const named = one.trim().match(/\.[\w-]+/g) ?? []
      return named.length > 0 && named.every((name) => classes.includes(name.slice(1)))
    })
  })
  if (applicable.length === 0) return null
  return applicable.reduce((best, rule) =>
    classCount(rule.selector) > classCount(best.selector) ||
    (classCount(rule.selector) === classCount(best.selector) && rule.at > best.at)
      ? rule
      : best,
  )
}

describe('the apply dialog sizing survives the cascade', () => {
  it('is the apply-modal rule that sets its width, not the generic .modal', () => {
    const rule = winning('width', ['modal', 'apply-modal'])
    expect(rule?.selector).toContain('apply-modal')
  })

  it('gives the diff most of the viewport rather than conversation width', () => {
    const width = declaration(winning('width', ['modal', 'apply-modal'])?.body ?? '', 'width')
    // the cap the diff lines are sized against — see the #131 screenshots
    expect(width).toMatch(/min\((\d{4,})px, 100%\)/)
    expect(Number(width?.match(/(\d{4,})px/)?.[1])).toBeGreaterThanOrEqual(1000)
  })

  it('leaves the plain .modal narrow, since it holds a sentence and two fields', () => {
    expect(declaration(winning('width', ['modal'])?.body ?? '', 'width')).toBe('min(520px, 100%)')
  })

  it('keeps a visible horizontal scrollbar for lines that overflow even so', () => {
    const rule = topLevelRules(CSS).find((one) => one.selector === '.diff-body')
    expect(declaration(rule?.body ?? '', 'overflow-x')).toBe('auto')
    expect(declaration(rule?.body ?? '', 'scrollbar-width')).toBe('thin')
  })
})

describe('the working button lays out its spinner (#160)', () => {
  it('is .button.is-working that sets display, not the plain .button under it', () => {
    // .button is inline-block on purpose everywhere else, and it is declared ~2000
    // lines earlier: only the extra class makes the flex row win
    const rule = winning('display', ['button', 'is-working'])
    expect(rule?.selector).toContain('is-working')
    expect(declaration(rule?.body ?? '', 'display')).toBe('inline-flex')
  })

  it('leaves every other button inline-block', () => {
    expect(declaration(winning('display', ['button'])?.body ?? '', 'display')).toBe('inline-block')
  })

  it('gives the spinner a gap, so the ring is not against the label', () => {
    expect(declaration(winning('gap', ['button', 'is-working'])?.body ?? '', 'gap')).toBeTruthy()
  })

  it('stops turning under prefers-reduced-motion', () => {
    // no runtime signal for a missing media query either: the animation just keeps
    // running for a reader who asked it not to
    const bodies: string[] = []
    const marker = /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/g
    for (let hit = marker.exec(CSS); hit !== null; hit = marker.exec(CSS)) {
      const open = hit.index + hit[0].length - 1
      bodies.push(CSS.slice(open + 1, matchingBrace(CSS, open)))
    }
    expect(bodies.length).toBeGreaterThan(0)
    // inside such a block, not merely somewhere after one
    const stopped = bodies
      .flatMap((body) => body.match(/\.spinner\s*\{[^}]*\}/g) ?? [])
      .filter((rule) => /animation\s*:\s*none/.test(rule))
    expect(stopped).toHaveLength(1)
  })
})
