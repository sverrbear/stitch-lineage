"""Estate hygiene over graph.json: what nothing consumes (`stitch doctor --dead`).

The mirror image of impact analysis -- the same flow edges, walked the other way.
`impact` asks what breaks if a column changes; this asks what nobody would miss:
columns no card reaches, models feeding nothing, and cards that are archived (or only
shown on archived dashboards) while still holding live columns.

Pure graph analysis over graph.json, so it works offline with no Metabase env vars.
"""

import textwrap
from collections import deque

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import EdgeType, Graph, NodeType

CAVEAT = (
    "stitch only sees Metabase. A column no card reaches may still be read by reverse "
    "ETL, a notebook, ad-hoc SQL or another BI tool, so this is a list of candidates to "
    "review -- not a delete queue."
)

_NO_CARDS = (
    "graph.json holds no Metabase cards, so every column and model below reads as dead "
    "weight. Rebuild with the Metabase side for a meaningful report."
)

_NOTHING = "nothing flagged -- no unconsumed columns, dead models or archived-but-bound cards"

_OWNER_TYPES = (NodeType.MODEL, NodeType.SOURCE)
_CONSUMER_TYPES = (NodeType.MB_CARD, NodeType.MB_DASHBOARD)


class DeadColumnGroup(BaseModel):
    """The unconsumed columns of one owning model or source.

    column_node_ids holds column node ids (not bare names) so the group joins straight
    against nodes.jsonl; owner_columns_total is the owner's full column count, which is
    what tells "3 of 18 unused" apart from "nothing here is used at all".
    """

    owner_id: str
    owner_type: NodeType | None = None
    owner_name: str
    owner_columns_total: int = 0
    column_node_ids: list[str] = Field(default_factory=list)

    @property
    def whole_owner(self) -> bool:
        return len(self.column_node_ids) >= self.owner_columns_total


class DeadModel(BaseModel):
    """A model with no flow path to any card or dashboard."""

    node_id: str
    name: str
    columns_total: int = 0


class ArchivedCardBinding(BaseModel):
    """An archived card still consuming live dbt columns.

    columns are rendered labels ("fct_orders.order_total") so the report formats
    without the graph in hand.
    """

    node_id: str
    card_ref: str
    name: str
    columns: list[str] = Field(default_factory=list)


class ArchivedDashboardCard(BaseModel):
    """A live card whose every dashboard is archived (dashboards: their names)."""

    node_id: str
    card_ref: str
    name: str
    dashboards: list[str] = Field(default_factory=list)


class DeadReport(BaseModel):
    """Counts headline, lists follow -- the shape `stitch build`'s coverage report uses.

    The totals are denominators for the counts: cards_total being 0 means the graph was
    built without the Metabase side and the whole report is vacuous, which the renderer
    says out loud instead of printing an alarming list.
    """

    columns_total: int = 0
    models_total: int = 0
    cards_total: int = 0
    unconsumed_columns: list[DeadColumnGroup] = Field(default_factory=list)
    dead_models: list[DeadModel] = Field(default_factory=list)
    archived_cards_bound: list[ArchivedCardBinding] = Field(default_factory=list)
    cards_only_on_archived_dashboards: list[ArchivedDashboardCard] = Field(default_factory=list)
    caveat: str = CAVEAT

    @property
    def unconsumed_column_count(self) -> int:
        return sum(len(group.column_node_ids) for group in self.unconsumed_columns)

    @property
    def empty(self) -> bool:
        return not (
            self.unconsumed_columns
            or self.dead_models
            or self.archived_cards_bound
            or self.cards_only_on_archived_dashboards
        )


def _owner_id(column_node_id: str) -> str:
    return column_node_id.rpartition("::")[0]


def _id_tail(node_id: str) -> str:
    """The part after '::': a column name, or a card's Metabase id."""
    return node_id.rpartition("::")[2]


def _card_sort_key(node_id: str) -> tuple[int, str]:
    ref = _id_tail(node_id)
    return (int(ref), ref) if ref.isdigit() else (1 << 31, ref)


