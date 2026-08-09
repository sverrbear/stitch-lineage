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


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _gather_impact(
    diff: ColumnDiff, report: ImpactReport, graph: Graph
) -> tuple[list[str], list[dict]]:
    """Shared data-gathering behind the comment renderers.

    Returns (headline_parts, blocks). Each block describes one changed column:
    {label, suffix, models, cards: [{ref, name, attribution}], dashboards} --
    dashboards holds only impacted dashboards not already implied by a listed card.
    Fully sorted for determinism; renderers only do string layout on top of this.
    """
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

    def build_block(start_id: str, suffix: str) -> dict:
        impacted = report.impacted.get(start_id, [])
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

        cards = []
        implied_dashboards: set[str] = set()
        card_items = sorted(
            (item for item in impacted if item.node_type is NodeType.MB_CARD),
            key=lambda item: card_ref(item.node_id),
        )
        for item in card_items:
            node = nodes_by_id[item.node_id]
            dash_ids = sorted(dashboards_by_card.get(item.node_id, ()))
            dash_names = sorted(nodes_by_id[d].name for d in dash_ids if d in nodes_by_id)
            implied_dashboards.update(dash_ids)
            owner = node.properties.get("creator") or node.owner
            attribution = ", ".join([*dash_names, owner] if owner else dash_names)
            cards.append(
                {"ref": card_ref(item.node_id)[1], "name": node.name, "attribution": attribution}
            )

        extra_dashboards = sorted(
            nodes_by_id[item.node_id].name
            for item in impacted
            if item.node_type is NodeType.MB_DASHBOARD and item.node_id not in implied_dashboards
        )
        return {
            "label": column_label(start_id),
            "suffix": suffix,
            "models": model_names,
            "cards": cards,
            "dashboards": extra_dashboards,
        }

    removed = sorted(diff.removed)
    changed = sorted(diff.type_changed, key=lambda tc: tc.node_id)
    headline = []
    if removed:
        headline.append(f"{_plural(len(removed), 'column')} removed or renamed")
    if changed:
        headline.append(f"{_plural(len(changed), 'column type')} changed")

    blocks = [build_block(start, "→ removed") for start in removed]
    blocks.extend(
        build_block(tc.node_id, f"→ type changed: {tc.old_type or '?'} → {tc.new_type or '?'}")
        for tc in changed
    )
    return headline, blocks


_RENAME_NOTE = "Renames appear as remove+add: a renamed column shows up here as removed."
_NO_IMPACT = "✅ no downstream-impacting column changes"


def format_build_summary(diff: ColumnDiff, report: ImpactReport) -> str | None:
    """One line for the end of `stitch build`: what changed since the previous build.

    None when both graphs carry the same columns -- a build that changed nothing stays
    quiet. Additions are counted but carry no blast radius, so they get no arrow clause.
    """
    changes = []
    if diff.removed:
        changes.append(f"{_plural(len(diff.removed), 'column')} removed")
    if diff.type_changed:
        changes.append(f"{len(diff.type_changed)} type-changed")
    if diff.added:
        changes.append(f"{len(diff.added)} added")
    if not changes:
        return None

    line = f"since last build: {', '.join(changes)}"
    if not (diff.removed or diff.type_changed):
        return line

    # a node reachable from two changed columns is one impacted card, not two
    impacted = {
        item.node_id: item.node_type for items in report.impacted.values() for item in items
    }
    cards = sum(1 for node_type in impacted.values() if node_type is NodeType.MB_CARD)
    dashboards = sum(1 for node_type in impacted.values() if node_type is NodeType.MB_DASHBOARD)
    if not cards:
        reach = "no Metabase cards affected"
    elif dashboards:
        reach = f"{_plural(cards, 'card')} on {_plural(dashboards, 'dashboard')} affected"
    else:
        reach = f"{_plural(cards, 'card')} affected"
    return f"{line} -> {reach} (run 'stitch impact' for the tree)"


def format_github_comment(diff: ColumnDiff, report: ImpactReport, graph: Graph) -> str:
    """Render the PR comment per SPEC.md section 10.

    Markdown: a headline count of removed/renamed and type-changed columns, then per
    changed column a tree of downstream models (derived from the impacted columns'
    parent models), Metabase cards ("#412 Card title  (Dashboard name, owner)") and
    any impacted dashboards not already implied by a listed card. Renames stated as
    remove+add. Empty diff -> a short "no downstream impact" line. Output is fully
    sorted for determinism.
    """
    headline, blocks = _gather_impact(diff, report, graph)
    if not blocks:
        return _NO_IMPACT

    def render_block(block: dict) -> list[str]:
        lines = [f"{block['label']} {block['suffix']}"]
        sections: list[list[str]] = []
        if block["models"]:
            sections.append(
                [
                    f"{_plural(len(block['models']), 'downstream model')}: "
                    f"{', '.join(block['models'])}"
                ]
            )
        if block["cards"]:
            card_lines = []
            for card in block["cards"]:
                line = f"#{card['ref']} {card['name']}"
                if card["attribution"]:
                    line = f"{line}  ({card['attribution']})"
                card_lines.append(line)
            sections.append([f"{_plural(len(block['cards']), 'Metabase card')}:", *card_lines])
        if block["dashboards"]:
            sections.append(
                [
                    f"{_plural(len(block['dashboards']), 'dashboard')}: "
                    f"{', '.join(block['dashboards'])}"
                ]
            )
        if not sections:
            sections.append(["no downstream impact found"])
        for index, section in enumerate(sections):
            glyph = "└" if index == len(sections) - 1 else "├"
            lines.append(f"  {glyph} {section[0]}")
            lines.extend(f"      {sub}" for sub in section[1:])
        return lines

    lines = [f"⚠ {', '.join(headline)}", ""]
    for block in blocks:
        lines.extend(render_block(block))
        lines.append("")
    lines.append(f"_{_RENAME_NOTE}_")
    if report.truncated:
        lines.append(f"_Downstream walk truncated at depth {report.max_depth}._")
    return "\n".join(lines)


def format_slack_comment(diff: ColumnDiff, report: ImpactReport, graph: Graph) -> str:
    """Render the impact report as Slack mrkdwn (same content as the GitHub comment).

    Slack conventions: *bold* headline and column labels, bullet lists instead of
    tree glyphs, card lines "#412 Card title (Dashboard name, owner)". Empty diff
    -> the same short "no downstream impact" line.
    """
    headline, blocks = _gather_impact(diff, report, graph)
    if not blocks:
        return _NO_IMPACT

    lines = [f"*⚠ {', '.join(headline)}*", ""]
    for block in blocks:
        lines.append(f"*{block['label']}* {block['suffix']}")
        if block["models"]:
            lines.append(
                f"• {_plural(len(block['models']), 'downstream model')}: "
                f"{', '.join(block['models'])}"
            )
        if block["cards"]:
            lines.append(f"• {_plural(len(block['cards']), 'Metabase card')}:")
            for card in block["cards"]:
                line = f"    • #{card['ref']} {card['name']}"
                if card["attribution"]:
                    line = f"{line} ({card['attribution']})"
                lines.append(line)
        if block["dashboards"]:
            lines.append(
                f"• {_plural(len(block['dashboards']), 'dashboard')}: "
                f"{', '.join(block['dashboards'])}"
            )
        if not (block["models"] or block["cards"] or block["dashboards"]):
            lines.append("• no downstream impact found")
        lines.append("")
    lines.append(f"_{_RENAME_NOTE}_")
    if report.truncated:
        lines.append(f"_Downstream walk truncated at depth {report.max_depth}._")
    return "\n".join(lines)
