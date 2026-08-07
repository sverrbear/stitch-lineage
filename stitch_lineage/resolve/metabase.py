"""Resolve raw Metabase payloads into graph nodes and edges (SPEC.md section 7.4)."""

from collections.abc import Callable
from fnmatch import fnmatch
from typing import Any

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import (
    Confidence,
    Edge,
    EdgeType,
    Node,
    NodeType,
    mb_card_node_id,
    mb_dashboard_node_id,
    mb_field_node_id,
)
from stitch_lineage.payloads import MetabasePayload

_CLAUSE_KEYS = ("fields", "breakout", "aggregation", "filter", "expressions", "order-by")


class MetabaseResolution(BaseModel):
    """Output of resolve_metabase: the Metabase side of the graph plus coverage counters.

    Coverage fields map 1:1 onto graph.schema.Coverage; the CLI copies them over.
    unresolved_field_refs itemizes the field refs behind unresolved_cards entries
    (card_id, the raw ref, a reason) for `stitch doctor`.
    """

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    mbql_cards_resolved: int = 0
    mbql_cards_total: int = 0
    native_cards_resolved: int = 0
    native_cards_total: int = 0
    dashboards: int = 0
    dashboards_total: int = 0
    unresolved_cards: list[int] = Field(default_factory=list)
    unresolved_field_refs: list[dict[str, Any]] = Field(default_factory=list)


