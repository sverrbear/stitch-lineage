// The click split on a lineage node (#140): the body re-traces, the NAME navigates.
// This covers where the name goes; the re-root itself is one `navigate(lineageHref)`
// call asserted by hand against the real graph (see the PR).

import { describe, expect, it } from 'vitest'
import type { GraphNode } from '../types'
import { titleTarget } from './LineagePage'

const node = (node_id: string, node_type: GraphNode['node_type'], name = 'x'): GraphNode =>
  ({ node_id, node_type, name }) as GraphNode

const MB = 'https://metabase.example.com'

describe('titleTarget', () => {
  it('sends a card and a dashboard to Metabase itself', () => {
    expect(titleTarget(node('mb_card::412', 'mb_card'), MB)).toEqual({
      href: `${MB}/question/412`,
      external: true,
    })
    expect(titleTarget(node('mb_dash::7', 'mb_dashboard'), MB)).toEqual({
      href: `${MB}/dashboard/7`,
      external: true,
    })
  })

  it('sends a dbt model or column to its own detail page', () => {
    for (const entry of [
      node('model.demo.fct_orders', 'model'),
      node('model.demo.fct_orders::customer_id', 'column'),
    ]) {
      const target = titleTarget(entry, MB)
      expect(target.external).toBe(false)
      expect(target.href).toContain(encodeURIComponent(entry.node_id))
    }
  })

  it('sends a FIELD to its detail page, not to a Metabase URL it cannot build', () => {
    // Metabase's field URLs need the database and table ids too, and the graph
    // carries only the field id — a link built from that would 404
    const target = titleTarget(node('mb_field::101', 'mb_field'), MB)
    expect(target.external).toBe(false)
    expect(target.href).toContain('101')
  })

  it('stays internal when there is no Metabase to link to', () => {
    // a static export built without a metabase url must not emit href="null/question/1"
    expect(titleTarget(node('mb_card::412', 'mb_card'), null).external).toBe(false)
  })
})
