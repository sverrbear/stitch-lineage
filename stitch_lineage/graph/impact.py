"""Impact analysis: diff two graphs, walk the downstream blast radius (SPEC.md section 10)."""

from collections import deque

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import EdgeType, Graph, NodeType


class TypeChange(BaseModel):
    node_id: str
    old_type: str | None = None
    new_type: str | None = None


class ColumnDiff(BaseModel):
    """Column-level changes between two graphs, as column node ids.

    A rename surfaces as removed + added -- name-based ids cannot tell the difference,
    and the conservative reading is correct (the downstream card genuinely breaks
    until repointed). State this in output, never hide it.
    """

    removed: list[str] = Field(default_factory=list)
    type_changed: list[TypeChange] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)


class ImpactedNode(BaseModel):
    """One downstream node, with its shortest path from the start node.

    path is the node id chain from the start node to this node (both inclusive);
    edge_types are the traversed edge types, one per hop, so callers can render the
    chain.
    """

    node_id: str
    node_type: NodeType
    depth: int
    path: list[str] = Field(default_factory=list)
    edge_types: list[EdgeType] = Field(default_factory=list)


class ImpactReport(BaseModel):
    """Downstream fan-out per start node; each list sorted by (depth, node_id).

    truncated is set when the walk hit max_depth with unexplored edges remaining.
    """

    start_node_ids: list[str] = Field(default_factory=list)
    impacted: dict[str, list[ImpactedNode]] = Field(default_factory=dict)
    max_depth: int = 20
    truncated: bool = False


def diff_columns(base: Graph, candidate: Graph) -> ColumnDiff:
    """Compare column nodes between the baseline and candidate graphs.

    removed: column node ids present in base, absent from candidate.
    type_changed: same node id in both, different data_type.
    added: present in candidate only.

    A rename surfaces as removed + added by design: ids are name-based, so a rename
    is indistinguishable from a remove + add, and downstream consumers genuinely
    break either way.
    """
    base_cols = {n.node_id: n.data_type for n in base.nodes if n.node_type is NodeType.COLUMN}
    cand_cols = {n.node_id: n.data_type for n in candidate.nodes if n.node_type is NodeType.COLUMN}
    return ColumnDiff(
        removed=sorted(set(base_cols) - set(cand_cols)),
        added=sorted(set(cand_cols) - set(base_cols)),
        type_changed=[
            TypeChange(node_id=nid, old_type=base_cols[nid], new_type=cand_cols[nid])
            for nid in sorted(set(base_cols) & set(cand_cols))
            if base_cols[nid] != cand_cols[nid]
        ],
    )


def downstream(graph: Graph, start_node_ids: list[str], max_depth: int = 20) -> ImpactReport:
    """Recursive downstream walk over from -> to edges, EXCLUDING relates_to.

    Edge direction is pinned upstream -> downstream (see schema.Edge), so following
    from -> to is following data flow. Depth-capped at max_depth (report.truncated
    flags a hit cap); a node reachable by several routes is deduped to its shortest
    path (BFS first-found, neighbours visited in sorted order for determinism).
    Start ids missing from the graph yield an empty entry, not an error (a
    just-added column has no baseline edges).
    """
    adjacency: dict[str, list[tuple[str, EdgeType]]] = {}
    for edge in graph.edges:
        if edge.edge_type is EdgeType.RELATES_TO:
            continue
        adjacency.setdefault(edge.from_, []).append((edge.to, edge.edge_type))
    for targets in adjacency.values():
        targets.sort()

    nodes_by_id = {node.node_id: node for node in graph.nodes}
    starts = list(dict.fromkeys(start_node_ids))
    report = ImpactReport(start_node_ids=starts, max_depth=max_depth)
    truncated = False

    for start in starts:
        visited = {start}
        found: list[ImpactedNode] = []
        queue: deque[tuple[str, int, list[str], list[EdgeType]]] = deque()
        queue.append((start, 0, [start], []))
        while queue:
            node_id, depth, path, hops = queue.popleft()
            if depth >= max_depth:
                if any(to not in visited for to, _ in adjacency.get(node_id, ())):
                    truncated = True
                continue
            for to, edge_type in adjacency.get(node_id, ()):
                if to in visited:
                    continue
                visited.add(to)
                new_path = [*path, to]
                new_hops = [*hops, edge_type]
                node = nodes_by_id.get(to)
                if node is not None:
                    found.append(
                        ImpactedNode(
                            node_id=to,
                            node_type=node.node_type,
                            depth=depth + 1,
                            path=new_path,
                            edge_types=new_hops,
                        )
                    )
                queue.append((to, depth + 1, new_path, new_hops))
        found.sort(key=lambda item: (item.depth, item.node_id))
        report.impacted[start] = found

    report.truncated = truncated
    return report


def impact_from_graphs(
    base: Graph, candidate: Graph, max_depth: int = 20
) -> tuple[ColumnDiff, ImpactReport]:
    """Diff candidate against base, then walk the BASE graph downstream from every
    removed or type-changed column (spec: baseline edges carry the blast radius)."""
    diff = diff_columns(base, candidate)
    start_ids = [*diff.removed, *(tc.node_id for tc in diff.type_changed)]
    return diff, downstream(base, start_ids, max_depth=max_depth)


