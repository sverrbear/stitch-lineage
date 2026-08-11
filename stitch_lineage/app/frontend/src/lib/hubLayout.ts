// Hub-and-spoke placement for the ERD (#129). Pure TS, no DOM / React Flow imports.
//
// This replaces a constrained optimisation (#101, lib/layoutStress) that measured
// well and READ badly: minimising total edge length on a real schema packs every
// card towards the centroid, and 42 tables came out as one dense clump with the
// relationships buried under the cards. Sverrir rearranged that scope by hand and
// the result was dramatically more legible, so the hand-tuned map is the spec and
// this file reproduces its RULES rather than trying to score its output:
//
//   1. THE HUB IS THE CENTRE. The highest-degree table sits dead centre and its
//      relationships radiate outward. On the reference that is `dim_users`, and it
//      is what makes the map readable: the thing everything joins to is where your
//      eye starts.
//
//   2. COMMUNITIES BECOME SECTORS. Tables that join each other take one wedge of
//      the circle each -- subscriptions in one, matches in another -- so a cluster
//      is a place on the map and not just a colour. Wedges are sized by what the
//      cluster actually needs, so a cluster of nine does not get the same arc as a
//      cluster of two.
//
//   3. EACH SECTOR HAS ITS OWN SUB-HUB. Inside a wedge the busiest table sits
//      nearest the centre and its own satellites ring it, which is the structure
//      the hand-tuned map has around `dim_matches`, `dim_subscriptions`, `dim_forms`.
//
//   4. WHITESPACE IS THE POINT. Radii come from what the cards MEASURE plus a
//      generous gap, so the corridors between clusters stay open for the edges to
//      run through -- which matters more now that they are drawn dead straight
//      (#130) and no longer bend around anything.
//
// Everything is constructive and ordered by id, so the same graph always gives the
// same map. There is no objective function to get stuck in: the only iterative step
// is `relaxBridges`, which is what stops a dimension shared by two clusters from
// being stranded inside one of them.

export interface HubNode {
  id: string
  w: number
  h: number
}

export interface HubEdge {
  from: string
  to: string
}

export interface HubOptions {
  /** What one relationship should span — the ring pitch is built from this. */
  idealLength: number
  /** Minimum clearance between any two cards; also the padding between sectors. */
  gutter: number
  /** Breathing room aimed for, on top of the cards' own size. */
  gap: number
}

interface Placed {
  x: number
  y: number
}

/** Half the diagonal of a card: the radius of the disc that always contains it. */
function radiusOf(node: HubNode): number {
  return Math.hypot(node.w, node.h) / 2
}

function degreesOf(ids: readonly string[], edges: readonly HubEdge[]): Map<string, number> {
  const degree = new Map<string, number>(ids.map((id) => [id, 0]))
  for (const edge of edges) {
    degree.set(edge.from, (degree.get(edge.from) ?? 0) + 1)
    degree.set(edge.to, (degree.get(edge.to) ?? 0) + 1)
  }
  return degree
}

function adjacencyOf(ids: readonly string[], edges: readonly HubEdge[]): Map<string, Set<string>> {
  const adjacency = new Map<string, Set<string>>(ids.map((id) => [id, new Set<string>()]))
  for (const edge of edges) {
    adjacency.get(edge.from)?.add(edge.to)
    adjacency.get(edge.to)?.add(edge.from)
  }
  return adjacency
}

/** The busiest table, ties broken by id so the centre never depends on input order. */
function pickHub(ids: readonly string[], degree: Map<string, number>): string {
  return [...ids].sort((a, b) => {
    const byDegree = (degree.get(b) ?? 0) - (degree.get(a) ?? 0)
    return byDegree !== 0 ? byDegree : a.localeCompare(b)
  })[0]
}

/**
 * One thing that claims a wedge: either a cluster with its own sub-hub, or a single
 * table hanging directly off the global hub.
 */
interface Unit {
  /** Nearest the centre; a lone satellite is its own lead. */
  lead: string
  /** Ringed around the lead, in placement order. */
  satellites: string[]
  /** Radius of the disc this unit needs, lead and satellites included. */
  radius: number
}

