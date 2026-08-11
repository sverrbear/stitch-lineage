// Where the ERD's coordinates come from (#101): stress majorization (SMACOF) over
// the relationship graph, not a ring of heuristic seats.
//
// The layout is stated as a minimisation. For every pair of tables the graph says
// how far apart they SHOULD be — `d(i,j)`, their shortest path through the
// relationships — and the placement minimises
//
//     stress(P) = Σ_{i<j} w(i,j) · ( ‖p_i − p_j‖ − d(i,j) )²   ,  w = 1/d²
//
// which is the standard weighted-MDS objective: short paths are honoured first
// (that is what `1/d²` buys) so a relationship stays readable even when the far
// side of the schema has to give. Majorization is the iteration that always
// decreases it, so the result does not depend on a step size or a lucky start.
//
// Three things are folded into the same objective rather than applied afterwards as
// nudges, because a nudge is what the ring seats already were:
//
//   * COMMUNITY CLUSTERING. `d` is measured on weighted edges: an edge inside a
//     community counts as one ideal length, an edge that leaves it counts as
//     `clusterSpread` of them. Clusters therefore separate as a consequence of the
//     distances being minimised, and `d` stays a metric, so the objective stays the
//     one majorization is guaranteed to decrease.
//
//   * HUBS INTERIOR, DEGREE-1 LEAVES PERIPHERAL (Sverrir, #101). Each table also
//     gets a target distance `r_i` from its own community's centroid, worth
//     `radialStrength` of an edge, with `r_i` falling from the community's radius
//     for a leaf to zero for its busiest table. This is a stress term of exactly
//     the same shape as an edge — an anchor rather than a spring — so it is
//     minimised alongside everything else instead of fighting it. It is what
//     separates a degree-1 leaf from a degree-3 table that happens to sit the same
//     number of hops out.
//
//   * RECTANGULAR COLLISION. Cards are not points. From halfway through the
//     iterations a damped separation sweep runs after each majorization step, using
//     each card's real measured width and height, so tables settle into positions
//     that a 300×246 rectangle can actually occupy instead of positions a point
//     could. (The hard guarantee is `separateBoxes`; this is the part that lets the
//     optimiser see the constraint while it still has freedom to move.)
//
// Deterministic throughout: nodes are indexed by sorted id, the start is a fixed
// sunflower spiral, and there is no random number anywhere in the file — an unseeded
// `Math.random` would make the canvas reshuffle on every open, which is the bug
// #101 asks not to have.

import { relaxOverlaps, type SeparationBox } from './layoutSeparation'

export interface StressNode {
  id: string
  w: number
  h: number
}

export interface StressEdge {
  from: string
  to: string
}

export interface StressOptions {
  /** Centre-to-centre distance one relationship should span. */
  idealLength: number
  /** What an edge leaving its community costs, in ideal lengths. */
  clusterSpread: number
  /** Weight of the radial hubs-in/leaves-out term, relative to one edge. */
  radialStrength: number
  /** Minimum clearance the interleaved collision sweeps aim for. */
  gutter: number
  iterations: number
}

export const STRESS_DEFAULTS: StressOptions = {
  idealLength: 352,
  clusterSpread: 1.9,
  radialStrength: 0.55,
  gutter: 28,
  iterations: 240,
}

/** Vogel's angle: the deterministic spiral that spreads a start evenly. */
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5))
/** Guard for coincident points, where the majorization's unit vector is undefined. */
const EPSILON = 1e-9
/** Iterations spent on the graph alone before card rectangles start pushing back. */
const COLLISION_FROM = 0.5

/**
 * Place one connected component. `communities` partitions its ids (see
 * `communitiesOf`); every id in `nodes` must appear in exactly one of them.
 * Returns card CENTRES, positioned around the origin.
 */
