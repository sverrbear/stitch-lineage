"""Impact analysis: diff two graphs, walk the downstream blast radius (SPEC.md section 10)."""

from collections import deque

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import EdgeType, Graph, Node, NodeType
from stitch_lineage.graph.search import search


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


# --------------------------------------------------------------------------------------
# Point query (issue #86): the blast radius of ONE column over the CURRENT graph.
#
# The diff path above answers "what did my change break" and needs a committed baseline.
# This answers "what would a change here break" and needs nothing but graph.json -- no
# baseline, no git, no Metabase credentials. Both share the one downstream() walk.
#
# The label helpers below duplicate the closures inside _gather_impact; they are kept
# separate on purpose while PR #56 reworks the diff path, and should be collapsed into
# one set once that lands.
# --------------------------------------------------------------------------------------


class GraphRef(BaseModel):
    """A node named in point-query output: its id, a display label and its type."""

    node_id: str
    label: str
    node_type: NodeType


class BlastCard(BaseModel):
    """One downstream Metabase card, with the dashboards it appears on.

    card_id is the numeric Metabase id parsed out of the node id (None if the id does
    not carry one), so `--json` consumers can build deep links without re-parsing.
    """

    node_id: str
    label: str
    card_id: int | None = None
    dashboards: list[str] = Field(default_factory=list)
    owner: str | None = None


class BlastRadius(BaseModel):
    """Everything downstream of one column, grouped by what it is.

    Every list is deduped and sorted (cards by Metabase id, the rest by label), so the
    output is stable across builds. `dashboards` lists every impacted dashboard,
    including those already named against a card -- this is a summary, not a diff.
    """

    node_id: str
    label: str
    models: list[GraphRef] = Field(default_factory=list)
    columns: list[GraphRef] = Field(default_factory=list)
    fields: list[GraphRef] = Field(default_factory=list)
    cards: list[BlastCard] = Field(default_factory=list)
    dashboards: list[GraphRef] = Field(default_factory=list)
    max_depth: int = 20
    truncated: bool = False


class ColumnLookup(BaseModel):
    """Outcome of resolving a user-typed column reference against the graph.

    Exactly one of three states: `node_id` set (resolved), `candidates` non-empty (the
    reference matched several columns), or neither (nothing matched -- `suggestions`
    then carries search-style hints). `matched_model` names the model when the query
    turned out to be a model rather than a column, so the caller can say so and offer
    that model's columns. Never raises: callers own the error prose.
    """

    query: str
    node_id: str | None = None
    candidates: list[GraphRef] = Field(default_factory=list)
    suggestions: list[GraphRef] = Field(default_factory=list)
    matched_model: str | None = None


def _owner_display(nodes_by_id: dict[str, Node], owner_id: str) -> str:
    owner = nodes_by_id.get(owner_id)
    return owner.name if owner else owner_id.rsplit(".", 1)[-1]


def column_display(nodes_by_id: dict[str, Node], node_id: str) -> str:
    """'model.column' for a column node id; the raw id for anything else."""
    owner_id, sep, column = node_id.rpartition("::")
    if not sep:
        return node_id
    return f"{_owner_display(nodes_by_id, owner_id)}.{column}"


def _card_sort_key(node_id: str) -> tuple[int, str]:
    tail = node_id.rpartition("::")[2]
    return (int(tail), tail) if tail.isdigit() else (1 << 31, tail)


def _node_display(nodes_by_id: dict[str, Node], node: Node) -> str:
    if node.node_type is NodeType.COLUMN:
        return column_display(nodes_by_id, node.node_id)
    return node.name


def _owner_aliases(nodes_by_id: dict[str, Node], owner_id: str) -> set[str]:
    """Every way a user might name the model owning a column, casefolded.

    Covers the dbt unique id ('model.smitten.fct_matches'), its last segment
    ('fct_matches'), the node name, and the warehouse relation ('MARTS.FCT_MATCHES').
    """
    aliases = {owner_id.casefold(), owner_id.rsplit(".", 1)[-1].casefold()}
    owner = nodes_by_id.get(owner_id)
    if owner is not None:
        aliases.add(owner.name.casefold())
        if owner.table:
            aliases.add(owner.table.casefold())
            if owner.schema_:
                aliases.add(f"{owner.schema_}.{owner.table}".casefold())
    return aliases