/**
 * Group everything except the hub into the units that will claim wedges.
 *
 * A table whose ONLY relationship is to the hub is its own unit: it is a spoke, and
 * burying it inside whichever community happened to absorb it is what turns a clean
 * star into a clump. Everything else keeps its community, minus the hub.
 */
function unitsOf(
  hub: string,
  ids: readonly string[],
  communities: readonly string[][],
  adjacency: Map<string, Set<string>>,
  degree: Map<string, number>,
  size: Map<string, HubNode>,
  options: HubOptions,
): { spokes: string[]; clusters: Unit[] } {
  const known = new Set(ids)
  const spokes: string[] = []
  const clustered: string[][] = []

  for (const community of communities) {
    const members: string[] = []
    for (const id of [...community].sort()) {
      if (id === hub || !known.has(id)) continue
      const neighbours = adjacency.get(id) ?? new Set<string>()
      // its ONLY relationship is to the hub: it is a spoke, and burying it inside
      // whichever community absorbed it is what turns a clean star into a clump
      if (neighbours.size === 1 && neighbours.has(hub)) spokes.push(id)
      else members.push(id)
    }
    if (members.length > 0) clustered.push(members)
  }

  const clusters: Unit[] = clustered.map((members) => {
    const lead = pickHub(members, degree)
    const satellites = members
      .filter((id) => id !== lead)
      .sort((a, b) => {
        const byDegree = (degree.get(b) ?? 0) - (degree.get(a) ?? 0)
        return byDegree !== 0 ? byDegree : a.localeCompare(b)
      })
    return { lead, satellites, radius: unitRadius(lead, satellites, size, options) }
  })

  // Biggest wedge first, so the arc a cluster gets is decided before the smaller
  // ones fill in around it; ties by lead id keep it deterministic.
  clusters.sort((a, b) => b.radius - a.radius || a.lead.localeCompare(b.lead))
  return { spokes: spokes.sort(), clusters }
}

/** How much room a unit needs: its lead, plus the ring of satellites around it. */
function unitRadius(
  lead: string,
  satellites: readonly string[],
  size: Map<string, HubNode>,
  options: HubOptions,
): number {
  const leadRadius = radiusOf(size.get(lead) as HubNode)
  if (satellites.length === 0) return leadRadius
  const ring = ringRadius(lead, satellites, size, options)
  const widest = Math.max(...satellites.map((id) => radiusOf(size.get(id) as HubNode)))
  return ring + widest
}

/**
 * The orbit a set of satellites needs around their lead: far enough that they fit
 * side by side without touching, and never closer than the two cards' own radii.
 */
function ringRadius(
  lead: string,
  satellites: readonly string[],
  size: Map<string, HubNode>,
  options: HubOptions,
): number {
  const { gutter, gap } = options
  const circumference = satellites.reduce(
    (total, id) => total + 2 * radiusOf(size.get(id) as HubNode) + gutter,
    0,
  )
  const packed = circumference / (2 * Math.PI)
  const widest = Math.max(...satellites.map((id) => radiusOf(size.get(id) as HubNode)))
  const touching = radiusOf(size.get(lead) as HubNode) + widest + gap
  return Math.max(packed, touching)
}

/**
 * Place the satellites of one lead around it, over as many orbits as it takes.
 *
 * One crowded ring is what a big fact used to get; past a dozen satellites the ring
 * has to grow so far that the spokes stop being followable, so it spills onto a
 * second orbit instead. `spread` narrows the arc when the unit only owns a wedge
 * rather than the whole circle.
 */