def _collection_paths(collections: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    """Index collections by id, each with its full name path ("Archive 2020/Old dashboards")."""
    by_id = {col.get("id"): col for col in collections if isinstance(col, dict)}
    indexed: dict[Any, dict[str, Any]] = {}
    for col_id, col in by_id.items():
        ancestors = []
        location = col.get("location")
        if isinstance(location, str):
            for part in location.strip("/").split("/"):
                if part.isdigit():
                    ancestor = by_id.get(int(part))
                    if ancestor is not None:
                        ancestors.append(str(ancestor.get("name", "")))
        name = str(col.get("name", ""))
        indexed[col_id] = {
            "name": name,
            "path": "/".join([*ancestors, name]),
            "personal_owner_id": col.get("personal_owner_id"),
        }
    return indexed


def _excluded_collection_ids(
    collections: list[dict[str, Any]], patterns: list[str]
) -> set[Any]:
    excluded: set[Any] = set()
    for col_id, info in _collection_paths(collections).items():
        is_personal = info["personal_owner_id"] is not None
        for pattern in patterns:
            if (
                fnmatch(info["name"], pattern)
                or fnmatch(info["path"], pattern)
                or (is_personal and fnmatch("Personal", pattern))
            ):
                excluded.add(col_id)
                break
    return excluded


def _card_source_id(value: Any) -> int | None:
    """"card__123" -> 123, anything else -> None."""
    if isinstance(value, str) and value.startswith("card__"):
        suffix = value.removeprefix("card__")
        if suffix.isdigit():
            return int(suffix)
    return None


def _collect_refs(
    node: Any, clause: str, stage: str, refs: list[tuple[Any, str, bool, str]]
) -> None:
    """Recursively collect ["field", id_or_name, opts] refs with the clause they sit in
    and the query stage (the prefix) that owns them.

    stage travels with the ref because join aliases are scoped to their query stage:
    the same alias may name a different table in a nested source-query.

    opts["source-field"] (implicit join through an FK) is emitted as an extra ref
    flagged is_source_field=True -- suggestion-layer input per SPEC.md section 7.4.
    """
    if isinstance(node, list):
        if len(node) >= 2 and node[0] == "field":
            refs.append((node, clause, False, stage))
            opts = node[2] if len(node) > 2 else None
            if isinstance(opts, dict) and opts.get("source-field") is not None:
                refs.append((["field", opts["source-field"], None], clause, True, stage))
            return
        for item in node:
            _collect_refs(item, clause, stage, refs)
    elif isinstance(node, dict):
        for value in node.values():
            _collect_refs(value, clause, stage, refs)


def _walk_query(
    query: dict[str, Any],
    prefix: str,
    refs: list[tuple[Any, str, bool, str]],
    upstream_cards: list[int],
    join_aliases: dict[str, dict[str, Any]],
) -> None:
    aliases = join_aliases.setdefault(prefix, {})
    upstream = _card_source_id(query.get("source-table"))
    if upstream is not None:
        upstream_cards.append(upstream)
    for key in _CLAUSE_KEYS:
        if key in query:
            _collect_refs(query[key], f"{prefix}{key}", prefix, refs)
    joins = query.get("joins")
    for join in joins if isinstance(joins, list) else []:
        if not isinstance(join, dict):
            continue
        if isinstance(join.get("alias"), str):
            aliases.setdefault(join["alias"], join.get("source-table"))
        join_upstream = _card_source_id(join.get("source-table"))
        if join_upstream is not None:
            upstream_cards.append(join_upstream)
        if "condition" in join:
            _collect_refs(join["condition"], f"{prefix}joins.condition", prefix, refs)
        if isinstance(join.get("fields"), list):
            _collect_refs(join["fields"], f"{prefix}joins.fields", prefix, refs)
        if isinstance(join.get("source-query"), dict):
            _walk_query(
                join["source-query"],
                f"{prefix}joins.source-query.",
                refs,
                upstream_cards,
                join_aliases,
            )
    if isinstance(query.get("source-query"), dict):
        _walk_query(
            query["source-query"], f"{prefix}source-query.", refs, upstream_cards, join_aliases
        )


def _source_context(query: dict[str, Any]) -> tuple[str, Any] | None:
    """Innermost source of a query: ("table", table_id) or ("card", card_id)."""
    while isinstance(query.get("source-query"), dict):
        query = query["source-query"]
    source = query.get("source-table")
    card_id = _card_source_id(source)
    if card_id is not None:
        return ("card", card_id)
    if isinstance(source, int):
        return ("table", source)
    return None


class _CardWalk(BaseModel):
    """Per-card walk result: direct field consumption, upstream card refs, join
    aliases (query stage -> {alias: source-table}), failures."""

    consumed: dict[int, dict[str, Any]] = Field(default_factory=dict)
    upstream_cards: list[int] = Field(default_factory=list)
    join_aliases: dict[str, dict[str, Any]] = Field(default_factory=dict)
    problems: list[dict[str, Any]] = Field(default_factory=list)


def _resolve_by_name(
    name: str,
    opts: Any,
    context: tuple[str, Any] | None,
    field_ids: set[int],
    table_columns: dict[int, dict[str, int]],
    cards_by_id: dict[int, dict[str, Any]],
    walks: dict[int, _CardWalk],
    fields_by_id: dict[int, dict[str, Any]],
    join_aliases: dict[str, Any],
) -> tuple[int | None, bool]:
    """Resolve a by-name field ref; returns (field_id, exact).

    join_aliases is the alias map of the ref's own query stage, not the whole card:
    a nested source-query may reuse an outer alias for a different table.

    exact is True only for deterministic lookups: the ref's join-alias mapped to a
    joined table's metadata, the source table's metadata map, or the upstream card's
    result_metadata. The upstream-card consumed-fields name match is a heuristic ->
    exact=False (rendered as confidence `parsed`); an unmappable join-alias downgrades
    whatever fallback resolves the name, for the same reason.
    """
    exact = True
    join_alias = opts.get("join-alias") if isinstance(opts, dict) else None
    if join_alias is not None:
        source = join_aliases.get(join_alias)
        if isinstance(source, int):
            field_id = table_columns.get(source, {}).get(name.lower())
            return (field_id if field_id in field_ids else None), True
        exact = False
    if context is None:
        return None, exact
    kind, source_id = context
    candidates: list[int] = []
    if kind == "table":
        field_id = table_columns.get(source_id, {}).get(name.lower())
        if field_id is not None:
            candidates = [field_id]
    else:
        card = cards_by_id.get(source_id)
        result_metadata = card.get("result_metadata") if isinstance(card, dict) else None
        if isinstance(result_metadata, list):
            for column in result_metadata:
                if not isinstance(column, dict) or column.get("name") != name:
                    continue
                ref = column.get("field_ref")
                if isinstance(ref, list) and len(ref) >= 2 and isinstance(ref[1], int):
                    candidates.append(ref[1])
        if not candidates and source_id in walks:
            exact = False
            candidates = sorted(
                {
                    field_id
                    for field_id in walks[source_id].consumed
                    if fields_by_id.get(field_id, {}).get("name", "").lower() == name.lower()
                }
            )
    if len(candidates) == 1 and candidates[0] in field_ids:
        return candidates[0], exact
    return None, exact


def _record(consumed: dict[int, dict[str, Any]], field_id: int, clause: str, **flags: bool) -> None:
    entry = consumed.setdefault(field_id, {"clauses": set()})
    entry["clauses"].add(clause)
    for flag, value in flags.items():
        if value:
            entry[flag] = True


def resolve_metabase(
    payload: MetabasePayload,
    exclude_collections: list[str],
    include_schemas: list[str] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> MetabaseResolution:
    """Build the Metabase side of the graph from raw API payloads.

    Produces:
      * mb_field Nodes (via schema.mb_field_node_id) from database_metadata, carrying
        database/schema/table/column so resolve.bind can match them to dbt models.
      * mb_card / mb_dashboard Nodes (mb_card_node_id / mb_dashboard_node_id) with
        collection_id, creator and archived in properties.
      * `consumed_by` edges (mb_field -> mb_card): walk MBQL dataset_query.query over
        fields, breakout, aggregation, filter, expressions, joins[].condition,
        joins[].fields and order-by; every ["field", <id>, opts] resolves through the
        metadata map, confidence exact. By-name string refs resolve exactly through a
        mapped join-alias, the source table's metadata or the upstream card's
        result_metadata; the upstream-card consumed-fields name match (and any ref
        whose join-alias cannot be mapped to its joined table) is a heuristic and
        ships confidence `parsed`. Card-on-card (source-table "card__123", Metabase
        models/metrics) resolves transitively, cycle-guarded with a visited set.
        Native SQL cards are Phase 3: count them in native_cards_total, resolve
        none, add their ids to unresolved_cards -- never drop a card silently.
      * `appears_on` edges (mb_card -> mb_dashboard) from dashcards, confidence exact.
        A dashboard counts as resolved in coverage only when every non-virtual
        dashcard points at a known in-scope card.

    exclude_collections are glob patterns (e.g. "Personal*") matched against collection
    names and full paths (personal collections also match "Personal*" via
    personal_owner_id); cards/dashboards in excluded collections are skipped entirely
    and count in no total.

    include_schemas (config metabase.include_schemas) limits mb_field/table scope to
    the named schemas, compared case-insensitively; empty/None includes every schema.

    on_progress, when given, is called as on_progress(done, total) after each in-scope
    card is resolved; total is the in-scope card count, fixed for the whole run.

    Pure: payload in, nodes/edges out. No filesystem or network access.
    """
    result = MetabaseResolution()
    excluded = _excluded_collection_ids(payload.collections, exclude_collections)
    included_schemas = {schema.casefold() for schema in include_schemas or []}

    db_names = {
        db.get("id"): str(db.get("name", "")) for db in payload.databases if isinstance(db, dict)
    }
    fields_by_id: dict[int, dict[str, Any]] = {}
    table_columns: dict[int, dict[str, int]] = {}
    for db_id, metadata in payload.database_metadata.items():
        db_name = db_names.get(db_id, str(metadata.get("name", "")))
        tables = metadata.get("tables")
        for table in tables if isinstance(tables, list) else []:
            if not isinstance(table, dict):
                continue
            if included_schemas and str(table.get("schema", "")).casefold() not in included_schemas:
                continue
            columns: dict[str, int] = {}
            fields = table.get("fields")
            for field in fields if isinstance(fields, list) else []:
                if not isinstance(field, dict) or not isinstance(field.get("id"), int):
                    continue
                field_id = field["id"]
                fields_by_id[field_id] = field
                column_name = str(field.get("name", ""))
                columns[column_name.lower()] = field_id
                properties = {
                    key: field[value]
                    for key, value in (
                        ("semantic_type", "semantic_type"),
                        ("fk_target_field_id", "fk_target_field_id"),
                        ("visibility", "visibility_type"),
                    )
                    if field.get(value) is not None
                }
                result.nodes.append(
                    Node(
                        node_id=mb_field_node_id(field_id),
                        node_type=NodeType.MB_FIELD,
                        name=str(field.get("display_name") or column_name),
                        database=db_name,
                        schema_=table.get("schema"),
                        table=table.get("name"),
                        column=column_name,
                        data_type=field.get("base_type"),
                        description=field.get("description"),
                        properties=properties,
                    )
                )
            if isinstance(table.get("id"), int):
                table_columns[table["id"]] = columns
    field_ids = set(fields_by_id)

    cards_in_scope: list[dict[str, Any]] = []
    cards_by_id: dict[int, dict[str, Any]] = {}
    for card in payload.cards:
        if not isinstance(card, dict) or not isinstance(card.get("id"), int):
            continue
        if card.get("collection_id") in excluded:
            continue
        cards_in_scope.append(card)
        cards_by_id[card["id"]] = card

    walks: dict[int, _CardWalk] = {}
    deferred: list[tuple[int, Any, str, bool, tuple[str, Any] | None, str]] = []
    for card in cards_in_scope:
        card_id = card["id"]
        dataset_query = card.get("dataset_query")
        query = dataset_query.get("query") if isinstance(dataset_query, dict) else None
        if not isinstance(query, dict):
            continue
        walk = _CardWalk()
        refs: list[tuple[Any, str, bool, str]] = []
        _walk_query(query, "", refs, walk.upstream_cards, walk.join_aliases)
        context = _source_context(query)
        for ref, clause, is_source_field, stage in refs:
            target = ref[1]
            if isinstance(target, int):
                if target in field_ids:
                    _record(walk.consumed, target, clause, implicit_join=is_source_field)
                else:
                    walk.problems.append(
                        {"card_id": card_id, "ref": ref, "reason": "unknown field id"}
                    )
            elif isinstance(target, str):
                deferred.append((card_id, ref, clause, is_source_field, context, stage))
            else:
                walk.problems.append({"card_id": card_id, "ref": ref, "reason": "malformed ref"})
        walks[card_id] = walk

    # by-name refs resolve after every card is walked, so card-context lookups do not
    # depend on card ordering in /api/card
    for card_id, ref, clause, is_source_field, context, stage in deferred:
        opts = ref[2] if len(ref) > 2 else None
        resolved, exact = _resolve_by_name(
            ref[1],
            opts,
            context,
            field_ids,
            table_columns,
            cards_by_id,
            walks,
            fields_by_id,
            walks[card_id].join_aliases.get(stage, {}),
        )
        if resolved is not None:
            walk = walks[card_id]
            _record(
                walk.consumed,
                resolved,
                clause,
                implicit_join=is_source_field,
                by_name=True,
                parsed=not exact,
            )
        else:
            walks[card_id].problems.append(
                {"card_id": card_id, "ref": ref, "reason": "unresolvable field name"}
            )

    transitive: dict[int, set[int]] = {}

    def _transitive_fields(card_id: int, visiting: set[int]) -> tuple[set[int], bool]:
        """Fields consumed by a card and everything upstream of it, plus whether the
        walk was cut short by a cycle.

        A cycled result is only complete relative to the walk that produced it (the
        back-edge contributes nothing), so it is never memoized: a third card sourcing
        a card inside an A<->B cycle would otherwise inherit the truncated set.
        """
        if card_id in transitive:
            return transitive[card_id], False
        if card_id in visiting:
            return set(), True
        visiting.add(card_id)
        walk = walks.get(card_id)
        consumed = set(walk.consumed) if walk else set()
        cycled = False
        for upstream_id in walk.upstream_cards if walk else []:
            upstream_fields, upstream_cycled = _transitive_fields(upstream_id, visiting)
            consumed |= upstream_fields
            cycled = cycled or upstream_cycled
        visiting.discard(card_id)
        if not cycled:
            transitive[card_id] = consumed
        return consumed, cycled

    def _resolve_card(card: dict[str, Any]) -> None:
        card_id = card["id"]
        card_node_id = mb_card_node_id(card_id)
        dataset_query = card.get("dataset_query")
        query_type = dataset_query.get("type") if isinstance(dataset_query, dict) else None
        creator = card.get("creator")
        creator_name = (
            creator.get("common_name") or creator.get("email")
            if isinstance(creator, dict)
            else card.get("creator_id")
        )
        result.nodes.append(
            Node(
                node_id=card_node_id,
                node_type=NodeType.MB_CARD,
                name=str(card.get("name", "")),
                description=card.get("description"),
                properties={
                    "archived": bool(card.get("archived", False)),
                    "collection_id": card.get("collection_id"),
                    "creator": creator_name,
                    "display": card.get("display"),
                    "query_type": query_type,
                },
            )
        )

        if query_type == "native":
            result.native_cards_total += 1
            result.unresolved_cards.append(card_id)
            return

        result.mbql_cards_total += 1
        walk = walks.get(card_id)
        if walk is None:
            result.unresolved_cards.append(card_id)
            result.unresolved_field_refs.append(
                {"card_id": card_id, "ref": None, "reason": "no MBQL query in dataset_query"}
            )
            return

        for field_id in sorted(walk.consumed):
            entry = walk.consumed[field_id]
            evidence: dict[str, Any] = {"clauses": sorted(entry["clauses"])}
            for flag in ("implicit_join", "by_name"):
                if entry.get(flag):
                    evidence[flag] = True
            result.edges.append(
                Edge(
                    from_=mb_field_node_id(field_id),
                    to=card_node_id,
                    edge_type=EdgeType.CONSUMED_BY,
                    confidence=Confidence.PARSED if entry.get("parsed") else Confidence.EXACT,
                    evidence=evidence,
                )
            )

        problems = list(walk.problems)
        emitted = set(walk.consumed)
        for upstream_id in dict.fromkeys(walk.upstream_cards):
            if upstream_id not in walks:
                reason = (
                    "referenced card is native or has no MBQL query"
                    if upstream_id in cards_by_id
                    else "referenced card missing or excluded"
                )
                problems.append(
                    {"card_id": card_id, "ref": f"card__{upstream_id}", "reason": reason}
                )
                continue
            upstream_fields, _ = _transitive_fields(upstream_id, set())
            for field_id in sorted(upstream_fields - emitted):
                emitted.add(field_id)
                result.edges.append(
                    Edge(
                        from_=mb_field_node_id(field_id),
                        to=card_node_id,
                        edge_type=EdgeType.CONSUMED_BY,
                        confidence=Confidence.EXACT,
                        evidence={"via": f"card__{upstream_id}"},
                    )
                )

        if problems:
            result.unresolved_cards.append(card_id)
            result.unresolved_field_refs.extend(problems)
        else:
            result.mbql_cards_resolved += 1

    for done, card in enumerate(cards_in_scope, start=1):
        _resolve_card(card)
        if on_progress is not None:
            on_progress(done, len(cards_in_scope))

    card_node_ids = {mb_card_node_id(card["id"]) for card in cards_in_scope}
    for dashboard in payload.dashboards:
        if not isinstance(dashboard, dict) or not isinstance(dashboard.get("id"), int):
            continue
        if dashboard.get("collection_id") in excluded:
            continue
        result.dashboards_total += 1
        dash_node_id = mb_dashboard_node_id(dashboard["id"])
        result.nodes.append(
            Node(
                node_id=dash_node_id,
                node_type=NodeType.MB_DASHBOARD,
                name=str(dashboard.get("name", "")),
                description=dashboard.get("description"),
                properties={
                    "collection_id": dashboard.get("collection_id"),
                    "archived": bool(dashboard.get("archived", False)),
                },
            )
        )
        dashcards = dashboard.get("dashcards")
        if not isinstance(dashcards, list):
            dashcards = dashboard.get("ordered_cards")
        seen: set[int] = set()
        all_cards_known = True
        for dashcard in dashcards if isinstance(dashcards, list) else []:
            if not isinstance(dashcard, dict):
                continue
            card_id = dashcard.get("card_id")
            if card_id is None:
                continue  # virtual dashcard (text/heading)
            if not isinstance(card_id, int) or card_id in seen:
                all_cards_known = all_cards_known and isinstance(card_id, int)
                continue
            seen.add(card_id)
            if mb_card_node_id(card_id) not in card_node_ids:
                all_cards_known = False
                continue
            result.edges.append(
                Edge(
                    from_=mb_card_node_id(card_id),
                    to=dash_node_id,
                    edge_type=EdgeType.APPEARS_ON,
                    confidence=Confidence.EXACT,
                    evidence={"dashcard_id": dashcard.get("id")},
                )
            )
        if all_cards_known:
            result.dashboards += 1

    return result