def resolve_column_ref(graph: Graph, query: str, limit: int = 8) -> ColumnLookup:
    """Resolve a user-typed column reference to exactly one column node.

    Accepts a full column node id ('model.smitten.fct_matches::match_intensity'), the
    'model.column' form (the model part may be the unique id, the model name or
    'SCHEMA.TABLE'), or a bare column name when it is unique in the graph. Matching is
    case-insensitive throughout.

    Several matches -> `candidates`, so the caller can ask for a qualified reference.
    No match -> `suggestions` from graph.search (columns first, falling back to any
    node type), or the model's own columns when the query named a model.
    """
    needle = query.strip()
    lookup = ColumnLookup(query=needle)
    if not needle:
        return lookup

    nodes_by_id = {node.node_id: node for node in graph.nodes}
    columns = [node for node in graph.nodes if node.node_type is NodeType.COLUMN]

    def ref(node_id: str) -> GraphRef:
        node = nodes_by_id.get(node_id)
        return GraphRef(
            node_id=node_id,
            label=_node_display(nodes_by_id, node) if node else node_id,
            node_type=node.node_type if node else NodeType.COLUMN,
        )

    folded = needle.casefold()
    by_node_id = [node for node in columns if node.node_id.casefold() == folded]
    if len(by_node_id) == 1:
        lookup.node_id = by_node_id[0].node_id
        return lookup

    owner_hint, _, column_name = needle.rpartition(".")
    wanted = column_name.casefold()

    def owner_matches(column_id: str) -> bool:
        if not owner_hint:
            return True
        return owner_hint.casefold() in _owner_aliases(nodes_by_id, column_id.rpartition("::")[0])

    matches = [
        node
        for node in columns
        if node.node_id.rpartition("::")[2].casefold() == wanted and owner_matches(node.node_id)
    ]
    if len(matches) == 1:
        lookup.node_id = matches[0].node_id
        return lookup
    if matches:
        lookup.candidates = sorted(
            (ref(node.node_id) for node in matches), key=lambda item: item.label
        )
        return lookup

    model_types = (NodeType.MODEL, NodeType.SOURCE)
    named_model = next(
        (
            node
            for node in graph.nodes
            if node.node_type in model_types
            and folded in (node.node_id.casefold(), node.name.casefold())
        ),
        None,
    )
    if named_model is not None:
        lookup.matched_model = named_model.name
        lookup.suggestions = sorted(
            (
                ref(node.node_id)
                for node in columns
                if node.node_id.rpartition("::")[0] == named_model.node_id
            ),
            key=lambda item: item.label,
        )[:limit]
        return lookup

    term = column_name or needle
    hits = search(graph, term, limit=limit * 4)
    column_hits = [hit for hit in hits if hit.node_type is NodeType.COLUMN]
    lookup.suggestions = [ref(hit.node_id) for hit in (column_hits or hits)[:limit]]
    return lookup


