"""Resolve raw Metabase payloads into graph nodes and edges (SPEC.md section 7.4)."""

from collections.abc import Callable
from fnmatch import fnmatch
from typing import Any, NamedTuple

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

# Metabase ships two dataset_query shapes and both are live in the wild:
#   legacy  {"type": "query", "query": {...}}      -- nested one level per source-query
#   MBQL 5  {"lib/type": "mbql/query", "stages": [...]} -- a flat chain of stages
# Everything below walks them through shared helpers so the evidence vocabulary,
# confidence rules and coverage counters are identical on both.
_CLAUSE_KEYS = ("fields", "breakout", "aggregation", "filter", "expressions", "order-by")

# MBQL 5 renamed `filter` to `filters`; the clause LABEL stays `filter` so a Metabase
# upgrade does not rewrite every edge's evidence in graph.json.
_STAGE_CLAUSE_LABELS = {
    "fields": "fields",
    "breakout": "breakout",
    "aggregation": "aggregation",
    "filters": "filter",
    "expressions": "expressions",
    "order-by": "order-by",
}

_LEGACY_QUERY = "legacy"
_STAGE_QUERY = "stages"
_NATIVE_QUERY = "native"
_QUERY_TYPES = {_LEGACY_QUERY: "query", _STAGE_QUERY: "query", _NATIVE_QUERY: "native"}


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


def _source_card_id(container: dict[str, Any]) -> int | None:
    """Card a query/stage reads from: MBQL 5 "source-card": N, legacy "card__N"."""
    source_card = container.get("source-card")
    if isinstance(source_card, int):
        return source_card
    return _card_source_id(container.get("source-table"))


def _ref_parts(ref: list[Any]) -> tuple[Any, dict[str, Any] | None]:
    """(target, opts) of a clause ref, for either argument order.

    Legacy puts the id/name first: ["field", <id-or-name>, opts]. MBQL 5 moved the
    options map into the middle: ["field", opts, <id-or-name>]. The dict is the
    discriminator -- a legacy ref never carries one in position 1.
    """
    if len(ref) > 2 and isinstance(ref[1], dict):
        return ref[2], ref[1]
    target = ref[1] if len(ref) > 1 else None
    opts = ref[2] if len(ref) > 2 and isinstance(ref[2], dict) else None
    return target, opts


class _Ref(NamedTuple):
    """One collected field ref: the raw clause (verbatim, for `stitch doctor`), its
    shape-normalized target (field id or column name) and opts, the clause label it
    sits under, whether it came from opts["source-field"], and the join aliases in
    scope where it was found (a live dict -- fully populated once the walk ends)."""

    raw: Any
    target: Any
    opts: dict[str, Any] | None
    clause: str
    is_source_field: bool
    join_aliases: dict[str, Any]


def _collect_refs(
    node: Any,
    clause: str,
    refs: list[_Ref],
    join_aliases: dict[str, Any],
    upstream_cards: list[int],
) -> None:
    """Recursively collect field refs of either shape with the clause they sit in.

    opts["source-field"] (implicit join through an FK) is emitted as an extra ref
    flagged is_source_field=True -- suggestion-layer input per SPEC.md section 7.4.
    A ["metric", ...] ref points at a saved metric card, so it is recorded as an
    upstream card and its fields propagate through the card-on-card machinery.
    """
    if isinstance(node, list):
        head = node[0] if node else None
        if head == "field" and len(node) >= 2:
            target, opts = _ref_parts(node)
            refs.append(_Ref(node, target, opts, clause, False, join_aliases))
            source_field = opts.get("source-field") if isinstance(opts, dict) else None
            if source_field is not None:
                refs.append(
                    _Ref(
                        ["field", source_field, None],
                        source_field,
                        None,
                        clause,
                        True,
                        join_aliases,
                    )
                )
            return
        if head == "metric" and len(node) >= 2:
            metric_card, _ = _ref_parts(node)
            if isinstance(metric_card, int):
                upstream_cards.append(metric_card)
            return
        for item in node:
            _collect_refs(item, clause, refs, join_aliases, upstream_cards)
    elif isinstance(node, dict):
        for value in node.values():
            _collect_refs(value, clause, refs, join_aliases, upstream_cards)


def _walk_query(
    query: dict[str, Any],
    prefix: str,
    refs: list[_Ref],
    upstream_cards: list[int],
    join_aliases: dict[str, Any],
) -> None:
    """Walk a legacy MBQL query, descending into source-query and joins."""
    upstream = _source_card_id(query)
    if upstream is not None:
        upstream_cards.append(upstream)
    for key in _CLAUSE_KEYS:
        if key in query:
            _collect_refs(query[key], f"{prefix}{key}", refs, join_aliases, upstream_cards)
    joins = query.get("joins")
    for join in joins if isinstance(joins, list) else []:
        if not isinstance(join, dict):
            continue
        if isinstance(join.get("alias"), str):
            join_aliases.setdefault(join["alias"], join.get("source-table"))
        join_upstream = _source_card_id(join)
        if join_upstream is not None:
            upstream_cards.append(join_upstream)
        if "condition" in join:
            _collect_refs(
                join["condition"], f"{prefix}joins.condition", refs, join_aliases, upstream_cards
            )
        if isinstance(join.get("fields"), list):
            _collect_refs(
                join["fields"], f"{prefix}joins.fields", refs, join_aliases, upstream_cards
            )
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