export function stressLayout(
  nodes: readonly StressNode[],
  edges: readonly StressEdge[],
  communities: readonly string[][],
  options: Partial<StressOptions> = {},
): Map<string, { x: number; y: number }> {
  const { idealLength, clusterSpread, radialStrength, gutter, iterations } = {
    ...STRESS_DEFAULTS,
    ...options,
  }
  const positions = new Map<string, { x: number; y: number }>()
  if (nodes.length === 0) return positions

  // sorted ids are the index space: everything downstream is then independent of
  // the order the caller happened to hand things over in
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const ids = [...byId.keys()].sort()
  const index = new Map(ids.map((id, i) => [id, i]))
  const count = ids.length
  if (count === 1) {
    positions.set(ids[0], { x: 0, y: 0 })
    return positions
  }

  const community = new Int32Array(count)
  const groups: number[][] = []
  communities.forEach((members) => {
    const group: number[] = []
    for (const id of [...members].sort()) {
      const at = index.get(id)
      if (at === undefined) continue
      community[at] = groups.length
      group.push(at)
    }
    if (group.length > 0) groups.push(group)
  })
  // a node no community claimed (a caller passing an incomplete partition) forms
  // its own, so the radial term still has a centroid to work from
  for (let i = 0; i < count; i++) {
    if (!groups.some((group) => group.includes(i))) {
      community[i] = groups.length
      groups.push([i])
    }
  }

  const adjacency: number[][] = ids.map(() => [])
  for (const edge of edges) {
    const a = index.get(edge.from)
    const b = index.get(edge.to)
    if (a === undefined || b === undefined || a === b) continue
    if (!adjacency[a].includes(b)) adjacency[a].push(b)
    if (!adjacency[b].includes(a)) adjacency[b].push(a)
  }
  for (const list of adjacency) list.sort((a, b) => a - b)

  const target = shortestPaths(adjacency, community, idealLength, idealLength * clusterSpread)
  const radius = radialTargets(adjacency, groups, target)

  const boxes: SeparationBox[] = ids.map((id) => {
    const node = byId.get(id) as StressNode
    return { id, cx: 0, cy: 0, w: node.w, h: node.h }
  })
  seed(boxes, adjacency, groups, idealLength, clusterSpread)

  const collisionFrom = Math.floor(iterations * COLLISION_FROM)
  for (let iteration = 0; iteration < iterations; iteration++) {
    const centroid = groups.map((group) => {
      let x = 0
      let y = 0
      for (const i of group) {
        x += boxes[i].cx
        y += boxes[i].cy
      }
      return { x: x / group.length, y: y / group.length }
    })

    // Gauss-Seidel: each node moves to the position that minimises the majorizing
    // quadratic given where everything else is right now. In sorted-id order, so
    // the sequence itself is deterministic.
    for (let i = 0; i < count; i++) {
      let numeratorX = 0
      let numeratorY = 0
      let denominator = 0
      for (let j = 0; j < count; j++) {
        if (i === j) continue
        const d = target[i][j]
        if (!Number.isFinite(d) || d <= 0) continue
        const weight = 1 / (d * d)
        const dx = boxes[i].cx - boxes[j].cx
        const dy = boxes[i].cy - boxes[j].cy
        const distance = Math.max(EPSILON, Math.hypot(dx, dy))
        numeratorX += weight * (boxes[j].cx + (d * dx) / distance)
        numeratorY += weight * (boxes[j].cy + (d * dy) / distance)
        denominator += weight
      }

      // the radial anchor: an edge to a fixed point at the community's middle
      const anchor = centroid[community[i]]
      const r = radius[i]
      const anchorWeight = radialStrength / Math.max(idealLength, r) ** 2
      const dx = boxes[i].cx - anchor.x
      const dy = boxes[i].cy - anchor.y
      const distance = Math.max(EPSILON, Math.hypot(dx, dy))
      numeratorX += anchorWeight * (anchor.x + (r * dx) / distance)
      numeratorY += anchorWeight * (anchor.y + (r * dy) / distance)
      denominator += anchorWeight

      if (denominator > 0) {
        boxes[i].cx = numeratorX / denominator
        boxes[i].cy = numeratorY / denominator
      }
    }

    if (iteration >= collisionFrom) relaxOverlaps(boxes, gutter, 0.5)
  }

  for (const box of boxes) positions.set(box.id, { x: box.cx, y: box.cy })
  return positions
}