def column_blast_radius(graph: Graph, node_id: str, max_depth: int = 20) -> BlastRadius:
    """Group everything downstream of one node, reusing the impact walk.

    Downstream models come from both model nodes in the walk and the parent models of
    impacted columns -- `feeds` edges run column to column, so a model can be impacted
    without its model node ever being traversed.
    """
    report = downstream(graph, [node_id], max_depth=max_depth)
    impacted = report.impacted.get(node_id, [])
    nodes_by_id = {node.node_id: node for node in graph.nodes}

    dashboards_by_card: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_type is EdgeType.APPEARS_ON:
            dashboards_by_card.setdefault(edge.from_, []).append(edge.to)

    def refs(node_type: NodeType) -> list[GraphRef]:
        found = [
            GraphRef(
                node_id=item.node_id,
                label=_node_display(nodes_by_id, nodes_by_id[item.node_id]),
                node_type=node_type,
            )
            for item in impacted
            if item.node_type is node_type and item.node_id in nodes_by_id
        ]
        return sorted(found, key=lambda item: (item.label, item.node_id))

    model_ids = {
        item.node_id for item in impacted if item.node_type in (NodeType.MODEL, NodeType.SOURCE)
    }
    model_ids.update(
        item.node_id.rpartition("::")[0]
        for item in impacted
        if item.node_type is NodeType.COLUMN and "::" in item.node_id
    )
    models = sorted(
        (
            GraphRef(
                node_id=owner_id,
                label=_owner_display(nodes_by_id, owner_id),
                node_type=(
                    nodes_by_id[owner_id].node_type if owner_id in nodes_by_id else NodeType.MODEL
                ),
            )
            for owner_id in model_ids
        ),
        key=lambda item: (item.label, item.node_id),
    )

    cards = []
    for item in sorted(
        (item for item in impacted if item.node_type is NodeType.MB_CARD),
        key=lambda item: _card_sort_key(item.node_id),
    ):
        node = nodes_by_id.get(item.node_id)
        if node is None:
            continue
        dash_names = sorted(
            nodes_by_id[dash].name
            for dash in dashboards_by_card.get(item.node_id, ())
            if dash in nodes_by_id
        )
        ref_tail = item.node_id.rpartition("::")[2]
        cards.append(
            BlastCard(
                node_id=item.node_id,
                label=node.name,
                card_id=int(ref_tail) if ref_tail.isdigit() else None,
                dashboards=dash_names,
                owner=node.properties.get("creator") or node.owner,
            )
        )

    return BlastRadius(
        node_id=node_id,
        label=column_display(nodes_by_id, node_id),
        models=models,
        columns=refs(NodeType.COLUMN),
        fields=refs(NodeType.MB_FIELD),
        cards=cards,
        dashboards=refs(NodeType.MB_DASHBOARD),
        max_depth=report.max_depth,
        truncated=report.truncated,
    )


