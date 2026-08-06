"""Bind Metabase tables/fields to dbt models/columns (SPEC.md section 7.5)."""

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import (
    Confidence,
    Edge,
    EdgeType,
    Node,
    NodeType,
)


class BindResult(BaseModel):
    """Output of bind: binds_to edges plus binding coverage counters.

    Coverage fields map 1:1 onto graph.schema.Coverage; the CLI copies them over.
    case_mismatch_count backs the case-only-mismatch warning line.
    unverified_field_count counts mb fields whose table bound to a dbt relation that
    supplied no column inventory -- those bindings are skipped, never fabricated.
    """

    edges: list[Edge] = Field(default_factory=list)
    models_bound: int = 0
    models_total: int = 0
    unbound_models: list[str] = Field(default_factory=list)
    case_mismatch_count: int = 0
    unverified_field_count: int = 0


def _fold(value: str | None) -> str:
    return (value or "").casefold()


def _squash(value: str | None) -> str:
    return _fold(value).replace("_", "")


def bind(
    dbt_nodes: list[Node],
    mb_field_nodes: list[Node],
    database_map: list[tuple[str, str]],
) -> BindResult:
    """Produce `binds_to` edges (dbt column -> mb_field).

    dbt_nodes are the source/model Nodes from resolve_dbt plus their column Nodes,
    which gate every binding: an edge is only emitted for a column that exists on the
    dbt side. A bound relation with no column Nodes at all (absent from catalog AND
    no manifest columns) binds nothing -- its fields are skipped and counted in
    unverified_field_count instead of fabricating exact edges to unverified columns.
    mb_field_nodes are the mb_field Nodes from resolve_metabase (database = Metabase
    display name, schema/table/column physical names); database_map is
    [(metabase_display_name, dbt_database), ...] from config metabase.databases.

    Match (database, schema, table, column) case-insensitively, honouring the model's
    physical table (Node.table, set from alias). Exact match -> confidence exact; a
    case-only difference stays exact with evidence {"case_mismatch": true} and counts
    toward case_mismatch_count. When the mb column differs from a dbt column only by
    underscores/case -> confidence fuzzy with evidence; ambiguous or missing columns
    bind nothing. Never binds across tables. Traps handled explicitly: Snowflake
    uppercase unquoted identifiers, Metabase display name != dbt database name (the
    map), multiple Metabase connections to one warehouse (multiple map entries).

    models_total counts dbt model nodes (sources bind but are not counted);
    models_bound those whose (db, schema, table) appears on the Metabase side;
    unbound_models the remaining unique_ids, sorted.

    Pure: nodes in, edges out. No filesystem or network access.
    """
    display_to_dbt = {_fold(display): dbt_db for display, dbt_db in database_map}

    relations: dict[tuple[str, str, str], Node] = {}
    columns_by_model: dict[str, dict[str, Node]] = {}
    model_ids: list[str] = []

    for node in dbt_nodes:
        if node.node_type in (NodeType.MODEL, NodeType.SOURCE):
            if node.node_type is NodeType.MODEL and node.node_id not in model_ids:
                model_ids.append(node.node_id)
            key = (_fold(node.database), _fold(node.schema_), _fold(node.table or node.name))
            relations.setdefault(key, node)
        elif node.node_type is NodeType.COLUMN and "::" in node.node_id:
            owner = node.node_id.rpartition("::")[0]
            columns_by_model.setdefault(owner, {})[_fold(node.column or node.name)] = node

    edges: list[Edge] = []
    bound: set[str] = set()
    case_mismatches = 0
    unverified = 0

    for field in mb_field_nodes:
        dbt_database = display_to_dbt.get(_fold(field.database))
        if dbt_database is None or not field.table or not field.column:
            continue
        rel = relations.get((_fold(dbt_database), _fold(field.schema_), _fold(field.table)))
        if rel is None:
            continue
        if rel.node_type is NodeType.MODEL:
            bound.add(rel.node_id)

        case_mismatch = (
            (rel.database or "") != dbt_database
            or (rel.schema_ or "") != (field.schema_ or "")
            or (rel.table or rel.name) != field.table
        )
        confidence = Confidence.EXACT
        evidence: dict[str, object] = {}
        model_columns = columns_by_model.get(rel.node_id)

        if model_columns is None:
            # no column inventory to verify against -- never fabricate an exact edge
            unverified += 1
            continue
        exact_col = model_columns.get(_fold(field.column))
        if exact_col is not None:
            from_id = exact_col.node_id
            if (exact_col.column or exact_col.name) != field.column:
                case_mismatch = True
        else:
            squashed = _squash(field.column)
            candidates = [c for k, c in model_columns.items() if _squash(k) == squashed]
            if len(candidates) != 1:
                continue
            fuzzy_col = candidates[0]
            from_id = fuzzy_col.node_id
            confidence = Confidence.FUZZY
            evidence = {
                "dbt_column": fuzzy_col.column or fuzzy_col.name,
                "mb_column": field.column,
                "match": "underscore_case_fold",
            }

        if confidence is Confidence.EXACT and case_mismatch:
            evidence = {"case_mismatch": True}
            case_mismatches += 1

        edges.append(
            Edge(
                from_=from_id,
                to=field.node_id,
                edge_type=EdgeType.BINDS_TO,
                confidence=confidence,
                evidence=evidence,
            )
        )

    return BindResult(
        edges=edges,
        models_bound=len(bound),
        models_total=len(model_ids),
        unbound_models=sorted(uid for uid in model_ids if uid not in bound),
        case_mismatch_count=case_mismatches,
        unverified_field_count=unverified,
    )
