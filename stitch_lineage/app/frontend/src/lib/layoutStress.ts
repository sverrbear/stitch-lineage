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
//     gets a target distance `r_i` from the CONSTELLATION's centroid, worth
//     `radialStrength` of an edge, from an annulus assignment that seats the busiest
//     tables innermost (see `radialTargets`). This is a stress term of exactly the
//     same shape as an edge — an anchor rather than a spring — so it is minimised
//     alongside everything else instead of fighting it.
//
//   * RECTANGULAR COLLISION, WEIGHTED BY DEGREE. Cards are not points. From halfway
//     through the iterations a damped separation sweep runs after each majorization
//     step, using each card's real measured width and height, so tables settle into
//     positions that a 300×246 rectangle can actually occupy instead of positions a
//     point could. Each card's mass is its degree, so when a busy table's neighbours
//     cannot all fit on one orbit it is the LEAVES that give way — the ordering the
//     radial term asks for comes out of the collision instead of fighting it. (The
//     hard guarantee is `separateBoxes`; this is the part that lets the optimiser see
//     the constraint while it still has freedom to move.)
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
  const { radius, active: radial } = radialTargets(adjacency, idealLength)

  const boxes: SeparationBox[] = ids.map((id, i) => {
    const node = byId.get(id) as StressNode
    // mass = degree: see SeparationBox.mass for why the collision has to know this
    return { id, cx: 0, cy: 0, w: node.w, h: node.h, mass: Math.max(1, adjacency[i].length) }
  })
  seed(boxes, adjacency, radius, radial, groups, idealLength, clusterSpread)

  const collisionFrom = Math.floor(iterations * COLLISION_FROM)
  for (let iteration = 0; iteration < iterations; iteration++) {
    // one anchor for the constellation: the radial term is about where a table sits
    // in the picture, not where it sits inside its own cluster
    const anchor = { x: 0, y: 0 }
    for (const box of boxes) {
      anchor.x += box.cx / count
      anchor.y += box.cy / count
    }

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

      // the radial anchor: a stress term of the same shape as an edge, but to a
      // fixed point in the middle of the constellation rather than to another table
      if (radial) {
        const r = radius[i]
        // The anchor competes with the node's own relationships, so its weight is
        // scaled BY DEGREE to keep that competition proportional. Weighting it
        // uniformly gets the authority exactly backwards: against a hub's twenty
        // edges a single anchor is noise, so the hub drifts off the middle, while
        // against a leaf's one edge the same anchor dominates. Both errors point the
        // same way — the hub ends up outside its own leaves — and no amount of
        // `radialStrength` fixes an anchor pointing the wrong way. Per degree,
        // `radialStrength` reads as "worth this fraction of each relationship the
        // table has", and it holds a hub in the middle as firmly as it pushes a leaf
        // out.
        const anchorWeight = (radialStrength * Math.max(1, adjacency[i].length)) / idealLength ** 2
        const dx = boxes[i].cx - anchor.x
        const dy = boxes[i].cy - anchor.y
        const distance = Math.max(EPSILON, Math.hypot(dx, dy))
        numeratorX += anchorWeight * (anchor.x + (r * dx) / distance)
        numeratorY += anchorWeight * (anchor.y + (r * dy) / distance)
        denominator += anchorWeight
      }

      if (denominator > 0) {
        boxes[i].cx = numeratorX / denominator
        boxes[i].cy = numeratorY / denominator
      }
    }

    if (iteration >= collisionFrom) relaxOverlaps(boxes, gutter, 0.5)
  }

  alignPrincipalAxis(boxes)
  for (const box of boxes) positions.set(box.id, { x: box.cx, y: box.cy })
  return positions
}