def feeds_a_consumer(graph: Graph) -> set[str]:
    """Node ids with a flow path to a Metabase card or dashboard.

    Walks the flow edges backwards from every card and dashboard. `relates_to` is a
    declaration rather than a flow (schema.Edge), so it is excluded here exactly as it
    is in impact traversal.

    One hop is walked that no edge carries: from a column to the model or source that
    owns it. A model whose downstream model is consumed is not dead weight even when
    the column lineage between the two went untraced, and without that hop every such
    model reads as dead -- the report's own blind spot would become its loudest
    finding. It does not reach the other way, so a column is judged on real edges only.
    """
    reverse: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_type is EdgeType.RELATES_TO:
            continue
        reverse.setdefault(edge.to, []).append(edge.from_)

    owners = {node.node_id for node in graph.nodes if node.node_type in _OWNER_TYPES}
    for node in graph.nodes:
        if node.node_type is NodeType.COLUMN:
            owner = _owner_id(node.node_id)
            if owner in owners:
                reverse.setdefault(node.node_id, []).append(owner)

    queue = deque(node.node_id for node in graph.nodes if node.node_type in _CONSUMER_TYPES)
    reachable = set(queue)
    while queue:
        for upstream in reverse.get(queue.popleft(), ()):
            if upstream not in reachable:
                reachable.add(upstream)
                queue.append(upstream)
    return reachable


def dead_report(graph: Graph) -> DeadReport:
    """Group everything in the graph that nothing consumes. Fully sorted for determinism."""
    nodes_by_id = {node.node_id: node for node in graph.nodes}
    reachable = feeds_a_consumer(graph)

    columns_by_owner: dict[str, list[str]] = {}
    for node in graph.nodes:
        if node.node_type is NodeType.COLUMN:
            columns_by_owner.setdefault(_owner_id(node.node_id), []).append(node.node_id)

    groups = []
    for owner_id in sorted(columns_by_owner):
        unconsumed = sorted(
            node_id for node_id in columns_by_owner[owner_id] if node_id not in reachable
        )
        if not unconsumed:
            continue
        owner = nodes_by_id.get(owner_id)
        groups.append(
            DeadColumnGroup(
                owner_id=owner_id,
                owner_type=owner.node_type if owner else None,
                owner_name=owner.name if owner else owner_id.rsplit(".", 1)[-1],
                owner_columns_total=len(columns_by_owner[owner_id]),
                column_node_ids=unconsumed,
            )
        )

    models = [node for node in graph.nodes if node.node_type is NodeType.MODEL]
    dead_models = [
        DeadModel(
            node_id=node.node_id,
            name=node.name,
            columns_total=len(columns_by_owner.get(node.node_id, ())),
        )
        for node in sorted(models, key=lambda node: node.node_id)
        if node.node_id not in reachable
    ]

    columns_by_field: dict[str, list[str]] = {}
    fields_by_card: dict[str, list[str]] = {}
    dashboards_by_card: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_type is EdgeType.BINDS_TO:
            columns_by_field.setdefault(edge.to, []).append(edge.from_)
        elif edge.edge_type is EdgeType.CONSUMED_BY:
            fields_by_card.setdefault(edge.to, []).append(edge.from_)
        elif edge.edge_type is EdgeType.APPEARS_ON:
            dashboards_by_card.setdefault(edge.from_, []).append(edge.to)

    def archived(node_id: str) -> bool:
        node = nodes_by_id.get(node_id)
        return bool(node.properties.get("archived")) if node else False

    def column_label(column_id: str) -> str:
        owner = nodes_by_id.get(_owner_id(column_id))
        owner_name = owner.name if owner else _owner_id(column_id).rsplit(".", 1)[-1]
        return f"{owner_name}.{_id_tail(column_id)}"

    cards = sorted(
        (node for node in graph.nodes if node.node_type is NodeType.MB_CARD),
        key=lambda node: _card_sort_key(node.node_id),
    )
    archived_bound = []
    archived_dashboards_only = []
    for card in cards:
        if archived(card.node_id):
            columns = {
                column_id
                for field_id in fields_by_card.get(card.node_id, ())
                for column_id in columns_by_field.get(field_id, ())
            }
            if columns:
                archived_bound.append(
                    ArchivedCardBinding(
                        node_id=card.node_id,
                        card_ref=_id_tail(card.node_id),
                        name=card.name,
                        columns=sorted(column_label(column_id) for column_id in columns),
                    )
                )
            continue
        dashboards = dict.fromkeys(dashboards_by_card.get(card.node_id, ()))
        if dashboards and all(archived(dash_id) for dash_id in dashboards):
            archived_dashboards_only.append(
                ArchivedDashboardCard(
                    node_id=card.node_id,
                    card_ref=_id_tail(card.node_id),
                    name=card.name,
                    dashboards=sorted(
                        nodes_by_id[dash_id].name
                        for dash_id in dashboards
                        if dash_id in nodes_by_id
                    ),
                )
            )

    return DeadReport(
        columns_total=sum(len(ids) for ids in columns_by_owner.values()),
        models_total=len(models),
        cards_total=len(cards),
        unconsumed_columns=groups,
        dead_models=dead_models,
        archived_cards_bound=archived_bound,
        cards_only_on_archived_dashboards=archived_dashboards_only,
    )


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _note(text: str) -> list[str]:
    wrapped = textwrap.wrap(text, width=88)
    return [f"note: {wrapped[0]}", *(f"      {line}" for line in wrapped[1:])]