def _is_native_stage(stage: Any) -> bool:
    """True for an MBQL 5 native stage: {"lib/type": "mbql.stage/native", "native": <sql>}.

    Both the lib/type tag and the native payload are accepted -- instances in the wild
    also emit the "mbql/stage/native" spelling.
    """
    if not isinstance(stage, dict):
        return False
    return "native" in str(stage.get("lib/type", "")) or stage.get("native") is not None


def _join_source(join_stages: list[Any]) -> Any:
    """What an MBQL 5 join reads from, in the legacy "source-table" vocabulary: a table
    id (int) maps a join alias exactly, a "card__N" marker cannot and so downgrades
    by-name resolution through that alias to `parsed`."""
    first = join_stages[0] if join_stages and isinstance(join_stages[0], dict) else {}
    card_id = _source_card_id(first)
    return f"card__{card_id}" if card_id is not None else first.get("source-table")


def _walk_stages(
    stages: list[Any],
    prefix: str,
    refs: list[_Ref],
    upstream_cards: list[int],
    join_aliases: dict[str, Any],
) -> None:
    """Walk an MBQL 5 stage chain -- the flat equivalent of nested source-query.

    Stage 0 is the innermost source and keeps bare clause labels, so a single-stage
    MBQL 5 query yields exactly the evidence its legacy twin would; each later stage
    consumes the previous stage's output and is labelled stage1./stage2./... .

    Join aliases are scoped per stage: a stage starts from the aliases visible in the
    stage before it (a later stage can still reference "Alias -> column") and adds its
    own joins, so sibling joins and join subqueries never see each other's aliases.
    """
    scope = join_aliases
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        scope = dict(scope)
        stage_prefix = prefix if index == 0 else f"{prefix}stage{index}."
        upstream = _source_card_id(stage)
        if upstream is not None:
            upstream_cards.append(upstream)
        if _is_native_stage(stage):
            continue
        for key, label in _STAGE_CLAUSE_LABELS.items():
            if key in stage:
                _collect_refs(stage[key], f"{stage_prefix}{label}", refs, scope, upstream_cards)
        joins = stage.get("joins")
        for join in joins if isinstance(joins, list) else []:
            if not isinstance(join, dict):
                continue
            join_stages = join.get("stages") if isinstance(join.get("stages"), list) else []
            if isinstance(join.get("alias"), str):
                # this stage's own join shadows an inherited alias of the same name
                scope[join["alias"]] = _join_source(join_stages)
            # MBQL 5 pluralized `condition` -> `conditions`; the label stays singular
            for key, label in (("conditions", "joins.condition"), ("fields", "joins.fields")):
                if key in join:
                    _collect_refs(join[key], f"{stage_prefix}{label}", refs, scope, upstream_cards)
            if join_stages:
                _walk_stages(
                    join_stages,
                    f"{stage_prefix}joins.source-query.",
                    refs,
                    upstream_cards,
                    scope,
                )


def _source_context(query: dict[str, Any]) -> tuple[str, Any] | None:
    """Innermost source of a legacy query: ("table", table_id) or ("card", card_id)."""
    while isinstance(query.get("source-query"), dict):
        query = query["source-query"]
    card_id = _source_card_id(query)
    if card_id is not None:
        return ("card", card_id)
    source = query.get("source-table")
    return ("table", source) if isinstance(source, int) else None


def _stage_context(stages: list[Any]) -> tuple[str, Any] | None:
    """Innermost source of an MBQL 5 chain -- stage 0, the _source_context twin."""
    first = next((stage for stage in stages if isinstance(stage, dict)), None)
    if first is None:
        return None
    card_id = _source_card_id(first)
    if card_id is not None:
        return ("card", card_id)
    source = first.get("source-table")
    return ("table", source) if isinstance(source, int) else None


def _query_kind(dataset_query: Any) -> str | None:
    """Classify a card's dataset_query, or None when there is no query to walk.

    MBQL 5 wraps native SQL too ({"lib/type": "mbql/query", "stages": [{"lib/type":
    "mbql.stage/native", ...}]}), so nativeness is decided by the first stage. That
    keeps the native-vs-MBQL split -- and therefore coverage -- identical on both shapes.
    """
    if not isinstance(dataset_query, dict):
        return None
    stages = dataset_query.get("stages")
    if dataset_query.get("lib/type") == "mbql/query" or isinstance(stages, list):
        if not isinstance(stages, list) or not stages:
            return None
        return _NATIVE_QUERY if _is_native_stage(stages[0]) else _STAGE_QUERY
    if dataset_query.get("type") == "native":
        return _NATIVE_QUERY
    return _LEGACY_QUERY if isinstance(dataset_query.get("query"), dict) else None


