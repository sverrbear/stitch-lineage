import { afterEach, describe, expect, it } from 'vitest'
import { setStripModelPrefixes } from './present'
import type { StagedDescription, StagedRelationship } from './staging'
import {
  descriptionLabel,
  descriptionPreview,
  diffLines,
  diffStat,
  outcomeSummary,
  workspaceView,
} from './workspace'

function relationship(partial: Partial<StagedRelationship> = {}): StagedRelationship {
  return {
    id: 'r1',
    from_model: 'fct_orders',
    from_column: 'customer_id',
    to_model: 'dim_customers',
    to_column: 'customer_id',
    cardinality: 'many-to-one',
    shape: 'simple',
    ...partial,
  }
}

function description(partial: Partial<StagedDescription> = {}): StagedDescription {
  return {
    id: 'd1',
    entity: 'fct_orders',
    column: 'customer_id',
    new_description: 'The customer who placed the order.',
    ...partial,
  }
}

describe('workspaceView', () => {
  it('counts everything staged, whatever type it is', () => {
    const view = workspaceView(
      [relationship(), relationship({ id: 'r2', from_model: 'fct_refunds' })],
      [description()],
    )
    expect(view.total).toBe(3)
    expect(view.relationships).toHaveLength(1) // both point at dim_customers
    expect(view.descriptions).toHaveLength(1)
  })

  it('groups description edits by model, the model’s own edit first', () => {
    const view = workspaceView(
      [],
      [
        description({ id: 'd1', column: 'status' }),
        description({ id: 'd2', column: null }),
        description({ id: 'd3', column: 'amount' }),
        description({ id: 'd4', entity: 'dim_customers', column: null }),
      ],
    )
    expect(view.descriptions.map((group) => group.entity)).toEqual([
      'dim_customers',
      'fct_orders',
    ])
    expect(view.descriptions[1].entries.map((entry) => entry.column)).toEqual([
      null,
      'amount',
      'status',
    ])
  })

  it('is empty, not broken, with nothing staged', () => {
    expect(workspaceView([], [])).toEqual({ total: 0, relationships: [], descriptions: [] })
  })
})

describe('descriptionLabel', () => {
  afterEach(() => setStripModelPrefixes([]))

  it('reads as model.column, or the model alone for its own description', () => {
    expect(descriptionLabel(description())).toBe('fct_orders.customer_id')
    expect(descriptionLabel(description({ column: null }))).toBe('fct_orders')
  })

  it('hides a routing prefix, like every other surface (#69)', () => {
    setStripModelPrefixes(['viz_'])
    expect(descriptionLabel(description({ entity: 'viz_dim_users', column: 'user_id' }))).toBe(
      'dim_users.user_id',
    )
  })
})

describe('descriptionPreview', () => {
  it('shows the first line of a multi-line edit', () => {
    expect(descriptionPreview(description({ new_description: 'First line.\nSecond line.' }))).toBe(
      'First line.',
    )
  })

  it('truncates a long line with an ellipsis', () => {
    const preview = descriptionPreview(description({ new_description: 'x'.repeat(200) }), 20)
    expect(preview).toHaveLength(20)
    expect(preview.endsWith('…')).toBe(true)
  })
})

const DIFF = `--- a/models/_schema.yml
+++ b/models/_schema.yml
@@ -3,7 +3,8 @@
     columns:
       - name: customer_id
-        description: old
+        description: >-
+          new text
         data_tests:
`

describe('diffLines', () => {
  it('classifies every row of a unified diff', () => {
    const kinds = diffLines(DIFF).map((row) => row.kind)
    expect(kinds.slice(0, 3)).toEqual(['meta', 'meta', 'hunk'])
    expect(kinds).toContain('add')
    expect(kinds).toContain('del')
    expect(kinds).toContain('context')
  })

  it('does not invent a trailing empty row', () => {
    expect(diffLines('a\n')).toHaveLength(1)
  })

  it('survives an empty diff', () => {
    expect(diffLines('')).toEqual([{ kind: 'context', text: '' }])
  })
})

describe('diffStat', () => {
  it('counts added and removed lines, ignoring the file headers', () => {
    expect(diffStat(DIFF)).toEqual({ added: 2, removed: 1 })
  })
})

describe('outcomeSummary', () => {
  it('says what was written', () => {
    expect(
      outcomeSummary({ written: ['a.yml'], refused: [], applied: 2, still_staged: 0 }),
    ).toBe('Wrote 2 changes to 1 file. 0 still staged.')
  })

  it('leads with the refusals, because they are why to look further', () => {
    const summary = outcomeSummary({
      written: ['a.yml'],
      refused: [{ path: 'b.yml' }],
      applied: 1,
      still_staged: 1,
    })
    expect(summary).toContain('1 file refused')
    expect(summary).toContain('1 still staged')
  })

  it('calls a no-op a no-op', () => {
    expect(outcomeSummary({ written: [], refused: [], applied: 1, still_staged: 0 })).toContain(
      'already said',
    )
    expect(outcomeSummary({ written: [], refused: [], applied: 0, still_staged: 0 })).toBe(
      'Nothing to write.',
    )
  })
})