function placeAround(
  lead: string,
  satellites: readonly string[],
  centre: Placed,
  facing: number,
  spread: number,
  size: Map<string, HubNode>,
  options: HubOptions,
  into: Map<string, Placed>,
): void {
  into.set(lead, centre)
  if (satellites.length === 0) return

  const perOrbit = Math.max(1, Math.floor((2 * Math.PI) / Math.max(0.35, 2 * Math.PI / 9)))
  const orbits = Math.ceil(satellites.length / perOrbit)
  let placed = 0
  for (let orbit = 0; orbit < orbits; orbit++) {
    const slice = satellites.slice(placed, placed + perOrbit)
    placed += slice.length
    const radius = ringRadius(lead, slice, size, options) * (1 + orbit * 0.85)
    // one satellite sits on the axis; several fan out symmetrically about it
    const step = slice.length > 1 ? spread / slice.length : 0
    const start = facing - (step * (slice.length - 1)) / 2
    slice.forEach((id, i) => {
      const angle = start + step * i
      into.set(id, { x: centre.x + Math.cos(angle) * radius, y: centre.y + Math.sin(angle) * radius })
    })
  }
}

/**
 * Pull tables that join more than one wedge towards the tables they actually join.
 *
 * A conformed dimension -- one shared by two facts in different clusters -- is placed
 * inside whichever community claimed it, which strands it on one side. Moving it to
 * the centroid of its own neighbours puts it between them, where a reader looks for
 * it. Bounded and deterministic: a few damped passes, ids in sorted order, and the
 * hub and every unit lead stay pinned so the structure does not dissolve.
 */
function relaxBridges(
  ids: readonly string[],
  adjacency: Map<string, Set<string>>,
  pinned: Set<string>,
  places: Map<string, Placed>,
  passes = 6,
  damping = 0.5,
): void {
  const movable = [...ids].filter((id) => !pinned.has(id)).sort()
  for (let pass = 0; pass < passes; pass++) {
    for (const id of movable) {
      const neighbours = [...(adjacency.get(id) ?? new Set<string>())].sort()
      if (neighbours.length < 2) continue
      let x = 0
      let y = 0
      for (const other of neighbours) {
        const at = places.get(other) as Placed
        x += at.x
        y += at.y
      }
      const target = { x: x / neighbours.length, y: y / neighbours.length }
      const here = places.get(id) as Placed
      places.set(id, {
        x: here.x + (target.x - here.x) * damping,
        y: here.y + (target.y - here.y) * damping,
      })
    }
  }
}

/**
 * Coordinates for one constellation: hub in the middle, communities in wedges around
 * it, each wedge led by its own busiest table.
 *
 * Returns CENTRES. Overlap is not guaranteed here -- the caller runs the separation
 * pass, which is what turns "generous by construction" into "never overlapping".
 */
export function hubLayout(
  nodes: readonly HubNode[],
  edges: readonly HubEdge[],
  communities: readonly string[][],
  options: HubOptions,
): Map<string, Placed> {
  const places = new Map<string, Placed>()
  if (nodes.length === 0) return places

  const size = new Map(nodes.map((node) => [node.id, node]))
  const ids = [...size.keys()].sort()
  if (ids.length === 1) {
    places.set(ids[0], { x: 0, y: 0 })
    return places
  }

  const degree = degreesOf(ids, edges)
  const adjacency = adjacencyOf(ids, edges)
  const hub = pickHub(ids, degree)
  places.set(hub, { x: 0, y: 0 })

  const { spokes, clusters } = unitsOf(hub, ids, communities, adjacency, degree, size, options)
  if (spokes.length === 0 && clusters.length === 0) return places

  // The hub's own spokes go in orbits around it, filled nearest-first. One ring is
  // what a big fact used to get, and a ring that has to hold twenty cards has to
  // grow so far that you can no longer follow a spoke without panning -- so each
  // orbit takes what fits and the next one starts a card-pitch further out.
  const hubRadius = radiusOf(size.get(hub) as HubNode)
  placeOrbits(spokes, hubRadius, { x: 0, y: 0 }, size, options, places)

  if (clusters.length === 0) return finish(ids, adjacency, hub, clusters, places)

  // Clusters take wedges of the circle, each sized by the arc it actually needs, on
  // a ring outside whatever the spokes claimed. A cluster is then a PLACE on the
  // map -- which is the whole point of #129 -- and the gap between two wedges is a
  // corridor the straight edges (#130) can run down.
  const arcs = clusters.map((unit) => 2 * unit.radius + options.gutter)
  const total = arcs.reduce((sum, arc) => sum + arc, 0)
  const widest = Math.max(...clusters.map((unit) => unit.radius))
  // The ring is what makes the wedges fit side by side, and nothing more. Pushing it
  // outside the last spoke orbit as well was the first thing tried, and it flings a
  // two-table cluster halfway across the canvas whenever the hub happens to have
  // many spokes -- the clusters end up further from the hub than anything they
  // relate to. Spokes and clusters share the same band instead; the separation pass
  // is what keeps them off each other, and sharing the band is what gives the map
  // the roughly uniform whitespace the hand-tuned reference has.
  const ring = Math.max(total / (2 * Math.PI), hubRadius + widest + options.gap)

  let angle = -Math.PI / 2 // the first wedge starts at the top, as the reference reads
  clusters.forEach((unit, index) => {
    const share = (arcs[index] / total) * 2 * Math.PI
    const facing = angle + share / 2
    angle += share
    const centre = { x: Math.cos(facing) * ring, y: Math.sin(facing) * ring }
    // satellites fan out into the unit's own wedge, never across its neighbour's
    placeAround(unit.lead, unit.satellites, centre, facing, share * 0.9, size, options, places)
  })

  return finish(ids, adjacency, hub, clusters, places)
}