/**
 * Target distances: every pair's shortest path, in pixels, over edges that cost one
 * ideal length inside a community and `inter` across one. Dijkstra per source —
 * a scope is hundreds of tables, so O(n² log n) is nothing, and unlike a hop count
 * it keeps the cluster spreading inside the metric.
 */
function shortestPaths(
  adjacency: readonly number[][],
  community: Int32Array,
  intra: number,
  inter: number,
): number[][] {
  const count = adjacency.length
  const all: number[][] = []
  for (let source = 0; source < count; source++) {
    const distance = new Array<number>(count).fill(Infinity)
    const settled = new Array<boolean>(count).fill(false)
    distance[source] = 0
    for (;;) {
      // linear scan for the nearest unsettled node: no heap, no tie-break ambiguity
      let next = -1
      for (let i = 0; i < count; i++) {
        if (settled[i] || !Number.isFinite(distance[i])) continue
        if (next < 0 || distance[i] < distance[next]) next = i
      }
      if (next < 0) break
      settled[next] = true
      for (const other of adjacency[next]) {
        const weight = community[next] === community[other] ? intra : inter
        if (distance[next] + weight < distance[other]) distance[other] = distance[next] + weight
      }
    }
    all.push(distance)
  }
  return all
}

/**
 * How far from its community's middle each table wants to sit. Zero for the
 * community's busiest table, the community's own radius for a degree-1 leaf, and
 * `log(degree)` in between — linear in degree would put a degree-3 table almost as
 * far out as a leaf whenever one hub has twenty relationships, which is exactly the
 * shape a real analytics schema has.
 */
function radialTargets(
  adjacency: readonly number[][],
  groups: readonly number[][],
  target: readonly number[][],
): number[] {
  const radius = new Array<number>(adjacency.length).fill(0)
  for (const group of groups) {
    let hub = group[0]
    for (const i of group) if (adjacency[i].length > adjacency[hub].length) hub = i
    const highest = adjacency[hub].length
    if (highest <= 1) continue
    let extent = 0
    for (const i of group) {
      const d = target[hub][i]
      if (Number.isFinite(d)) extent = Math.max(extent, d)
    }
    for (const i of group) {
      const degree = Math.max(1, adjacency[i].length)
      radius[i] = extent * (1 - Math.log(degree) / Math.log(highest))
    }
  }
  return radius
}

/**
 * The starting arrangement — communities on a ring, and inside each one a sunflower
 * spiral ordered busiest-first, so the iterations begin from something that already
 * has the right shape. Fixed constants only: the same graph always starts here.
 */
function seed(
  boxes: SeparationBox[],
  adjacency: readonly number[][],
  groups: readonly number[][],
  idealLength: number,
  clusterSpread: number,
): void {
  const ring =
    groups.length < 2 ? 0 : (idealLength * clusterSpread * groups.length) / (2 * Math.PI) + idealLength
  groups.forEach((group, at) => {
    const angle = groups.length < 2 ? 0 : (2 * Math.PI * at) / groups.length
    const originX = Math.cos(angle) * ring
    const originY = Math.sin(angle) * ring
    const ordered = [...group].sort(
      (a, b) => adjacency[b].length - adjacency[a].length || boxes[a].id.localeCompare(boxes[b].id),
    )
    ordered.forEach((i, rank) => {
      const r = idealLength * 0.9 * Math.sqrt(rank)
      const theta = rank * GOLDEN_ANGLE
      boxes[i].cx = originX + Math.cos(theta) * r
      boxes[i].cy = originY + Math.sin(theta) * r
    })
  })
}
