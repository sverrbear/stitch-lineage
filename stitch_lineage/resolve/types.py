"""Resolve a column's data type through the source waterfall (SPEC.md section 7.6, issue #149).

Column types used to come from exactly one place: `target/catalog.json`, which only
describes the relations the developer running the build has actually built. Everything
else read `data type: unknown` -- not because the type was unknowable, but because we
only ever asked one source. Snowflake has a type for every column it stores, and
Metabase has already synced it.

The waterfall, first hit wins:

  1. catalog   -- the dbt build artifacts (catalog.json, else a schema.yml `data_type`).
                  Applied in resolve.dbt where the artifacts are read.
  2. metabase  -- the warehouse type of the Metabase field this column binds to
                  *exactly*. Metabase syncs the whole prod schema regardless of what
                  any one developer has built, so this is where most of the former
                  unknowns get their answer.
  3. inferred  -- sqlglot's `annotate_types` over the compiled SQL (opt-in, see
                  resolve.dbt._inferred_types). A parse result, not an observation.
  4. unknown   -- no source had it. `data_type` stays None and `data_type_source` is
                  absent; the app says so and says why.

Precedence lives HERE, in one function, rather than in each producer. The inferred pass
runs inside resolve.dbt (that is where the compiled SQL and the schema map are) but its
results arrive as *candidates* and are applied last, so a cheap parse guess can never
outrank the warehouse's own answer just by being computed earlier.

Pure: nodes and edges in, nodes out. No filesystem, no network -- step 2 reads the
Metabase field metadata that resolve.metabase already put on the mb_field nodes.
"""

from pydantic import BaseModel, Field

from stitch_lineage.graph.schema import (
    Confidence,
    DataTypeSource,
    Edge,
    EdgeType,
    Node,
    NodeType,
)


class TypeWaterfallResult(BaseModel):
    """Nodes with types filled in, plus a count per source for the build report."""

    nodes: list[Node] = Field(default_factory=list)
    from_catalog: int = 0
    from_metabase: int = 0
    from_inferred: int = 0
    unknown: int = 0


def _field_type(field: Node) -> str | None:
    """The warehouse type of an mb_field, else its Metabase base type.

    `database_type` is the warehouse's own spelling (NUMBER(38,0), TIMESTAMP_NTZ) and so
    is directly comparable with a catalog type, which is what makes mixing the two
    sources in one column of the UI honest. `base_type` (type/Float) is Metabase's
    abstraction over it -- a worse answer than the warehouse's, but a far better one
    than "unknown", so it stands in when the sync did not record a database type.
    """
    database_type = field.properties.get("database_type")
    if isinstance(database_type, str) and database_type.strip():
        return database_type
    return field.data_type or None


def apply_type_waterfall(
    nodes: list[Node],
    edges: list[Edge],
    inferred_types: dict[str, str] | None = None,
) -> TypeWaterfallResult:
    """Fill `data_type`/`data_type_source` on column nodes that have no type yet.

    Columns that already carry a type keep it and are counted as `catalog` -- resolve.dbt
    is the only producer that sets one before this pass, and it sets the source with it.

    Step 2 follows `binds_to` edges of confidence `exact` only. A fuzzy binding matched
    on squashed underscores and case; it is good enough to draw a lineage edge a human
    will read in context, and not good enough to assert a type as fact about a column
    whose name we already know we guessed at. When a column binds exactly to several
    fields (two Metabase connections onto one warehouse) the candidates are taken in
    sorted field-node order so the build stays deterministic.

    Step 3 applies `inferred_types` ({column node_id: type}), the opt-in sqlglot pass
    from resolve.dbt, to whatever is still untyped.

    Filling a type also clears `unknown_type_reason`: that property explains an absent
    type, and a node claiming both a NUMBER and a reason it has no type is a bug the
    app would render as one.
    """
    inferred = inferred_types or {}
    fields = {n.node_id: n for n in nodes if n.node_type is NodeType.MB_FIELD}

    # column node id -> its exactly-bound mb_field ids, sorted for determinism
    bound: dict[str, list[str]] = {}
    for edge in edges:
        if edge.edge_type is EdgeType.BINDS_TO and edge.confidence is Confidence.EXACT:
            bound.setdefault(edge.from_, []).append(edge.to)

    result = TypeWaterfallResult()
    out: list[Node] = []
    for node in nodes:
        if node.node_type is not NodeType.COLUMN:
            out.append(node)
            continue
        if node.data_type:
            result.from_catalog += 1
            out.append(node)
            continue

        data_type, source = None, None
        for field_id in sorted(bound.get(node.node_id, [])):
            field = fields.get(field_id)
            candidate = _field_type(field) if field is not None else None
            if candidate:
                data_type, source = candidate, DataTypeSource.METABASE
                break
        if data_type is None and inferred.get(node.node_id):
            data_type, source = inferred[node.node_id], DataTypeSource.INFERRED

        if data_type is None:
            result.unknown += 1
            out.append(node)
            continue

        if source is DataTypeSource.METABASE:
            result.from_metabase += 1
        else:
            result.from_inferred += 1
        properties = {k: v for k, v in node.properties.items() if k != "unknown_type_reason"}
        out.append(
            node.model_copy(
                update={
                    "data_type": data_type,
                    "data_type_source": source,
                    "properties": properties,
                }
            )
        )

    result.nodes = out
    return result