def format_github_comment(diff: ColumnDiff, report: ImpactReport, graph: Graph) -> str:
    """Render the PR comment per SPEC.md section 10.

    Markdown: a headline count of removed/renamed and type-changed columns, then per
    changed column a tree of downstream models (derived from the impacted columns'
    parent models), Metabase cards ("#412 Card title  (Dashboard name, owner)") and
    any impacted dashboards not already implied by a listed card. Renames stated as
    remove+add. Empty diff -> a short "no downstream impact" line. Output is fully
    sorted for determinism.
    """
    if not diff.removed and not diff.type_changed:
        return "✅ no downstream-impacting column changes"

    nodes_by_id = {node.node_id: node for node in graph.nodes}
    dashboards_by_card: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_type is EdgeType.APPEARS_ON:
            dashboards_by_card.setdefault(edge.from_, []).append(edge.to)

    def owner_model_name(column_id: str) -> str:
        owner = column_id.rpartition("::")[0]
        owner_node = nodes_by_id.get(owner)
        return owner_node.name if owner_node else owner.rsplit(".", 1)[-1]

    def column_label(node_id: str) -> str:
        owner, sep, column = node_id.rpartition("::")
        if not sep:
            return node_id
        owner_node = nodes_by_id.get(owner)
        owner_name = owner_node.name if owner_node else owner.rsplit(".", 1)[-1]
        return f"{owner_name}.{column}"

    def card_ref(node_id: str) -> tuple[int, str]:
        tail = node_id.rpartition("::")[2]
        return (int(tail), tail) if tail.isdigit() else (1 << 31, tail)

    def plural(count: int, noun: str) -> str:
        return f"{count} {noun}" if count == 1 else f"{count} {noun}s"

    def render_block(start_id: str, suffix: str) -> list[str]:
        impacted = report.impacted.get(start_id, [])
        lines = [f"{column_label(start_id)} {suffix}"]
        sections: list[list[str]] = []

        model_names = sorted(
            {
                owner_model_name(item.node_id)
                for item in impacted
                if item.node_type is NodeType.COLUMN
            }
            | {
                nodes_by_id[item.node_id].name
                for item in impacted
                if item.node_type is NodeType.MODEL
            }
        )
        if model_names:
            sections.append(
                [f"{plural(len(model_names), 'downstream model')}: {', '.join(model_names)}"]
            )

        cards = sorted(
            (item for item in impacted if item.node_type is NodeType.MB_CARD),
            key=lambda item: card_ref(item.node_id),
        )
        implied_dashboards: set[str] = set()
        if cards:
            card_lines = []
            for item in cards:
                node = nodes_by_id[item.node_id]
                dash_ids = sorted(dashboards_by_card.get(item.node_id, ()))
                dash_names = sorted(nodes_by_id[d].name for d in dash_ids if d in nodes_by_id)
                implied_dashboards.update(dash_ids)
                owner = node.properties.get("creator") or node.owner
                attribution = ", ".join([*dash_names, owner] if owner else dash_names)
                line = f"#{card_ref(item.node_id)[1]} {node.name}"
                if attribution:
                    line = f"{line}  ({attribution})"
                card_lines.append(line)
            sections.append([f"{plural(len(cards), 'Metabase card')}:", *card_lines])

        extra_dashboards = sorted(
            nodes_by_id[item.node_id].name
            for item in impacted
            if item.node_type is NodeType.MB_DASHBOARD and item.node_id not in implied_dashboards
        )
        if extra_dashboards:
            sections.append(
                [f"{plural(len(extra_dashboards), 'dashboard')}: {', '.join(extra_dashboards)}"]
            )

        if not sections:
            sections.append(["no downstream impact found"])

        for index, section in enumerate(sections):
            glyph = "└" if index == len(sections) - 1 else "├"
            lines.append(f"  {glyph} {section[0]}")
            lines.extend(f"      {sub}" for sub in section[1:])
        return lines

    removed = sorted(diff.removed)
    changed = sorted(diff.type_changed, key=lambda tc: tc.node_id)
    headline = []
    if removed:
        headline.append(f"{plural(len(removed), 'column')} removed or renamed")
    if changed:
        headline.append(f"{plural(len(changed), 'column type')} changed")

    lines = [f"⚠ {', '.join(headline)}", ""]
    for start in removed:
        lines.extend(render_block(start, "→ removed"))
        lines.append("")
    for tc in changed:
        suffix = f"→ type changed: {tc.old_type or '?'} → {tc.new_type or '?'}"
        lines.extend(render_block(tc.node_id, suffix))
        lines.append("")
    lines.append("_Renames appear as remove+add: a renamed column shows up here as removed._")
    if report.truncated:
        lines.append(f"_Downstream walk truncated at depth {report.max_depth}._")
    return "\n".join(lines)