function finish(
  ids: readonly string[],
  adjacency: Map<string, Set<string>>,
  hub: string,
  clusters: readonly Unit[],
  places: Map<string, Placed>,
): Map<string, Placed> {
  // the hub and every cluster lead hold the structure; only the rest may drift
  // Only a lead with satellites of its own is holding a sector together. A
  // one-table "cluster" is not a sub-hub -- it is usually a conformed dimension that
  // community detection could not assign to either side -- and pinning it strands it
  // in whichever wedge it drew, on the far side of the map from both facts that
  // share it. Those are exactly the tables `relaxBridges` exists to move.
  const pinned = new Set<string>([
    hub,
    ...clusters.filter((unit) => unit.satellites.length > 0).map((unit) => unit.lead),
  ])
  relaxBridges(ids, adjacency, pinned, places)
  // anything the community pass never mentioned still needs a coordinate
  for (const id of ids) if (!places.has(id)) places.set(id, { x: 0, y: 0 })
  return places
}

/**
 * Ring the hub's own spokes around it, nearest orbit first.
 *
 * Orbits grow by a card pitch rather than by a multiple: what makes a spoke
 * followable is that it is short, so the twentieth table should sit one card further
 * out than the fourteenth, not twice as far.
 */
function placeOrbits(
  spokes: readonly string[],
  hubRadius: number,
  centre: Placed,
  size: Map<string, HubNode>,
  options: HubOptions,
  into: Map<string, Placed>,
): void {
  if (spokes.length === 0) return
  const { gutter, gap } = options
  const radii = spokes.map((id) => radiusOf(size.get(id) as HubNode))
  const widest = Math.max(...radii)
  const pitch = 2 * widest + gutter
  let radius = hubRadius + widest + gap
  let placed = 0
  let orbit = 0
  while (placed < spokes.length) {
    const arc = 2 * widest + gutter
    const capacity = Math.max(1, Math.floor((2 * Math.PI * radius) / arc))
    const slice = spokes.slice(placed, placed + capacity)
    placed += slice.length
    // Every orbit is spread over the WHOLE circle, part-filled ones included: three
    // leftovers fanned across a quadrant drag the constellation's centroid off the
    // hub, and then the hub is no longer the middle of its own map. Each orbit is
    // rotated half a step so the outer ones sit in the gaps of the inner.
    const step = (2 * Math.PI) / slice.length
    slice.forEach((id, i) => {
      const angle = -Math.PI / 2 + step * i + (orbit % 2 === 0 ? 0 : step / 2)
      into.set(id, { x: centre.x + Math.cos(angle) * radius, y: centre.y + Math.sin(angle) * radius })
    })
    radius += pitch
    orbit += 1
  }
}