def format_dead_report(report: DeadReport) -> str:
    """Render the report as plain text: the counts, the caveat, then the lists.

    Printed with typer.echo, never console.print -- card and dashboard names are
    Metabase user input and a title containing square brackets would otherwise be
    eaten as rich markup.
    """
    owner_types = [group.owner_type for group in report.unconsumed_columns]
    spread = ", ".join(
        _plural(count, noun)
        for noun, count in (
            ("model", owner_types.count(NodeType.MODEL)),
            ("source", owner_types.count(NodeType.SOURCE)),
        )
        if count
    )
    columns = f"{report.unconsumed_column_count}/{report.columns_total}"
    lines = [
        f"{'unconsumed columns':<36}{columns}" + (f"   (across {spread})" if spread else ""),
        f"{'models feeding nothing':<36}{len(report.dead_models)}/{report.models_total}",
        f"{'archived cards still bound':<36}{len(report.archived_cards_bound)}",
        f"{'cards only on archived dashboards':<36}{len(report.cards_only_on_archived_dashboards)}",
        "",
    ]
    if not report.cards_total:
        lines.extend([*_note(_NO_CARDS), ""])
    lines.extend([*_note(report.caveat), ""])

    if report.empty:
        lines.append(f"✅ {_NOTHING}")
        return "\n".join(lines)

    if report.unconsumed_columns:
        lines.append(f"unconsumed columns ({report.unconsumed_column_count}):")
        for group in report.unconsumed_columns:
            count = len(group.column_node_ids)
            if group.whole_owner:
                lines.append(f"  {group.owner_id} -- all {_plural(count, 'column')}")
                continue
            lines.append(f"  {group.owner_id} -- {count} of {group.owner_columns_total} columns")
            lines.extend(f"    {_id_tail(node_id)}" for node_id in group.column_node_ids)
        lines.append("")

    if report.dead_models:
        lines.append(f"models feeding nothing ({len(report.dead_models)}):")
        lines.extend(
            f"  {model.node_id} ({_plural(model.columns_total, 'column')})"
            for model in report.dead_models
        )
        lines.append("")

    if report.archived_cards_bound:
        lines.append(f"archived cards still bound ({len(report.archived_cards_bound)}):")
        for card in report.archived_cards_bound:
            held = _plural(len(card.columns), "column")
            lines.append(f"  #{card.card_ref} {card.name} -- {held}")
            lines.extend(f"    {label}" for label in card.columns)
        lines.append("")

    if report.cards_only_on_archived_dashboards:
        count = len(report.cards_only_on_archived_dashboards)
        lines.append(f"cards only on archived dashboards ({count}):")
        lines.extend(
            f"  #{card.card_ref} {card.name} -- {', '.join(card.dashboards)}"
            for card in report.cards_only_on_archived_dashboards
        )
        lines.append("")

    return "\n".join(lines).rstrip()
