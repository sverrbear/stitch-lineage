"""Building a mend plan: impact diff + declared renames -> one action per card (SPEC.md §14).

Pure by construction: graphs, raw Metabase payloads and a rename map in, a MendPlan out.
No HTTP, no filesystem, no console -- which is what makes the taxonomy unit-testable
against synthetic cards instead of against someone's live BI estate.

Three rules carry the whole design:

  * Nothing is inferred. A repoint happens only where a human declared `old=new` AND that
    new column resolves to a field Metabase currently has. A declared rename that does not
    resolve does NOT decay into a strip -- it becomes `notify`, because "I cannot find the
    new column" and "the column is gone" call for opposite repairs.
  * One action per card, labelled by the most consequential thing being done. A card
    holding both a renamed and a removed reference is a `strip` whose diff also carries the
    repoint: leaving the dead reference behind to keep the label pure would ship a query
    that does not run.
  * A card reached only through another card is not repaired here. Its dead reference lives
    in the query it sources, so repairing that card is the repair; this one is listed for a
    human and written to by nobody.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from stitch_lineage.graph.impact import ColumnDiff, column_display, resolve_column_ref
from stitch_lineage.graph.schema import EdgeType, Graph, NodeType
from stitch_lineage.mend.models import (
    CardPlan,
    DashcardEdit,
    DeadRef,
    MendAction,
    MendPlan,
)
from stitch_lineage.mend.rewrite import (
    ClauseUse,
    DeadSet,
    rewrite_parameter_mappings,
    rewrite_query,
    scan_query,
)
from stitch_lineage.payloads import MetabasePayload
from stitch_lineage.resolve.metabase import collection_ids_matching, collection_index

_MB_FIELD_PREFIX = "mb_field::"
_MB_CARD_PREFIX = "mb_card::"


# --------------------------------------------------------------------------------------
# graph reading
# --------------------------------------------------------------------------------------


def _field_id(node_id: str) -> int | None:
    tail = node_id.removeprefix(_MB_FIELD_PREFIX)
    return int(tail) if node_id.startswith(_MB_FIELD_PREFIX) and tail.isdigit() else None


def _card_id(node_id: str) -> int | None:
    tail = node_id.removeprefix(_MB_CARD_PREFIX)
    return int(tail) if node_id.startswith(_MB_CARD_PREFIX) and tail.isdigit() else None


def _bound_field_ids(graph: Graph, column_node_id: str) -> list[int]:
    """Metabase field ids a dbt column binds to, in the graph's own words."""
    found = [
        _field_id(edge.to)
        for edge in graph.edges
        if edge.edge_type is EdgeType.BINDS_TO and edge.from_ == column_node_id
    ]
    return sorted({value for value in found if value is not None})


def _column_name(graph: Graph, column_node_id: str) -> str:
    """The warehouse column name a by-name MBQL ref would spell."""
    node = next((n for n in graph.nodes if n.node_id == column_node_id), None)
    if node is not None and node.column:
        return node.column
    return column_node_id.rpartition("::")[2]