def format_blast_radius(radius: BlastRadius) -> str:
    """Render a blast radius as the terminal tree from SPEC.md section 10.

    Counted groups, one per line; long-label groups (columns, cards) list their members
    underneath. Nothing downstream -> a single explicit line, never empty output.
    """
    sections: list[list[str]] = []
    if radius.models:
        names = ", ".join(item.label for item in radius.models)
        sections.append([f"{_plural(len(radius.models), 'downstream model')}: {names}"])
    if radius.columns:
        sections.append(
            [
                f"{_plural(len(radius.columns), 'downstream column')}:",
                *(item.label for item in radius.columns),
            ]
        )
    if radius.fields:
        names = ", ".join(item.label for item in radius.fields)
        sections.append([f"{_plural(len(radius.fields), 'Metabase field')}: {names}"])
    if radius.cards:
        card_lines = []
        for card in radius.cards:
            ref = f"#{card.card_id}" if card.card_id is not None else card.node_id
            attribution = ", ".join([*card.dashboards, *([card.owner] if card.owner else [])])
            line = f"{ref} {card.label}"
            card_lines.append(f"{line}  ({attribution})" if attribution else line)
        sections.append([f"{_plural(len(radius.cards), 'Metabase card')}:", *card_lines])
    if radius.dashboards:
        names = ", ".join(item.label for item in radius.dashboards)
        sections.append([f"{_plural(len(radius.dashboards), 'dashboard')}: {names}"])
    if not sections:
        sections.append(["no downstream impact found"])

    lines = [radius.label]
    for index, section in enumerate(sections):
        glyph = "└" if index == len(sections) - 1 else "├"
        lines.append(f"  {glyph} {section[0]}")
        lines.extend(f"      {sub}" for sub in section[1:])
    if radius.truncated:
        lines.append(f"Downstream walk truncated at depth {radius.max_depth}.")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Machine-readable diff (issue #143): the same answer the PR comment gives, as data.
#
# `stitch mend` recomputes the diff in-process rather than reading this back -- a file
# contract between two commands is a version to keep in step. What this is for is the CI
# gate ("did anything break?" is `columns == []`) and any other consumer that should not
# have to scrape prose or re-parse a node id to find a card.
# --------------------------------------------------------------------------------------


class ImpactedCard(BaseModel):
    """One Metabase card in the machine-readable diff, addressable without re-parsing ids."""

    node_id: str
    card_id: int | None = None
    name: str
    dashboards: list[str] = Field(default_factory=list)
    owner: str | None = None


class ImpactedColumn(BaseModel):
    """One changed column and everything the change reaches, grouped by what it is."""

    node_id: str
    label: str
    change: str
    old_type: str | None = None
    new_type: str | None = None
    models: list[str] = Field(default_factory=list)
    cards: list[ImpactedCard] = Field(default_factory=list)
    dashboards: list[str] = Field(default_factory=list)


class ImpactJson(BaseModel):
    """`stitch impact --format json` -- the diff a machine can act on.

    `added` carries no blast radius by definition and so appears as bare node ids.
    """

    removed: list[str] = Field(default_factory=list)
    type_changed: list[TypeChange] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    columns: list[ImpactedColumn] = Field(default_factory=list)
    card_count: int = 0
    dashboard_count: int = 0
    max_depth: int = 20
    truncated: bool = False


def impact_json(diff: ColumnDiff, report: ImpactReport, graph: Graph) -> ImpactJson:
    """Build the machine-readable impact diff.

    Deliberately built on the same _gather_impact the comment renderers use: a JSON
    consumer that disagreed with the PR comment about what broke would be worse than no
    JSON at all. Fully sorted, like every other renderer here.
    """
    _, blocks = _gather_impact(diff, report, graph)
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    dashboards_by_card: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_type is EdgeType.APPEARS_ON:
            dashboards_by_card.setdefault(edge.from_, []).append(edge.to)

    types = {tc.node_id: tc for tc in diff.type_changed}
    order = [*sorted(diff.removed), *sorted(types)]

    columns: list[ImpactedColumn] = []
    for node_id, block in zip(order, blocks, strict=False):
        change = types.get(node_id)
        cards = [
            ImpactedCard(
                node_id=item.node_id,
                card_id=int(tail) if (tail := item.node_id.rpartition("::")[2]).isdigit() else None,
                name=nodes_by_id[item.node_id].name,
                dashboards=sorted(
                    nodes_by_id[dash].name
                    for dash in dashboards_by_card.get(item.node_id, ())
                    if dash in nodes_by_id
                ),
                owner=nodes_by_id[item.node_id].properties.get("creator")
                or nodes_by_id[item.node_id].owner,
            )
            for item in sorted(
                (
                    item
                    for item in report.impacted.get(node_id, [])
                    if item.node_type is NodeType.MB_CARD and item.node_id in nodes_by_id
                ),
                key=_card_sort_key_of,
            )
        ]
        columns.append(
            ImpactedColumn(
                node_id=node_id,
                label=block["label"],
                change="type_changed" if change else "removed",
                old_type=change.old_type if change else None,
                new_type=change.new_type if change else None,
                models=block["models"],
                cards=cards,
                dashboards=sorted({dash for card in cards for dash in card.dashboards})
                or block["dashboards"],
            )
        )

    impacted = {
        item.node_id: item.node_type for items in report.impacted.values() for item in items
    }
    return ImpactJson(
        removed=sorted(diff.removed),
        type_changed=sorted(diff.type_changed, key=lambda tc: tc.node_id),
        added=sorted(diff.added),
        columns=columns,
        card_count=sum(1 for node_type in impacted.values() if node_type is NodeType.MB_CARD),
        dashboard_count=sum(
            1 for node_type in impacted.values() if node_type is NodeType.MB_DASHBOARD
        ),
        max_depth=report.max_depth,
        truncated=report.truncated,
    )


def _card_sort_key_of(item: ImpactedNode) -> tuple[int, str]:
    return _card_sort_key(item.node_id)
