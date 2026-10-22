import { describe, expect, it } from 'vitest'
import { readableViewport } from './diagramViewport'

describe('readable diagram framing', () => {
  const focus = { x: 400, y: 200, width: 300, height: 160 }
  it('frames a small diagram without making its labels microscopic', () => {
    const view = readableViewport({ x: 0, y: 0, width: 700, height: 250 }, focus, 1280, 480)
    expect(view.zoom).toBe(1.1)
    expect(view.x).toBeGreaterThan(0)
  })
  it('centres the selected object when the full diagram cannot fit readably', () => {
    const view = readableViewport({ x: 0, y: 0, width: 8000, height: 5000 }, focus, 1280, 480)
    expect(view.zoom).toBe(1)
    expect(view.x + (focus.x + focus.width / 2) * view.zoom).toBe(640)
    expect(view.y + (focus.y + focus.height / 2) * view.zoom).toBe(240)
  })
  it('reserves space for both drawers and keeps the top of an expanded table visible', () => {
    const tall = { ...focus, height: 1800 }
    const view = readableViewport(tall, tall, 1280, 480, { left: 344, right: 344 })
    expect(view.x + (tall.x + tall.width / 2)).toBe(640)
    expect(view.y + tall.y).toBe(24)
  })
})
