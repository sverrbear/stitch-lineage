// The ERD canvas must not be derived from where the pointer is (#186).
//
// This is a shape rule about ErdPage, and there is no runtime signal for it: put
// the hovered relationship back into React state and everything still WORKS — the
// right rows light up, every other test passes — it just rebuilds all 41 cards and
// all 29 edge objects to do it, and the canvas visibly churns. The regression is
// invisible except as slowness, so the check is on the source, over a real AST,
// the way `copy.enforce.test.ts` checks the copy rule.
//
// The rule: `baseNodes` and `edges` — the two arrays handed to React Flow — are
// built without consulting the highlight store, and the page holds no hover state
// of its own. Where the pointer is reaches the screen through
// `components/erdHighlight`, whose subscribers are the two rows and the one line
// that actually change.

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseAst } from 'rolldown/parseAst'
import { describe, expect, it } from 'vitest'

const PAGE = join(dirname(fileURLToPath(import.meta.url)), 'ErdPage.tsx')
const SOURCE = readFileSync(PAGE, 'utf8')

interface Node {
  type?: string
  [key: string]: unknown
}

function walk(node: unknown, visit: (node: Node) => void): void {
  if (!node || typeof node !== 'object') return
  if (Array.isArray(node)) {
    for (const child of node) walk(child, visit)
    return
  }
  const current = node as Node
  visit(current)
  for (const key of Object.keys(current)) {
    if (key !== 'type') walk(current[key], visit)
  }
}

const AST = parseAst(SOURCE, { lang: 'tsx' }) as unknown as Node

/** The `useMemo(...)` call a top-level `const <name> = useMemo(...)` is initialised with. */
function memoCall(name: string): Node {
  let found: Node | null = null
  walk(AST, (node) => {
    if (node.type !== 'VariableDeclarator') return
    const id = node.id as Node | undefined
    const init = node.init as Node | undefined
    if (id?.type !== 'Identifier' || id.name !== name) return
    if (init?.type !== 'CallExpression') return
    if ((init.callee as Node | undefined)?.name !== 'useMemo') return
    found = init
  })
  if (!found) throw new Error(`no \`const ${name} = useMemo(...)\` in ErdPage.tsx`)
  return found
}

/** Every identifier named anywhere inside a call — callback body and dependency list. */
function identifiersIn(call: Node): Set<string> {
  const names = new Set<string>()
  walk(call, (node) => {
    if (node.type === 'Identifier' && typeof node.name === 'string') names.add(node.name)
  })
  return names
}

/** The dependency array of a `useMemo(fn, [deps])`, as written. */
function dependencies(call: Node): string[] {
  const args = call.arguments as Node[]
  const deps = args[1]
  if (deps?.type !== 'ArrayExpression') throw new Error('useMemo without a dependency array')
  return (deps.elements as Node[]).map((element) =>
    element?.type === 'Identifier' ? String(element.name) : SOURCE.slice(
      Number(element?.start ?? 0),
      Number(element?.end ?? 0),
    ),
  )
}

describe('the arrays handed to React Flow do not depend on the pointer (#186)', () => {
  it('builds the relationships from the drawing alone', () => {
    expect(dependencies(memoCall('edges'))).toEqual(['erd'])
  })

  it('never consults the highlight store while building the relationships', () => {
    // `highlight` is the only place hovered/picked lives now, so naming it in here
    // is exactly the regression: one line lighting up would mint 29 fresh objects
    expect(identifiersIn(memoCall('edges'))).not.toContain('highlight')
  })

  it('never consults the highlight store while building the cards', () => {
    // and this one is worse: a card's `data` changing makes React Flow re-adopt
    // every node, which is what used to rebuild edge elements under the pointer
    expect(identifiersIn(memoCall('baseNodes'))).not.toContain('highlight')
  })

  it('puts no lit-column set into a card, the way it used to', () => {
    expect(identifiersIn(memoCall('baseNodes'))).not.toContain('litColumns')
    expect(identifiersIn(memoCall('baseNodes'))).not.toContain('lit')
  })
})

describe('the page holds no hover state of its own (#186)', () => {
  /** Names bound by `const [a, b] = useState(...)` anywhere in the file. */
  const stateNames = new Set<string>()
  walk(AST, (node) => {
    if (node.type !== 'VariableDeclarator') return
    const init = node.init as Node | undefined
    if (init?.type !== 'CallExpression') return
    if ((init.callee as Node | undefined)?.name !== 'useState') return
    const id = node.id as Node | undefined
    if (id?.type !== 'ArrayPattern') return
    for (const element of (id.elements ?? []) as Node[]) {
      if (element?.type === 'Identifier') stateNames.add(String(element.name))
    }
  })

  it('keeps the pointed-at relationship out of React state', () => {
    // state here re-renders the page, and the page IS the canvas
    expect([...stateNames]).not.toContain('hovered')
    expect([...stateNames]).not.toContain('setHovered')
  })

  it('keeps the clicked relationship out of React state', () => {
    expect([...stateNames]).not.toContain('picked')
    expect([...stateNames]).not.toContain('setPicked')
  })

  it('still holds the state that IS about the drawing', () => {
    // a guard on the guard: if useState stopped being found at all, the two checks
    // above would pass on an empty set and pin nothing
    expect([...stateNames]).toContain('nodes')
    expect([...stateNames]).toContain('expanded')
  })
})