def _cards_consuming(graph: Graph, field_ids: set[int]) -> dict[int, list[str]]:
    """card id -> the `via` markers on its edges from the affected fields."""
    cards: dict[int, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_type is not EdgeType.CONSUMED_BY:
            continue
        field_id = _field_id(edge.from_)
        card_id = _card_id(edge.to)
        if field_id is None or card_id is None or field_id not in field_ids:
            continue
        entry = cards.setdefault(card_id, [])
        via = edge.evidence.get("via")
        if isinstance(via, str) and via not in entry:
            entry.append(via)
    return cards


def affected_card_ids(graph: Graph, diff: ColumnDiff) -> list[int]:
    """Every card the removed columns reach, by id.

    The caller uses this to fetch a revision id per card BEFORE planning -- io/ work that
    would make this module impure if it happened inside it. Same edges the plan walks, so
    the two can never disagree about which cards are in play.
    """
    field_ids = {
        field_id for node_id in diff.removed for field_id in _bound_field_ids(graph, node_id)
    }
    return sorted(_cards_consuming(graph, field_ids))


def card_dependencies(graph: Graph) -> dict[int, list[int]]:
    """card id -> the card ids it reads from, from `via` evidence on consumed_by edges."""
    deps: dict[int, set[int]] = {}
    for edge in graph.edges:
        if edge.edge_type is not EdgeType.CONSUMED_BY:
            continue
        card_id = _card_id(edge.to)
        via = edge.evidence.get("via")
        if card_id is None or not isinstance(via, str):
            continue
        upstream = via.removeprefix("card__")
        if upstream.isdigit() and int(upstream) != card_id:
            deps.setdefault(card_id, set()).add(int(upstream))
    return {card: sorted(upstreams) for card, upstreams in deps.items()}


def _depth(
    card_id: int, deps: Mapping[int, list[int]], memo: dict[int, int], seen: set[int]
) -> int:
    """How many card-on-card hops sit upstream of this card. A cycle stops at 0."""
    if card_id in memo:
        return memo[card_id]
    if card_id in seen:
        return 0
    seen.add(card_id)
    depth = 1 + max((_depth(up, deps, memo, seen) for up in deps.get(card_id, [])), default=-1)
    seen.discard(card_id)
    memo[card_id] = depth
    return depth


# --------------------------------------------------------------------------------------
# targets: one per (dead column, way a query can name it)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Target:
    """One way a card's query can name a column that is gone.

    A dbt column yields a target per Metabase field id it bound to, plus one keyed on its
    written name for by-name refs. `key` is what mend/rewrite.py matches on: an int field
    id or a casefolded column name.
    """

    key: Any
    node_id: str
    label: str
    column: str
    field_id: int | None = None
    rename_to: str | None = None
    new_column: str | None = None
    new_field_id: int | None = None
    blocked: str | None = None

    @property
    def renamed(self) -> bool:
        return self.blocked is None and self.rename_to is not None


def _resolve_renames(
    baseline: Graph, candidate: Graph, removed: set[str], renames: Mapping[str, str]
) -> tuple[dict[str, tuple[str, str, int | None]], list[str]]:
    """Match each declared `old=new` to graph nodes and a live target field id.

    Returns (old column node id -> (new label, new column name, new field id), unresolved
    complaints). Every failure mode is a complaint rather than a silent drop, because a
    rename mend cannot see becomes a strip it must not do.
    """
    resolved: dict[str, tuple[str, str, int | None]] = {}
    unresolved: list[str] = []
    for old, new in sorted(renames.items()):
        declared = f"{old}={new}"
        old_lookup = resolve_column_ref(baseline, old)
        if old_lookup.node_id is None:
            unresolved.append(f"{declared}: '{old}' does not name one column in the baseline graph")
            continue
        if old_lookup.node_id not in removed:
            unresolved.append(f"{declared}: '{old}' was not removed by this change")
            continue
        new_lookup = resolve_column_ref(candidate, new)
        if new_lookup.node_id is None:
            unresolved.append(f"{declared}: '{new}' does not name one column in the new graph")
            continue
        field_ids = _bound_field_ids(candidate, new_lookup.node_id)
        if len(field_ids) != 1:
            detail = (
                "is not bound to a Metabase field yet -- sync Metabase and re-plan"
                if not field_ids
                else f"binds to {len(field_ids)} Metabase fields"
            )
            unresolved.append(f"{declared}: '{new}' {detail}")
            continue
        resolved[old_lookup.node_id] = (
            column_display({node.node_id: node for node in candidate.nodes}, new_lookup.node_id),
            _column_name(candidate, new_lookup.node_id),
            field_ids[0],
        )
    return resolved, unresolved


def _targets(
    baseline: Graph,
    candidate: Graph,
    diff: ColumnDiff,
    renames: Mapping[str, str],
) -> tuple[list[_Target], list[str]]:
    """Every dead-column target a card query could carry, plus unresolved-rename notes.

    A removed column named by a rename that did NOT resolve gets `blocked` set: cards
    referencing it are listed for a human instead of being repaired the wrong way.
    """
    removed = set(diff.removed)
    nodes_by_id = {node.node_id: node for node in baseline.nodes}
    resolved, unresolved = _resolve_renames(baseline, candidate, removed, renames)
    blocked_columns = _blocked_columns(baseline, removed, renames, resolved)

    targets: list[_Target] = []
    for node_id in sorted(removed):
        label = column_display(nodes_by_id, node_id)
        column = _column_name(baseline, node_id)
        rename = resolved.get(node_id)
        blocked = blocked_columns.get(node_id)
        keys: list[tuple[Any, int | None]] = [
            (field_id, field_id) for field_id in _bound_field_ids(baseline, node_id)
        ]
        keys.append((column.casefold(), None))
        for key, field_id in keys:
            targets.append(
                _Target(
                    key=key,
                    node_id=node_id,
                    label=label,
                    column=column,
                    field_id=field_id,
                    rename_to=rename[0] if rename else None,
                    new_column=rename[1] if rename else None,
                    new_field_id=rename[2] if rename else None,
                    blocked=blocked,
                )
            )
    return targets, unresolved


def _blocked_columns(
    baseline: Graph,
    removed: set[str],
    renames: Mapping[str, str],
    resolved: Mapping[str, tuple[str, str, int | None]],
) -> dict[str, str]:
    """Removed columns a human tried to rename and mend could not follow.

    Their cards must not be stripped: the operator's intent was a repoint, and stripping
    the clause would delete a column that still exists under another name.
    """
    blocked: dict[str, str] = {}
    for old in sorted(renames):
        lookup = resolve_column_ref(baseline, old)
        node_id = lookup.node_id
        if node_id is None or node_id not in removed or node_id in resolved:
            continue
        blocked[node_id] = (
            f"declared rename '{old}={renames[old]}' did not resolve to a live Metabase field"
        )
    return blocked


# --------------------------------------------------------------------------------------
# payload reading
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Dashcard:
    dashboard_id: int
    dashboard_name: str
    dashcard_id: int
    parameter_mappings: list[dict[str, Any]]


def _dashcards_by_card(payload: MetabasePayload) -> dict[int, list[_Dashcard]]:
    """card id -> its dashcards, so filter wiring can be repaired alongside the query."""
    found: dict[int, list[_Dashcard]] = {}
    for dashboard in payload.dashboards:
        if not isinstance(dashboard, dict) or not isinstance(dashboard.get("id"), int):
            continue
        dashcards = dashboard.get("dashcards")
        if not isinstance(dashcards, list):
            dashcards = dashboard.get("ordered_cards")
        for dashcard in dashcards if isinstance(dashcards, list) else []:
            if not isinstance(dashcard, dict) or not isinstance(dashcard.get("card_id"), int):
                continue
            mappings = dashcard.get("parameter_mappings")
            found.setdefault(dashcard["card_id"], []).append(
                _Dashcard(
                    dashboard_id=dashboard["id"],
                    dashboard_name=str(dashboard.get("name", "")),
                    dashcard_id=dashcard.get("id") if isinstance(dashcard.get("id"), int) else 0,
                    parameter_mappings=mappings if isinstance(mappings, list) else [],
                )
            )
    return found


def _creator(card: Mapping[str, Any]) -> str | None:
    creator = card.get("creator")
    if isinstance(creator, dict):
        name = creator.get("common_name") or creator.get("email")
        if isinstance(name, str):
            return name
    creator_id = card.get("creator_id")
    return str(creator_id) if creator_id is not None else None


# --------------------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------------------


def build_plan(
    baseline: Graph,
    candidate: Graph,
    diff: ColumnDiff,
    payload: MetabasePayload,
    *,
    renames: Mapping[str, str] | None = None,
    auto: Sequence[MendAction] = (),
    notify_only_collections: Sequence[str] = (),
    revisions: Mapping[int, int | None] | None = None,
) -> MendPlan:
    """Classify every card the diff broke, and compute the write that repairs it.

    `baseline` is the graph the change broke (its edges carry the blast radius and the old
    field ids); `candidate` is the graph after it, where a declared rename's new column has
    to be findable and bound. `payload` supplies what no graph carries: each card's live
    `dataset_query`, its `updated_at`, its collection and the dashcards it sits on.

    `revisions` maps card id -> latest revision id, observed by the caller at plan time
    (io/ fetches it; this module stays pure). A card with no revision id still plans: the
    apply loop falls back to restoring the `before` query it carries.
    """
    auto_actions = tuple(auto)
    targets, unresolved = _targets(baseline, candidate, diff, renames or {})
    if not targets:
        return _empty_plan(
            baseline, diff, renames, auto_actions, notify_only_collections, unresolved
        )

    by_key = {target.key: target for target in targets}
    detect = DeadSet(
        field_ids=frozenset(key for key in by_key if isinstance(key, int)),
        names=frozenset(key for key in by_key if isinstance(key, str)),
    )
    affected_fields = {key for key in by_key if isinstance(key, int)}

    cards_by_id = {
        card["id"]: card
        for card in payload.cards
        if isinstance(card, dict) and isinstance(card.get("id"), int)
    }
    notify_only = collection_ids_matching(payload.collections, list(notify_only_collections))
    collections = collection_index(payload.collections)
    dashcards = _dashcards_by_card(payload)
    deps = card_dependencies(baseline)
    graph_cards: dict[int, str] = {}
    for node in baseline.nodes:
        node_card_id = _card_id(node.node_id)
        if node.node_type is NodeType.MB_CARD and node_card_id is not None:
            graph_cards[node_card_id] = node.name

    entries: list[CardPlan] = []
    for card_id, via in sorted(_cards_consuming(baseline, affected_fields).items()):
        card = cards_by_id.get(card_id)
        if card is None:
            continue
        if bool(card.get("archived", False)):
            continue  # already archived: dead weight is `doctor --dead`, not mend
        entry = _plan_card(
            card=card,
            card_id=card_id,
            name=str(card.get("name", "")) or graph_cards.get(card_id) or f"card {card_id}",
            via=via,
            by_key=by_key,
            detect=detect,
            collection=_collection_path(collections, card.get("collection_id")),
            notify_only=card.get("collection_id") in notify_only,
            owner=_creator(card),
            dashcards=dashcards.get(card_id, []),
            revision_id=(revisions or {}).get(card_id),
            depends_on=deps.get(card_id, []),
            auto=auto_actions,
        )
        entries.append(entry)

    memo: dict[int, int] = {}
    entries.sort(key=lambda entry: (_depth(entry.card_id, deps, memo, set()), entry.card_id))
    return MendPlan(
        renames=dict(sorted((renames or {}).items())),
        auto=list(auto_actions),
        notify_only_collections=list(notify_only_collections),
        removed_columns=_removed_labels(baseline, diff),
        unresolved_renames=unresolved,
        cards=entries,
    )


def _empty_plan(
    baseline: Graph,
    diff: ColumnDiff,
    renames: Mapping[str, str] | None,
    auto: Sequence[MendAction],
    notify_only_collections: Sequence[str],
    unresolved: list[str],
) -> MendPlan:
    return MendPlan(
        renames=dict(sorted((renames or {}).items())),
        auto=list(auto),
        notify_only_collections=list(notify_only_collections),
        removed_columns=_removed_labels(baseline, diff),
        unresolved_renames=unresolved,
    )


def _removed_labels(baseline: Graph, diff: ColumnDiff) -> list[str]:
    nodes_by_id = {node.node_id: node for node in baseline.nodes}
    return [column_display(nodes_by_id, node_id) for node_id in sorted(diff.removed)]


def _collection_path(collections: Mapping[Any, dict[str, Any]], collection_id: Any) -> str | None:
    entry = collections.get(collection_id)
    path = str(entry.get("path", "")) if entry else ""
    return path or None


def _plan_card(
    *,
    card: Mapping[str, Any],
    card_id: int,
    name: str,
    via: list[str],
    by_key: Mapping[Any, _Target],
    detect: DeadSet,
    collection: str | None,
    notify_only: bool,
    owner: str | None,
    dashcards: Sequence[_Dashcard],
    revision_id: int | None,
    depends_on: list[int],
    auto: Sequence[MendAction],
) -> CardPlan:
    """Classify and compute the repair of exactly one card."""
    query = card.get("dataset_query")
    uses: dict[Any, list[ClauseUse]] = scan_query(query, detect)

    def entry(
        action: MendAction,
        reason: str,
        *,
        downgraded_from: MendAction | None = None,
        after: dict[str, Any] | None = None,
        repointed: Sequence[str] = (),
        removed: Sequence[str] = (),
        archive: bool = False,
        edits: Sequence[DashcardEdit] = (),
        refs: Sequence[DeadRef] = (),
    ) -> CardPlan:
        return CardPlan(
            card_id=card_id,
            name=name,
            action=action,
            reason=reason,
            collection=collection,
            owner=owner,
            dashboards=sorted({dashcard.dashboard_name for dashcard in dashcards}),
            updated_at=_updated_at(card),
            revision_id=revision_id,
            dead_refs=list(refs),
            repointed=list(repointed),
            removed_clauses=list(removed),
            before=query if isinstance(query, dict) else None,
            after=after,
            archive=archive,
            dashcards=list(edits),
            downgraded_from=downgraded_from,
            depends_on=depends_on,
        )

    if not uses:
        # A card the graph says is affected but whose clauses hold no dead ref is either
        # unrewritable (native SQL: the reference is in text this feature does not touch) or
        # reached through another card. Both are notify, and the difference is the whole
        # message a human needs, so it is never collapsed into one line.
        unsupported = rewrite_query(query, detect).unsupported
        if unsupported is not None:
            return entry(MendAction.NOTIFY, unsupported)
        source = ", ".join(f"#{marker.removeprefix('card__')}" for marker in sorted(via))
        reason = (
            f"reached only through card {source} -- repairing that card is the repair"
            if source
            else "no reference to a removed column in this card's own query"
        )
        return entry(MendAction.NOTIFY, reason)

    refs = _dead_refs(uses, by_key)
    blocked = [by_key[key].blocked for key in uses if by_key[key].blocked]
    if blocked:
        return entry(MendAction.NOTIFY, blocked[0], refs=refs)

    renamed = [key for key in uses if by_key[key].renamed]
    orphans = [key for key in uses if not by_key[key].renamed]
    essential = [key for key in orphans if any(use.essential for use in uses[key])]

    if essential:
        columns = ", ".join(sorted({by_key[key].label for key in essential}))
        clauses = ", ".join(
            sorted({use.label for key in essential for use in uses[key] if use.essential})
        )
        action = MendAction.ARCHIVE
        reason = f"{columns} is essential to this card ({clauses})"
        computed = entry(action, reason, archive=True, refs=refs)
    else:
        action = MendAction.STRIP if orphans else MendAction.REPOINT
        dead = DeadSet(
            field_ids=frozenset(key for key in orphans if isinstance(key, int)),
            names=frozenset(key for key in orphans if isinstance(key, str)),
            field_map={
                key: by_key[key].new_field_id
                for key in renamed
                if isinstance(key, int) and by_key[key].new_field_id is not None
            },
            name_map={
                key: by_key[key].new_column
                for key in renamed
                if isinstance(key, str) and by_key[key].new_column
            },
            # so the notice says "removed filter -> fct_orders.promo_code" rather than
            # naming a field id nobody can look up from Slack
            labels={key: by_key[key].label for key in uses},
            rename_labels={key: by_key[key].rename_to for key in renamed if by_key[key].rename_to},
        )
        result = rewrite_query(query, dead)
        if result.unsupported is not None:
            return entry(MendAction.NOTIFY, result.unsupported, refs=refs)
        if not result.changed:
            return entry(
                MendAction.NOTIFY,
                "nothing to rewrite -- the reference is not in a clause mend can repair",
                refs=refs,
            )
        edits = _dashcard_edits(dashcards, dead)
        columns = ", ".join(sorted({by_key[key].label for key in uses}))
        reason = (
            f"{columns} repointed to its declared new column"
            if action is MendAction.REPOINT
            else f"{columns} removed from clauses that are not what the card is about"
        )
        computed = entry(
            action,
            reason,
            after=result.query,
            repointed=result.repointed,
            removed=result.removed,
            edits=edits,
            refs=refs,
        )

    if notify_only:
        return entry(
            MendAction.NOTIFY,
            f"{computed.reason}; collection is notify-only",
            downgraded_from=action,
            refs=refs,
        )
    if action not in auto:
        return entry(
            MendAction.NOTIFY,
            f"{computed.reason}; '{action.value}' is not in mend.auto",
            downgraded_from=action,
            refs=refs,
        )
    return computed


def _updated_at(card: Mapping[str, Any]) -> str | None:
    value = card.get("updated_at")
    return value if isinstance(value, str) else None


def _dead_refs(uses: Mapping[Any, list[ClauseUse]], by_key: Mapping[Any, _Target]) -> list[DeadRef]:
    """One DeadRef per dead COLUMN (not per way the query named it), clauses merged.

    `essential` is only ever set for a column actually being LOST. Essentialness answers
    "can this card survive without this column", which is not a question about a column that
    is merely moving to a new name.
    """
    merged: dict[str, DeadRef] = {}
    for key in sorted(uses, key=str):
        target = by_key[key]
        ref = merged.get(target.node_id)
        if ref is None:
            ref = DeadRef(
                column=target.label,
                node_id=target.node_id,
                field_id=target.field_id,
                rename_to=target.rename_to,
                new_field_id=target.new_field_id,
            )
            merged[target.node_id] = ref
        if ref.field_id is None and target.field_id is not None:
            ref.field_id = target.field_id
        for use in uses[key]:
            if use.label not in ref.clauses:
                ref.clauses.append(use.label)
            ref.essential = ref.essential or (use.essential and not target.renamed)
    for ref in merged.values():
        ref.clauses.sort()
    return [merged[node_id] for node_id in sorted(merged)]


def _dashcard_edits(dashcards: Sequence[_Dashcard], dead: DeadSet) -> list[DashcardEdit]:
    """Dashcards whose filter wiring changes, one entry per dashcard actually affected."""
    edits: list[DashcardEdit] = []
    for dashcard in sorted(dashcards, key=lambda item: (item.dashboard_id, item.dashcard_id)):
        after, notes = rewrite_parameter_mappings(dashcard.parameter_mappings, dead)
        if not notes or after == dashcard.parameter_mappings:
            continue
        edits.append(
            DashcardEdit(
                dashboard_id=dashcard.dashboard_id,
                dashboard_name=dashcard.dashboard_name,
                dashcard_id=dashcard.dashcard_id,
                before=list(dashcard.parameter_mappings),
                after=after,
            )
        )
    return edits
