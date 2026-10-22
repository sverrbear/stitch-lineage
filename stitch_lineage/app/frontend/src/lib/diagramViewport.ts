// Default framing preserves readable labels. Only an explicit overview may shrink
// the entire diagram below this scale; overflowing diagrams stay centred on focus.
export const READABLE_ZOOM = 1

export interface DiagramRect { x: number; y: number; width: number; height: number }
export interface DiagramViewport { x: number; y: number; zoom: number }

export function readableViewport(
  bounds: DiagramRect,
  focus: DiagramRect,
  width: number,
  height: number,
  inset: { left?: number; right?: number } = {},
): DiagramViewport {
  const left = inset.left ?? 0
  const right = inset.right ?? 0
  const availableWidth = Math.max(1, width - left - right)
  const fit = Math.min(
    (availableWidth - 48) / Math.max(1, bounds.width),
    (height - 48) / Math.max(1, bounds.height),
  )
  const zoom = Math.max(READABLE_ZOOM, Math.min(1.1, fit))
  const target = fit >= READABLE_ZOOM ? bounds : focus
  return {
    x: left + availableWidth / 2 - (target.x + target.width / 2) * zoom,
    y: height / 2 - (target.y + Math.min(target.height, (height - 48) / zoom) / 2) * zoom,
    zoom,
  }
}