/**
 * Stress is rotation-invariant: the objective cannot tell a constellation from the
 * same constellation turned thirty degrees, so majorization leaves the orientation
 * to whatever the start happened to imply. That gauge freedom has to be spent
 * deliberately, or it is spent badly — two tables joined by one relationship came
 * out stacked corner to corner, and the main constellation came out tall and narrow
 * on a canvas that is wide.
 *
 * So the solution is rotated onto its own principal axis: the eigenvector of the
 * centres' covariance, laid along x. The widest direction of whatever the graph
 * turned out to be becomes the widest direction of the viewport, and a pair becomes
 * a pair side by side. Distances — and therefore the stress — are untouched;
 * rectangles are axis-aligned, so the overlap guarantee still runs afterwards.
 */
function alignPrincipalAxis(boxes: SeparationBox[]): void {
  if (boxes.length < 2) return
  let meanX = 0
  let meanY = 0
  for (const box of boxes) {
    meanX += box.cx / boxes.length
    meanY += box.cy / boxes.length
  }
  let xx = 0
  let yy = 0
  let xy = 0
  for (const box of boxes) {
    const dx = box.cx - meanX
    const dy = box.cy - meanY
    xx += dx * dx
    yy += dy * dy
    xy += dx * dy
  }
  // a round blob has no principal axis worth honouring; leave it where it is
  if (Math.abs(xy) < EPSILON && Math.abs(xx - yy) < EPSILON) return
  const angle = 0.5 * Math.atan2(2 * xy, xx - yy)
  const cos = Math.cos(-angle)
  const sin = Math.sin(-angle)
  for (const box of boxes) {
    const dx = box.cx - meanX
    const dy = box.cy - meanY
    box.cx = meanX + dx * cos - dy * sin
    box.cy = meanY + dx * sin + dy * cos
  }
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
 * How far from the constellation's middle each table wants to sit — the term that
 * makes the picture read hubs-in-the-middle, leaves-at-the-edge (Sverrir, #101).
 *
 * Tables are ORDERED first: by how many relationships they are from the busiest
 * table in the component, and — the part that matters — busiest-first within each
 * of those distances. Then they are seated outward into annuli one ideal length
 * apart, each annulus taking as many as its circumference has room for. So the
 * tables that hold the schema together take the inner annulus and the degree-1
 * leaves are seated after them, which is the requirement stated as a target rather
 * than hoped for as a side effect.
 *
 * Two details earn their keep:
 *
 *   * The base is the UNWEIGHTED hop count. Letting the community spreading into
 *     the radius as well was a bug worth naming: a cluster's own degree-3 hub was
 *     pushed a spread-multiplier out and ended up further from the middle than the
 *     degree-1 leaves of the cluster beside it — the requirement upside down.
 *
 *   * An annulus is a TARGET, not a seat. It says which ring a table belongs to;
 *     majorization and rectangular collision then pull the ring in as tight as the
 *     cards allow, which is why this costs so much less edge length than seating
 *     cards on circles does. A table can never be seated inside its own parent
 *     (`hops` floors the ring), so a subtree still reads outward.
 *
 * The whole component shares one anchor — its centroid — so this is a statement
 * about the constellation, which is what the reader is looking at. A component with
 * no degree hierarchy at all (a pair, a ring) gets no radial term: there is no hub
 * to put in the middle and it would only stretch it.
 */
function radialTargets(
  adjacency: readonly number[][],
  idealLength: number,
): { radius: number[]; active: boolean } {
  const count = adjacency.length
  const radius = new Array<number>(count).fill(0)
  let hub = 0
  for (let i = 0; i < count; i++) {
    if (adjacency[i].length > adjacency[hub].length) hub = i
  }
  if (adjacency[hub].length < 2) return { radius, active: false }

  const hops = new Array<number>(count).fill(Infinity)
  hops[hub] = 0
  let frontier = [hub]
  while (frontier.length > 0) {
    const next: number[] = []
    for (const i of frontier) {
      for (const other of adjacency[i]) {
        if (Number.isFinite(hops[other])) continue
        hops[other] = hops[i] + 1
        next.push(other)
      }
    }
    frontier = next
  }

  const order: number[] = []
  for (let i = 0; i < count; i++) if (i !== hub) order.push(i)
  order.sort(
    (a, b) =>
      (Number.isFinite(hops[a]) ? hops[a] : count) - (Number.isFinite(hops[b]) ? hops[b] : count) ||
      adjacency[b].length - adjacency[a].length ||
      a - b,
  )

  // how many cards an annulus has circumference for, at one ideal length of arc each
  const capacity = (ring: number) => Math.max(1, Math.floor(2 * Math.PI * ring))
  let ring = 1
  let seated = 0
  for (const i of order) {
    const floor = Number.isFinite(hops[i]) ? hops[i] : 1
    if (floor > ring) {
      ring = floor
      seated = 0
    }
    if (seated >= capacity(ring)) {
      ring += 1
      seated = 0
    }
    radius[i] = ring * idealLength
    seated += 1
  }
  return { radius, active: true }
}

/**
 * The starting arrangement, and it matters more than a start usually does.
 *
 * Majorization is a LOCAL method: it will compact and tidy the configuration it is
 * given, but it will not reconsider which table sits at which coordinate, because
 * exchanging two tables is not a small move in a continuous objective. Whatever
 * ordering the start implies is very largely the ordering that survives. A blind
 * sunflower spiral therefore threw away the one thing the radial term was for — the
 * leaves it happened to seed near the middle stayed near the middle, and the anchor
 * could not outvote twenty rectangles all needing somewhere to be.
 *
 * So the start IS the annulus assignment: every table on the ring
 * `radialTargets` gave it, and inside a ring seated near the neighbour it hangs off,
 * so subtrees begin contiguous and stay that way. Iteration then pulls the rings in
 * as tight as the cards allow, which is where the edge length comes back — seating
 * cards on circles and leaving them there is what costs a third more than it needs
 * to.
 *
 * Fixed constants only: the same graph always starts here.
 */
function seed(
  boxes: SeparationBox[],
  adjacency: readonly number[][],
  radius: readonly number[],
  radial: boolean,
  groups: readonly number[][],
  idealLength: number,
  clusterSpread: number,
): void {
  if (!radial) {
    seedSunflower(boxes, adjacency, groups, idealLength, clusterSpread)
    return
  }
  const angle = new Map<number, number>()
  const rings = [...new Set(radius)].sort((a, b) => a - b)
  for (const ring of rings) {
    const occupants = radius
      .map((r, i) => ({ r, i }))
      .filter((entry) => entry.r === ring)
      .map((entry) => entry.i)
      .sort((a, b) => adjacency[b].length - adjacency[a].length || boxes[a].id.localeCompare(boxes[b].id))
    if (ring === 0) {
      for (const i of occupants) {
        angle.set(i, 0)
        boxes[i].cx = 0
        boxes[i].cy = 0
      }
      continue
    }
    // each table would like to sit on the side of the ring its parent is on
    const preferred = new Map<number, number>()
    occupants.forEach((i, rank) => {
      let sin = 0
      let cos = 0
      for (const other of adjacency[i]) {
        const known = angle.get(other)
        if (known === undefined) continue
        sin += Math.sin(known)
        cos += Math.cos(known)
      }
      preferred.set(
        i,
        sin === 0 && cos === 0 ? (2 * Math.PI * rank) / occupants.length : Math.atan2(sin, cos),
      )
    })
    // keep that ORDER but space them evenly, so a ring is never a heap on one side
    const seatedOrder = [...occupants].sort(
      (a, b) =>
        (preferred.get(a) as number) - (preferred.get(b) as number) ||
        boxes[a].id.localeCompare(boxes[b].id),
    )
    seatedOrder.forEach((i, rank) => {
      const theta = (2 * Math.PI * rank) / seatedOrder.length
      angle.set(i, theta)
      boxes[i].cx = Math.cos(theta) * ring
      boxes[i].cy = Math.sin(theta) * ring
    })
  }
}

/** The fallback start for a component with no hub to build rings around. */
function seedSunflower(
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