class _CardWalk(BaseModel):
    """Per-card walk result: direct field consumption, upstream card refs, failures."""

    consumed: dict[int, dict[str, Any]] = Field(default_factory=dict)
    upstream_cards: list[int] = Field(default_factory=list)
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
                # ["aggregation", 0] / ["expression", "name"] are not field refs
                if isinstance(ref, list) and len(ref) >= 2 and ref[0] == "field":
                    target, _ = _ref_parts(ref)
                    if isinstance(target, int):
                        candidates.append(target)
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
      * `consumed_by` edges (mb_field -> mb_card): walk the card's MBQL over fields,
        breakout, aggregation, filter, expressions, joins[].condition, joins[].fields
        and order-by; every ["field", <id>, opts] resolves through the metadata map,
        confidence exact. Both query shapes are walked -- legacy dataset_query.query
        with nested source-query, and MBQL 5 dataset_query.stages (lib/type
        "mbql/query"), whose refs put the options map in the middle
        (["field", opts, <id-or-name>]), chain stages instead of nesting, pluralize
        `filter`/`condition` and carry join sources in the join's own stages. Clause
        labels are shape-independent; MBQL 5 stages after the first are prefixed
        stage1./stage2./... . By-name string refs resolve exactly through a mapped
        join-alias, the source table's metadata or the upstream card's
        result_metadata; the upstream-card consumed-fields name match (and any ref
        whose join-alias cannot be mapped to its joined table) is a heuristic and
        ships confidence `parsed`. Card-on-card (source-table "card__123", MBQL 5
        source-card, and ["metric", ...] refs to saved metric cards) resolves
        transitively, cycle-guarded with a visited set. Native SQL cards are Phase 3
        -- in either shape (legacy type "native", MBQL 5 first stage
        mbql.stage/native): count them in native_cards_total, resolve none, add their
        ids to unresolved_cards -- never drop a card silently.
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
    kinds: dict[int, str | None] = {}
    deferred: list[tuple[int, _Ref, tuple[str, Any] | None]] = []
    for card in cards_in_scope:
        card_id = card["id"]
        dataset_query = card.get("dataset_query")
        kind = _query_kind(dataset_query)
        kinds[card_id] = kind
        walk = _CardWalk()
        refs: list[_Ref] = []
        if kind == _LEGACY_QUERY:
            query = dataset_query["query"]
            context = _source_context(query)
            _walk_query(query, "", refs, walk.upstream_cards, {})
        elif kind == _STAGE_QUERY:
            stages = dataset_query["stages"]
            context = _stage_context(stages)
            _walk_stages(stages, "", refs, walk.upstream_cards, {})
        else:
            continue
        for ref in refs:
            if isinstance(ref.target, int):
                if ref.target in field_ids:
                    _record(
                        walk.consumed, ref.target, ref.clause, implicit_join=ref.is_source_field
                    )
                else:
                    walk.problems.append(
                        {"card_id": card_id, "ref": ref.raw, "reason": "unknown field id"}
                    )
            elif isinstance(ref.target, str):
                deferred.append((card_id, ref, context))
            else:
                walk.problems.append(
                    {"card_id": card_id, "ref": ref.raw, "reason": "malformed ref"}
                )
        walks[card_id] = walk

    # by-name refs resolve after every card is walked, so card-context lookups do not
    # depend on card ordering in /api/card
    for card_id, ref, context in deferred:
        resolved, exact = _resolve_by_name(
            ref.target,
            ref.opts,
            context,
            field_ids,
            table_columns,
            cards_by_id,
            walks,
            fields_by_id,
            ref.join_aliases,
        )
        if resolved is not None:
            _record(
                walks[card_id].consumed,
                resolved,
                ref.clause,
                implicit_join=ref.is_source_field,
                by_name=True,
                parsed=not exact,
            )
        else:
            walks[card_id].problems.append(
                {"card_id": card_id, "ref": ref.raw, "reason": "unresolvable field name"}
            )

    transitive: dict[int, set[int]] = {}

    def _transitive_fields(card_id: int, visiting: set[int]) -> set[int]:
        if card_id in transitive:
            return transitive[card_id]
        if card_id in visiting:
            return set()
        visiting.add(card_id)
        walk = walks.get(card_id)
        consumed = set(walk.consumed) if walk else set()
        for upstream_id in walk.upstream_cards if walk else []:
            consumed |= _transitive_fields(upstream_id, visiting)
        visiting.discard(card_id)
        transitive[card_id] = consumed
        return consumed

    def _resolve_card(card: dict[str, Any]) -> None:
        card_id = card["id"]
        card_node_id = mb_card_node_id(card_id)
        dataset_query = card.get("dataset_query")
        kind = kinds.get(card_id)
        declared = dataset_query.get("type") if isinstance(dataset_query, dict) else None
        query_type = _QUERY_TYPES.get(kind, declared)
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

        if kind == _NATIVE_QUERY:
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
            for field_id in sorted(_transitive_fields(upstream_id, set()) - emitted):
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
