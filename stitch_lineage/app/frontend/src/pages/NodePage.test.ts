import { beforeEach, describe, expect, it, vi } from 'vitest'
import { buildIndex } from '../lib/graph'
import { fixtureGraph } from '../lib/fixture'
import { DescriptionEditor } from '../components/DescriptionEditor'
import { NodePage } from './NodePage'

// A small hook harness keeps these interaction tests DOM-free. As in React, a
// panel with a new key gets fresh hook slots; setters already captured by an
// async save continue to address the old slots, never the replacement session.
const harness = vi.hoisted(() => ({
  key: undefined as string | null | undefined,
  slots: [] as unknown[],
  cursor: 0,
  effects: [] as Array<() => void>,
  save: vi.fn(),
  discard: vi.fn(),
}))
vi.mock('react', async () => ({
  ...await vi.importActual('react'),
  useState: (initial: unknown) => {
    const slots = harness.slots
    const position = harness.cursor++
    if (!(position in slots)) slots[position] = initial
    return [slots[position], (value: unknown) => { slots[position] = value }]
  },
  useEffect: (effect: () => void) => { harness.effects.push(effect) },
}))
vi.mock('../data', () => ({ useStitch: () => ({ index }) }))
vi.mock('../lib/staging', async () => ({
  ...await vi.importActual('../lib/staging'),
  stageDescription: harness.save,
  unstageDescription: harness.discard,
}))

const index = buildIndex(fixtureGraph())
type Element = { type: unknown; props: Record<string, any> }

function find(element: any, predicate: (entry: Element) => boolean): Element | undefined {
  if (!element || typeof element !== 'object') return undefined
  if (predicate(element)) return element
  for (const child of [element.props?.children].flat()) {
    const found = find(child, predicate)
    if (found) return found
  }
  return undefined
}

function editor(nodeId: string, onChanged = vi.fn()) {
  const panel = NodePage({ nodeId })
  if (panel.key !== harness.key) {
    harness.key = panel.key
    harness.slots = []
  }
  harness.cursor = 0
  harness.effects = []
  const node = index.nodesById.get(nodeId)!
  const [modelId, column] = nodeId.split('::')
  const entity = index.nodesById.get(modelId)!.name
  const tree = DescriptionEditor({
    entity, column: column ?? null, applied: node.description,
    staged: null, canStage: true, onChanged,
  })
  for (const effect of harness.effects) effect()
  return tree
}

function open(nodeId: string) {
  find(editor(nodeId), (entry) => entry.props.className === 'ghost-button desc-edit')!.props.onClick()
}

function type(nodeId: string, text: string) {
  find(editor(nodeId), (entry) => entry.type === 'textarea')!.props.onChange({ target: { value: text } })
}

function save(nodeId: string) {
  find(editor(nodeId), (entry) => entry.props.className === 'button')!.props.onClick()
}

function draft(nodeId: string) {
  return find(editor(nodeId), (entry) => entry.type === 'textarea')
}

beforeEach(() => {
  harness.key = undefined
  harness.slots = []
  harness.save.mockReset().mockResolvedValue({ created: true })
  harness.discard.mockReset()
})

describe('detail navigation starts a separate editing session', () => {
  it.each([
    ['model.demo.stg_payments', 'model.demo.fct_revenue'],
    ['model.demo.fct_revenue::net_revenue', 'model.demo.fct_revenue::user_id'],
    ['model.demo.stg_payments::amount', 'model.demo.fct_revenue::net_revenue'],
  ])('does not save a draft from %s into %s', async (fromId, toId) => {
    open(fromId)
    type(fromId, 'Only belongs to the first node')
    expect(draft(fromId)?.props.value).toBe('Only belongs to the first node')
    expect(draft(toId)).toBeUndefined()
    open(toId)
    expect(draft(toId)?.props.value).not.toBe('Only belongs to the first node')
    type(toId, 'Second node description')
    save(toId)
    await vi.waitFor(() => expect(harness.save).toHaveBeenCalledOnce())
    const [modelId, column] = toId.split('::')
    expect(harness.save).toHaveBeenCalledWith({
      entity: index.nodesById.get(modelId)!.name,
      column: column ?? null,
      new_description: 'Second node description',
    })
  })

  it.each(['resolve', 'reject'] as const)('isolates a late save %s from the new editor', async (settle) => {
    let resolve!: (value: unknown) => void
    let reject!: (error: Error) => void
    harness.save.mockImplementationOnce(() => new Promise((yes, no) => { resolve = yes; reject = no }))
    const fromId = 'model.demo.stg_payments'
    const toId = 'model.demo.fct_revenue'
    open(fromId)
    type(fromId, 'Pending first-node description')
    save(fromId)
    expect(draft(fromId)?.props.disabled).toBe(true)
    expect(draft(toId)).toBeUndefined()
    open(toId)
    type(toId, 'Keep editing the second node')
    expect(draft(toId)?.props.disabled).toBe(false)
    if (settle === 'resolve') resolve({ created: true })
    else reject(new Error('First-node save failed'))
    await harness.save.mock.results[0].value.catch(() => undefined)
    await Promise.resolve()
    await vi.waitFor(() => {
      // Completion must neither close the new editor nor import its old error.
      expect(draft(toId)?.props.value).toBe('Keep editing the second node')
      expect(draft(toId)?.props.disabled).toBe(false)
      expect(find(editor(toId), (entry) => entry.props.className === 'modal-error')).toBeUndefined()
    })
    expect(harness.save).toHaveBeenCalledWith({
      entity: index.nodesById.get(fromId)!.name, column: null,
      new_description: 'Pending first-node description',
    })
  })

  it('clears validation errors when moving to another node', () => {
    const fromId = 'model.demo.fct_revenue::net_revenue'
    const toId = 'model.demo.fct_revenue::user_id'
    open(fromId)
    type(fromId, ' ')
    save(fromId)
    expect(find(editor(fromId), (entry) => entry.props.className === 'modal-error')).toBeDefined()
    open(toId)
    expect(find(editor(toId), (entry) => entry.props.className === 'modal-error')).toBeUndefined()
    expect(harness.save).not.toHaveBeenCalled()
  })
})
