"""Resolve dbt manifest + catalog into graph nodes and edges (SPEC.md sections 7.1, 7.3, 8.1)."""

import re
from collections.abc import Callable
from enum import StrEnum
from typing import Any

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage as _sqlglot_lineage
from sqlglot.optimizer.annotate_types import annotate_types as _sqlglot_annotate_types
from sqlglot.optimizer.qualify import qualify as _sqlglot_qualify
from sqlglot.schema import MappingSchema

from stitch_lineage.graph.schema import (
    Confidence,
    DataTypeSource,
    Edge,
    EdgeType,
    Node,
    NodeType,
    column_node_id,
)

_DIALECT = "snowflake"
_REF_RE = re.compile(r"ref\(\s*(?:['\"][^'\"]+['\"]\s*,\s*)?['\"]([^'\"]+)['\"]")
_FK_EXPRESSION_RE = re.compile(r"^\s*(?P<target>[^()]+?)\s*\(\s*(?P<column>[^()]+?)\s*\)\s*$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_GENERATED_ALIAS_RE = re.compile(r"^_col_\d+$", re.IGNORECASE)

# `defined_as.sql` is a label in a detail panel, not the evidence: the untruncated
# expression already travels on every feeds edge's `evidence.sql`.
_DEFINED_AS_SQL_LIMIT = 240


class TraceReason(StrEnum):
    """Why a model column has no `feeds` edge -- the SPEC section 7.3 failure taxonomy.

    One member per failure mode the resolver actually distinguishes, because "untraced"
    on its own is a dead end: the whole point of the list is that fixing one upstream
    doc gap often rescues a subtree, and only the reason says which gap that is.
    """

    NO_COMPILED_CODE = "no_compiled_code"
    UNPARSEABLE_SQL = "unparseable_sql"
    COLUMN_NOT_IN_SQL = "column_not_in_sql"
    STAR_NOT_EXPANDABLE = "star_not_expandable"
    UPSTREAM_NOT_IN_SCHEMA_MAP = "upstream_not_in_schema_map"
    UPSTREAM_NOT_IN_PROJECT = "upstream_not_in_project"
    NO_UPSTREAM_COLUMNS = "no_upstream_columns"
    LINEAGE_FAILED = "lineage_failed"


# One line per reason, in the words of the thing to go and fix. The app carries its
# own copy (frontend/src/lib/trace.ts) the same way types.ts mirrors graph/schema.py;
# `stitch doctor --untraced` reads these.
TRACE_REASON_LABELS: dict[str, str] = {
    TraceReason.NO_COMPILED_CODE: "model has no compiled SQL",
    TraceReason.UNPARSEABLE_SQL: "SQL could not be parsed",
    TraceReason.COLUMN_NOT_IN_SQL: "documented but not in the SQL",
    TraceReason.STAR_NOT_EXPANDABLE: "star not expandable",
    TraceReason.UPSTREAM_NOT_IN_SCHEMA_MAP: "upstream absent from the schema map",
    TraceReason.UPSTREAM_NOT_IN_PROJECT: "upstream not a dbt model or source",
    TraceReason.NO_UPSTREAM_COLUMNS: "literal — nothing upstream",
    TraceReason.LINEAGE_FAILED: "parser could not walk this column",
}


class DefinedOrigin(BaseModel):
    """Where a passthrough column was actually DEFINED, `hops` models upstream (#162).

    A passthrough's own projection only restates the edge the upstream list already
    shows -- `revenue` is `i.amount_usd` is `s.amount_usd` is ... -- so on its own it
    answers "where was this last mentioned", not "where was this defined". This is the
    answer to the second question: the walk up the passthrough chain, stopping where
    the column stops being a passthrough.

    kind is what ended the walk:
      expression -- a computed projection in `model`; `sql` is that expression
      source     -- a source column, i.e. the warehouse root; `sql` is None

    Those two are the only definitive ends, so they are the only ones recorded: a walk
    that dies on an ambiguous or untraced hop yields no origin at all, because "as far
    as this build could get" would read in the panel as a definition without being one.
    """

    kind: str
    """The model or source the column is defined in, in dbt's spelling."""
    model: str
    """Its name there, which a rename mid-chain makes different from the caller's."""
    column: str
    sql: str | None = None
    """Passthrough hops walked to get here; 1 means the immediate upstream."""
    hops: int


class DefinedAs(BaseModel):
    """How a model column is defined, read off its projection in the compiled SQL.

    kind is what the projection IS, which is also how it reads in the app:
      expression  -- computed: `amount * fx_rate`, a CASE, an aggregate, a literal
      passthrough -- the upstream column itself, renamed or not; `upstream` names it
      star        -- arrived through `select *`; `upstream` names the relation

    `upstream` is set only when it is unambiguous (exactly one upstream column for a
    passthrough, exactly one upstream relation for a star) -- a guess here would read
    as a fact. sql is truncated for display; see _DEFINED_AS_SQL_LIMIT.

    `origin` is set on a passthrough or star whose chain ends somewhere definitive --
    the column's actual definition, which is the whole point of the block (#162). An
    expression is its own origin and carries none.
    """

    kind: str
    sql: str
    upstream: str | None = None
    origin: DefinedOrigin | None = None


class ColumnTrace(BaseModel):
    """Per-column output of the sqlglot pass, beyond the edges themselves.

    reason is set exactly when the column produced no `feeds` edge; defined_as
    whenever the compiled SQL says how the column is built. Either can be None:
    an unparseable model has a reason and no definition, and a column can be
    traced (definition known) with no reason at all.

    upstream_ref is scaffolding rather than output: the (unique_id, column) a
    passthrough or star reads, which is what makes the chain walkable once every
    model's own trace exists. It is never written onto a node -- only `defined_as`
    and `reason` are (see _column_nodes) -- because the app wants the end of the
    walk, not the links.
    """

    defined_as: DefinedAs | None = None
    reason: TraceReason | None = None
    upstream_ref: tuple[str, str] | None = None


class DbtResolution(BaseModel):
    """Output of resolve_dbt: the dbt side of the graph plus its coverage counters.

    Coverage fields map 1:1 onto graph.schema.Coverage (columns_traced/columns_total/
    columns_inferred/untraced_columns/dangling_relationships/seed_snapshot_dependencies);
    the CLI copies them over.

    nodes/edges come out in resolver order -- io.graph_store canonicalizes on write.

    inferred_types ({column node_id: type}) are *candidates*, not applied types: the
    type waterfall (resolve.types) ranks them below the warehouse's own answer, so
    they are handed over rather than written onto the nodes here (issue #149).
    """

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    inferred_types: dict[str, str] = Field(default_factory=dict)
    columns_traced: int = 0
    columns_total: int = 0
    columns_inferred: int = 0
    seed_snapshot_dependencies: int = 0
    untraced_columns: list[str] = Field(default_factory=list)
    dangling_relationships: list[str] = Field(default_factory=list)


_DEFAULT_FK_META_KEYS = ("metabase.fk_target_table", "metabase.fk_target_field")
_DEFAULT_CARDINALITY_META_KEY = "relationship_type"


def resolve_dbt(
    manifest: dict[str, Any],
    catalog: dict[str, Any],
    fk_meta_keys: tuple[str, str] | list[str] = _DEFAULT_FK_META_KEYS,
    cardinality_meta_key: str = _DEFAULT_CARDINALITY_META_KEY,
    on_progress: Callable[[int, int], None] | None = None,
    infer_types: bool = False,
) -> DbtResolution:
    """Build the dbt side of the graph from parsed manifest.json and catalog.json.

    fk_meta_keys is the (target_table_key, target_field_key) pair read from column
    meta for simple FK declarations, and cardinality_meta_key the key carrying the
    relationship cardinality -- both default to the dbt-metabase/dbterd interop keys
    and are configurable via relationships.fk_meta_keys / cardinality_meta_key in
    stitch.yml (additive; passing nothing keeps the historical behavior).

    on_progress, when given, is called as on_progress(done, total) after each model's
    column lineage is traced (the sqlglot pass dominates build time); total is the
    model count, fixed for the whole run.

    infer_types (opt-in, `stitch build --infer-types`) runs sqlglot's annotate_types
    over each model's compiled SQL and returns the results in `inferred_types` for the
    type waterfall to apply last (SPEC.md section 7.6). Off by default: the types come
    back in sqlglot's canonical dialect spelling (a Snowflake NUMBER reads back as
    DECIMAL(38, 0)), so switching it on trades a spelling that matches the catalog's
    for an answer where there was none.

    Produces:
      * source/model Nodes (node_id = dbt unique_id) from manifest nodes/sources.
      * column Nodes (node_id via schema.column_node_id). A model's column *set* is
        the outermost projection of its compiled_code (sqlglot, stars expanded against
        the schema map) unioned with its schema.yml columns; the catalog contributes
        data types for matching names only, never set membership. The SQL is what a PR
        changes, so a column dropped from a model but still standing in the warehouse
        disappears from the graph at parse time -- which is what makes a pre-deploy
        graph diff see breaking changes before deployment. Fall back to the catalog
        set (manifest columns when the relation is absent from the catalog) whenever
        the projection cannot be pinned down: unparseable SQL, no compiled_code, an
        unexpandable star over an unknown upstream, or an output whose name sqlglot
        cannot give (an unaliased literal/expression). Never an empty set from a model
        that has columns elsewhere. Sources keep catalog-then-manifest behavior: they
        have no SQL to derive from. A column Node is *named* the dbt way -- the model's
        compiled SQL, else schema.yml, else the upstream's spelling for a `select *`
        pass-through, else the catalog -- because the warehouse's SCREAMING_CASE is not
        how anyone refers to the column; the catalog spelling stays on
        properties.warehouse_name whenever it differs.
      * `references` edges (upstream model -> downstream model) from manifest
        depends_on, confidence exact. Seeds and snapshots are not node types in the
        graph, so those dependencies carry no edge and are counted in
        seed_snapshot_dependencies instead of vanishing silently.
      * `feeds` edges (upstream column -> downstream column) via sqlglot.lineage over
        each model's compiled_code, dialect="snowflake", schema-qualified from the
        catalog, falling back to manifest columns for relations the catalog is missing
        (a dev catalog only holds what that developer built). Plain projections/renames
        -> confidence exact; expressions -> parsed (one edge per input column);
        star-expansion by name-matching, whether via the fallback path or expanded
        against manifest columns -> inferred. Unparseable model -> fail soft: keep its
        `references` edges, add its columns to untraced_columns, never blank the graph.
        Ephemeral hops attribute to the parent model; VARIANT sub-paths land on the
        VARIANT column. A feeds edge never carries a "*" or empty column endpoint: when
        a star branch cannot resolve to real upstream columns the downstream column
        goes untraced instead.
      * `relates_to` edges (FK column -> referenced PK column) read from column meta
        (metabase.fk_target_table/field + relationship_type), model-level
        stitch.relationships meta, existing relationships tests, and contract
        constraints. Meta-only -> confidence declared; backed by a relationships
        test -> validated. Declarations that point at a missing model/column go to
        dangling_relationships (formatted "model.column -> target"), not the edge list.

    Every model column also carries what the sqlglot pass learned about it --
    `trace_status`, `trace_reason` when untraced, and `defined_as` when the compiled
    SQL says how it is built (see _column_nodes). The projection used to be discarded
    once its edges were emitted; keeping it is what lets the app say of every column
    either how it is defined or why stitch could not tell (#147/#148).

    Coverage counts model columns only: source columns are lineage roots, so they are
    excluded from columns_total, columns_traced and untraced_columns. columns_total
    therefore counts the SQL-derived sets above, not the warehouse's: an undeployed
    column counts and is traced, a column only the warehouse still has does not exist.
    A model column is traced when at least one feeds edge points into it, and inferred
    when any of those edges came from the star-expansion name-matching fallback.

    Pure: dicts in, nodes/edges out. No filesystem or network access.
    """
    manifest_nodes = manifest.get("nodes") or {}
    models = {
        uid: node for uid, node in manifest_nodes.items() if node.get("resource_type") == "model"
    }
    sources = dict(manifest.get("sources") or {})

    catalog_specs = _catalog_column_specs(models, sources, catalog)
    mapping, manifest_sourced = _sqlglot_schema(models, sources, catalog_specs, catalog)
    # built once and then updated in place as model column sets resolve: sqlglot would
    # otherwise re-normalize every relation on every lineage call
    schema_mapping = MappingSchema(mapping, dialect=_DIALECT)
    column_specs, inferred_by_uid = _column_specs(
        models, sources, catalog, catalog_specs, schema_mapping, infer_types
    )
    entity_nodes = _entity_nodes(models, sources)
    references, seed_snapshot_deps = _references_edges(models, sources, manifest_nodes)
    # before the column nodes: trace_status is "did a feeds edge reach this column",
    # which is not knowable until the whole sqlglot pass has run
    feeds, traces = _feeds_edges(
        models, sources, column_specs, schema_mapping, manifest_sourced, on_progress
    )
    fed = {edge.to for edge in feeds}
    column_nodes = _column_nodes(
        models, sources, column_specs, _catalogued_relations(catalog), traces, fed
    )
    relates, dangling = _relates_to_edges(
        manifest_nodes, models, column_specs, tuple(fk_meta_keys), cardinality_meta_key
    )

    model_column_ids = {
        column_node_id(uid, spec["name"])
        for uid in models
        for spec in column_specs.get(uid, {}).values()
    }
    inferred_targets = {edge.to for edge in feeds if edge.confidence == Confidence.INFERRED}
    traced = model_column_ids & fed

    # candidates for columns the artifacts could not type -- the waterfall decides
    inferred_types = {
        column_node_id(uid, spec["name"]): inferred[lower]
        for uid, inferred in inferred_by_uid.items()
        for lower, spec in column_specs.get(uid, {}).items()
        if not spec["data_type"] and lower in inferred
    }

    return DbtResolution(
        nodes=entity_nodes + column_nodes,
        edges=references + feeds + relates,
        inferred_types=inferred_types,
        columns_traced=len(traced),
        columns_total=len(model_column_ids),
        columns_inferred=len(model_column_ids & inferred_targets),
        seed_snapshot_dependencies=seed_snapshot_deps,
        untraced_columns=sorted(model_column_ids - fed),
        dangling_relationships=dangling,
    )


def _merged_meta(entity: dict[str, Any]) -> dict[str, Any]:
    meta = dict(entity.get("meta") or {})
    meta.update((entity.get("config") or {}).get("meta") or {})
    return meta


def _physical_table(entity: dict[str, Any]) -> str:
    return entity.get("alias") or entity.get("identifier") or entity.get("name") or ""


def _is_ephemeral(model: dict[str, Any]) -> bool:
    return (model.get("config") or {}).get("materialized") == "ephemeral"


def _entity_nodes(models: dict[str, Any], sources: dict[str, Any]) -> list[Node]:
    nodes = []
    for uid, model in models.items():
        properties: dict[str, Any] = {
            "tags": model.get("tags") or [],
            "materialization": (model.get("config") or {}).get("materialized"),
            "path": model.get("original_file_path") or model.get("path"),
            # the dbt package that owns this model -- what metabase.exclude_packages
            # matches on, read from the manifest rather than split out of the unique_id
            "package": model.get("package_name"),
        }
        if _is_ephemeral(model):
            properties["is_ephemeral"] = True
        nodes.append(
            Node(
                node_id=uid,
                node_type=NodeType.MODEL,
                name=model.get("name") or uid,
                database=model.get("database"),
                schema_=model.get("schema"),
                table=_physical_table(model),
                description=model.get("description") or None,
                owner=_merged_meta(model).get("owner"),
                properties=properties,
            )
        )
    for uid, source in sources.items():
        nodes.append(
            Node(
                node_id=uid,
                node_type=NodeType.SOURCE,
                name=source.get("name") or uid,
                database=source.get("database"),
                schema_=source.get("schema"),
                table=_physical_table(source),
                description=source.get("description") or None,
                owner=_merged_meta(source).get("owner"),
                properties={
                    "tags": source.get("tags") or [],
                    "path": source.get("original_file_path") or source.get("path"),
                    "source_name": source.get("source_name"),
                },
            )
        )
    return nodes


def _catalog_columns(catalog: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Per entity uid: {lowercased column name: catalog column dict}."""
    catalog_entries = {**(catalog.get("nodes") or {}), **(catalog.get("sources") or {})}
    return {
        uid: {
            str(column.get("name") or key).lower(): column
            for key, column in (entry.get("columns") or {}).items()
        }
        for uid, entry in catalog_entries.items()
    }


def _model_order(models: dict[str, Any]) -> list[str]:
    """Model uids upstream-first, so a model's column set resolves after its parents'.

    Ties broken by uid and any dependency cycle appended sorted: order must not depend
    on dict iteration order, and dbt already rejects cycles.
    """
    pending = {
        uid: {
            dep
            for dep in (model.get("depends_on") or {}).get("nodes") or []
            if dep in models and dep != uid
        }
        for uid, model in models.items()
    }
    order: list[str] = []
    while pending:
        ready = sorted(uid for uid, deps in pending.items() if not deps & pending.keys())
        if not ready:
            order.extend(sorted(pending))
            break
        order.extend(ready)
        for uid in ready:
            del pending[uid]
    return order


def _projected_columns(
    compiled: str, schema_mapping: MappingSchema, infer_types: bool = False
) -> tuple[list[str], dict[str, str], dict[str, str]] | None:
    """(output column names, {lowercased name: SQL spelling}, {lowercased name: type}), or None.

    The third element is empty unless infer_types is on; see `_inferred_types`.

    None means "could not be pinned down" and the caller must fall back to the catalog
    set -- never to an empty one. sqlglot's qualify does the work stars need (expanding
    against the schema map, honouring `exclude`/`rename`, taking a set operation's names
    from its first branch); a star it could not expand survives as a literal "*" output.
    Outputs sqlglot can only name `_col_0` or `1` (an unaliased expression or literal)
    have no name to match a catalog column or a schema.yml entry against, so they force
    the fallback rather than inventing one.

    qualify normalizes identifiers for the dialect, so on Snowflake every returned name
    is upper-cased regardless of how the model spells it -- useless as a display casing.
    The second element is therefore read off the *unnormalized* parse: the project's own
    spelling of the outputs it names explicitly (a star's outputs are not in there).
    """
    if not compiled.strip():
        return None
    try:
        parsed = sqlglot.parse_one(compiled, dialect=_DIALECT)
        if not isinstance(parsed, exp.Query):
            return None
        spelled = _sql_output_spellings(parsed)
        qualified = _sqlglot_qualify(
            parsed,
            schema=schema_mapping,
            dialect=_DIALECT,
            validate_qualify_columns=False,
            identify=False,
        )
        names = qualified.named_selects
        inferred = _inferred_types(qualified, schema_mapping) if infer_types else {}
    except SqlglotError:
        return None
    if not names or not all(_is_usable_output_name(name) for name in names):
        return None
    deduped: dict[str, str] = {}
    for name in names:
        deduped.setdefault(name.lower(), name)
    return list(deduped.values()), spelled, inferred


def _inferred_types(qualified: exp.Expression, schema_mapping: MappingSchema) -> dict[str, str]:
    """{lowercased output name: inferred type} from sqlglot's annotate_types.

    Runs on the already-qualified expression -- annotation needs columns resolved to
    their relations to reach the schema map at all. Types sqlglot could not work out
    come back as UNKNOWN or NULL (an unmapped UDF, a bare `null as x`); those are
    dropped rather than recorded, because "we parsed it and learned nothing" is the
    same state as never having asked, and the app should keep saying unknown.

    The spelling is sqlglot's canonical one for the dialect, not the warehouse's --
    which is why this source ranks last and is labelled `inferred` in the app.
    """
    try:
        annotated = _sqlglot_annotate_types(qualified, schema=schema_mapping, dialect=_DIALECT)
    except SqlglotError:
        return {}
    select = annotated if isinstance(annotated, exp.Select) else annotated.find(exp.Select)
    if select is None:
        return {}
    types: dict[str, str] = {}
    for projection in select.expressions:
        name = projection.alias_or_name
        data_type = projection.type
        if not _is_real_column(name) or data_type is None:
            continue
        if data_type.this in (exp.DataType.Type.UNKNOWN, exp.DataType.Type.NULL):
            continue
        try:
            types.setdefault(name.lower(), data_type.sql(dialect=_DIALECT))
        except SqlglotError:
            continue
    return types


def _output_projections(parsed: exp.Expression) -> dict[str, exp.Expression]:
    """{lowercased output name: its projection expression}, explicit outputs only.

    The defining projection is what #148 renders and what #147's taxonomy is read
    against, so it is taken from the *unnormalized* parse for the same reason the
    spellings are: qualify would rewrite it into dialect-normalized SQL nobody wrote.
    A star projection names itself "*" and is filtered out here -- it has no single
    output name to key on; `_star_projection_sql` handles that case separately.
    """
    select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
    if select is None:
        return {}
    projections: dict[str, exp.Expression] = {}
    for projection in select.expressions:
        name = projection.alias_or_name
        if _is_real_column(name):
            projections.setdefault(name.lower(), projection)
    return projections


def _sql_output_spellings(parsed: exp.Expression) -> dict[str, str]:
    """{lowercased output name: the spelling the compiled SQL uses}, explicit outputs only."""
    return {
        lower: projection.alias_or_name for lower, projection in _output_projections(parsed).items()
    }


def _star_projection_sql(parsed: exp.Expression) -> str:
    """The first star projection as written (`o.*`, `*`), or "*" if none is found."""
    select = parsed if isinstance(parsed, exp.Select) else parsed.find(exp.Select)
    if select is not None:
        for projection in select.expressions:
            if isinstance(projection, exp.Star) or (
                isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
            ):
                return projection.sql(dialect=_DIALECT)
    return "*"


def _truncate_sql(text: str) -> str:
    """One line, capped at _DEFINED_AS_SQL_LIMIT -- a panel label, not the evidence."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= _DEFINED_AS_SQL_LIMIT:
        return collapsed
    return collapsed[: _DEFINED_AS_SQL_LIMIT - 1].rstrip() + "…"


def _is_usable_output_name(name: str) -> bool:
    return bool(
        name and _IDENTIFIER_RE.match(name) is not None and not _GENERATED_ALIAS_RE.match(name)
    )


def _dbt_spelling(warehouse_name: str | None) -> str | None:
    """The warehouse spelling as dbt would write it, when it says nothing about case.

    Snowflake folds an unquoted identifier to upper case on the way in, so a catalog
    entry of DOWNLOADS is a storage artefact, not somebody's choice -- and rendering it
    that way is exactly what #44 reports. An all-caps identifier therefore reads back
    lower-cased (the spelling every dbt model and every `ref` uses); anything with a
    lower-case letter in it was quoted deliberately and is left alone. The original
    always survives on `warehouse_name`.
    """
    if not warehouse_name or warehouse_name.lower() == warehouse_name:
        return warehouse_name
    return warehouse_name.lower() if warehouse_name.isupper() else warehouse_name


def _merge_column_specs(
    projected: list[str],
    spelled: dict[str, str],
    manifest_columns: dict[str, dict[str, Any]],
    catalog_columns: dict[str, dict[str, Any]],
    upstream_names: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Projection order first, then schema.yml columns the projection does not name.

    A documented column missing from the projection is kept: schema.yml is a claim about
    this model that a build would surface as an error, and silently dropping it would
    read as a deliberate removal in a graph diff.

    `name` is the dbt-side spelling, because that is the one people write and read:
    the model's own SQL, else its schema.yml, else -- for a column arriving through a
    `select *` -- the spelling the upstream node already resolved to, else the catalog's
    or the parser's with the dialect case-folding undone (see `_dbt_spelling`). The
    warehouse spelling is kept separately as `warehouse_name` (None when the relation is
    not in the catalog); node ids are lowercased either way.
    """
    specs: dict[str, dict[str, Any]] = {}
    for name in [
        *projected,
        *(column.get("name") or key for key, column in manifest_columns.items()),
    ]:
        lower = str(name).lower()
        if lower in specs:
            continue
        catalog_column = catalog_columns.get(lower) or {}
        manifest_column = manifest_columns.get(lower) or {}
        warehouse_name = catalog_column.get("name")
        specs[lower] = {
            "name": (
                spelled.get(lower)
                or manifest_column.get("name")
                # the parser's own output is dialect-normalized too, so it gets the
                # same treatment as the catalog's -- neither is an authored spelling
                or _dbt_spelling(warehouse_name or str(name))
            ),
            "warehouse_name": warehouse_name,
            "data_type": catalog_column.get("type") or manifest_column.get("data_type"),
            "description": manifest_column.get("description") or None,
        }
    return specs


def _upstream_names(
    model: dict[str, Any], specs: dict[str, dict[str, dict[str, Any]]]
) -> dict[str, str]:
    """{lowercased column name: dbt-side spelling} across the model's resolved parents.

    Models resolve in dependency order, so a `select *` can inherit the spelling its
    upstream settled on instead of falling back to the warehouse's. First parent that
    has the column wins -- the choice only ever affects casing.
    """
    names: dict[str, str] = {}
    for dep in dict.fromkeys((model.get("depends_on") or {}).get("nodes") or []):
        for lower, spec in specs.get(dep, {}).items():
            names.setdefault(lower, spec["name"])
    return names


def _column_specs(
    models: dict[str, Any],
    sources: dict[str, Any],
    catalog: dict[str, Any],
    catalog_specs: dict[str, dict[str, dict[str, Any]]],
    schema_mapping: MappingSchema,
    infer_types: bool = False,
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, str]]]:
    """Per entity: {lowercased column name: {name, warehouse_name, data_type, description}}.

    Second element is the per-model inferred types ({uid: {lowercased name: type}}),
    empty unless infer_types is on -- candidates for the waterfall, never applied here.

    A model's set comes from its compiled SQL (see resolve_dbt) with the catalog
    supplying types; sources, and models whose projection did not resolve, keep the
    catalog-then-manifest specs. Resolved model sets are written back into
    schema_mapping so a downstream `select *` expands to the columns that will exist
    rather than the ones the warehouse still has -- otherwise a removal one hop up
    would reappear downstream and feed an edge from a column node that is gone.
    """
    catalog_columns = _catalog_columns(catalog)
    specs = {uid: catalog_specs.get(uid, {}) for uid in sources}
    inferred_by_uid: dict[str, dict[str, str]] = {}
    for uid in _model_order(models):
        model = models[uid]
        compiled = model.get("compiled_code") or model.get("compiled_sql") or ""
        projection = _projected_columns(compiled, schema_mapping, infer_types)
        if projection is None:
            specs[uid] = catalog_specs.get(uid, {})
            continue
        projected, spelled, inferred = projection
        if inferred:
            inferred_by_uid[uid] = inferred
        manifest_columns = {
            str(name).lower(): column for name, column in (model.get("columns") or {}).items()
        }
        specs[uid] = _merge_column_specs(
            projected,
            spelled,
            manifest_columns,
            catalog_columns.get(uid, {}),
            _upstream_names(model, specs),
        )
        _update_relation(schema_mapping, model, specs[uid])
    return specs, inferred_by_uid


def _update_relation(
    schema_mapping: MappingSchema, model: dict[str, Any], specs: dict[str, dict[str, Any]]
) -> None:
    """Point the model's relation in the schema map at its resolved column set."""
    # ephemerals compile inline as CTEs -- they are never a relation to look up
    if _is_ephemeral(model) or not specs:
        return
    schema = model.get("schema")
    table = _physical_table(model)
    if not schema or not table:
        return
    parts = [part for part in (model.get("database"), schema, table) if part]
    columns = {
        spec["name"]: spec["data_type"] or "UNKNOWN"
        for spec in specs.values()
        if _is_real_column(spec["name"])
    }
    if not columns:
        return
    try:
        schema_mapping.add_table(
            ".".join(str(part) for part in parts), columns, dialect=_DIALECT, match_depth=False
        )
    except SqlglotError:
        return


def _catalog_column_specs(
    models: dict[str, Any], sources: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, dict[str, dict[str, Any]]]:
    """Per entity: {lowercased column name: {name, warehouse_name, data_type, description}}.

    Catalog is authoritative (column set + data_type) when the entity is present;
    manifest columns fill descriptions and act as full fallback otherwise. This is the
    warehouse's view of a relation: the seed for the sqlglot schema map, the specs for
    sources, and the fallback for models whose SQL does not resolve.

    `name` still prefers the project's spelling from schema.yml over the warehouse's --
    a source column documented as `user_id` reads as `user_id`, not `USER_ID` -- and an
    undocumented one falls back to `_dbt_spelling`, since a source has no SQL to derive
    a spelling from. The warehouse spelling stays on `warehouse_name` (None when the
    relation is not in the catalog, which is the only place a warehouse spelling exists).
    """
    catalog_entries = {**(catalog.get("nodes") or {}), **(catalog.get("sources") or {})}
    specs: dict[str, dict[str, dict[str, Any]]] = {}
    for uid, entity in {**models, **sources}.items():
        manifest_columns = {
            str(name).lower(): column for name, column in (entity.get("columns") or {}).items()
        }
        entity_specs: dict[str, dict[str, Any]] = {}
        catalog_entry = catalog_entries.get(uid)
        if catalog_entry:
            for key, column in (catalog_entry.get("columns") or {}).items():
                display = column.get("name") or str(key)
                manifest_column = manifest_columns.get(display.lower(), {})
                entity_specs[display.lower()] = {
                    "name": manifest_column.get("name") or _dbt_spelling(display),
                    "warehouse_name": display,
                    "data_type": column.get("type"),
                    "description": manifest_column.get("description") or None,
                }
        else:
            for lower, column in manifest_columns.items():
                entity_specs[lower] = {
                    "name": column.get("name") or lower,
                    "warehouse_name": None,
                    "data_type": column.get("data_type"),
                    "description": column.get("description") or None,
                }
        specs[uid] = entity_specs
    return specs


def _catalogued_relations(catalog: dict[str, Any]) -> set[str]:
    """uids the catalog has an entry for -- i.e. relations this target actually built."""
    return {*(catalog.get("nodes") or {}), *(catalog.get("sources") or {})}


def _column_nodes(
    models: dict[str, Any],
    sources: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
    catalogued: set[str] | None = None,
    traces: dict[str, ColumnTrace] | None = None,
    fed: set[str] | None = None,
) -> list[Node]:
    """Column nodes named the dbt way; `warehouse_name` carries the other spelling.

    The property is set only when the warehouse spells the column differently, so its
    absence means "same as name" (or "not in the catalog") rather than "unknown".

    A column with no data type gets `unknown_type_reason` saying WHY, because a bare
    "unknown" in the app reads as a broken tool rather than as a dev catalog that
    never built this relation (#122). Two different situations, told apart:
      relation_not_in_catalog -- this target never built the model at all
      column_not_in_catalog   -- the model is built, this column is not in it yet
                                 (it exists in the SQL, so it is undeployed, not lost)
    Set only when there is no data type; its absence is never a claim.

    Three more properties carry the sqlglot pass's own findings, so the app can say
    of every column either how it is defined or why stitch could not tell (#147/#148):
      trace_status  -- "traced" / "untraced", i.e. whether a feeds edge reached it
      trace_reason  -- the TraceReason, on untraced columns only
      defined_as    -- the defining projection {kind, sql, upstream, origin}, when
                       known; `origin` is where a passthrough chain was defined (#162)
    Model columns only. A source column is a lineage root -- warehouse-native, no SQL
    behind it -- so all three stay absent rather than claiming it failed to trace.
    Absence means "does not apply / not known", never "no": the same rule the two
    properties above already follow.
    """
    entities = {**models, **sources}
    catalogued = catalogued if catalogued is not None else set()
    traces = traces or {}
    fed = fed or set()
    nodes = []
    for uid, entity_specs in column_specs.items():
        entity = entities[uid]
        for spec in entity_specs.values():
            node_id = column_node_id(uid, spec["name"])
            warehouse_name = spec.get("warehouse_name")
            properties: dict[str, Any] = {}
            if warehouse_name and warehouse_name != spec["name"]:
                properties["warehouse_name"] = warehouse_name
            if not spec["data_type"]:
                properties["unknown_type_reason"] = (
                    "column_not_in_catalog" if uid in catalogued else "relation_not_in_catalog"
                )
            if uid in models and _is_real_column(spec["name"]):
                trace = traces.get(node_id)
                properties["trace_status"] = "traced" if node_id in fed else "untraced"
                if node_id not in fed and trace is not None and trace.reason is not None:
                    properties["trace_reason"] = str(trace.reason)
                if trace is not None and trace.defined_as is not None:
                    properties["defined_as"] = trace.defined_as.model_dump()
            nodes.append(
                Node(
                    node_id=node_id,
                    node_type=NodeType.COLUMN,
                    name=spec["name"],
                    database=entity.get("database"),
                    schema_=entity.get("schema"),
                    table=_physical_table(entity),
                    column=spec["name"],
                    data_type=spec["data_type"],
                    data_type_source=DataTypeSource.CATALOG if spec["data_type"] else None,
                    description=spec["description"],
                    properties=properties,
                )
            )
    return nodes


def _references_edges(
    models: dict[str, Any], sources: dict[str, Any], manifest_nodes: dict[str, Any]
) -> tuple[list[Edge], int]:
    """Returns (edges, seed/snapshot dependency count) -- the latter are real
    dependencies with no node type to point at, so they are counted, not dropped."""
    edges = []
    seed_snapshot_deps = 0
    for uid, model in models.items():
        deps = (model.get("depends_on") or {}).get("nodes") or []
        for dep in dict.fromkeys(deps):
            if dep in models or dep in sources:
                edges.append(
                    Edge(
                        from_=dep,
                        to=uid,
                        edge_type=EdgeType.REFERENCES,
                        confidence=Confidence.EXACT,
                        evidence={"source": "manifest.depends_on"},
                    )
                )
            elif (manifest_nodes.get(dep) or {}).get("resource_type") in ("seed", "snapshot"):
                seed_snapshot_deps += 1
    return edges, seed_snapshot_deps


def _add_relation(
    mapping: dict[str, Any],
    database: str | None,
    schema: str,
    table: str,
    columns: dict[str, str],
) -> None:
    level = mapping
    for part in [database, schema] if database else [schema]:
        level = level.setdefault(str(part), {})
    level[table] = columns


def _sqlglot_schema(
    models: dict[str, Any],
    sources: dict[str, Any],
    catalog_specs: dict[str, dict[str, dict[str, Any]]],
    catalog: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    """Seed sqlglot schema mapping, plus the uids whose entry came from manifest columns.

    Seed because _column_specs then replaces each model relation with the column set its
    compiled SQL projects; this is the warehouse-shaped starting point that the first
    models in dependency order expand their stars against.

    The catalog is authoritative, but a dev catalog only holds the relations that
    developer happens to have built -- every other relation would fail schema-qualified
    resolution and take its whole downstream subtree untraced with it. Relations absent
    from the catalog therefore fall back to their manifest (schema.yml) columns: types
    are unknown there, but lineage resolves on names alone.

    Relation keys are compared case-insensitively across the two passes: catalog
    metadata carries warehouse casing (SMITTEN_PROD.SEEDS.USER_MASTER_ID) and the
    manifest project casing, and sqlglot merges the two spellings into one relation --
    so a stale schema.yml would otherwise graft phantom columns onto a built table.
    """
    mapping: dict[str, Any] = {}
    catalog_entries = {**(catalog.get("nodes") or {}), **(catalog.get("sources") or {})}
    claimed: set[tuple[str, str, str]] = set()
    for entry in catalog_entries.values():
        metadata = entry.get("metadata") or {}
        database, schema, table = (
            metadata.get("database"),
            metadata.get("schema"),
            metadata.get("name"),
        )
        if not schema or not table:
            continue
        claimed.add((str(database or "").lower(), schema.lower(), table.lower()))
        _add_relation(
            mapping,
            database,
            schema,
            table,
            {
                (column.get("name") or str(key)): (column.get("type") or "TEXT")
                for key, column in (entry.get("columns") or {}).items()
            },
        )

    manifest_sourced: set[str] = set()
    for uid, entity in {**models, **sources}.items():
        # ephemerals are compiled inline as CTEs -- they are never a real relation
        if uid in catalog_entries or (uid in models and _is_ephemeral(entity)):
            continue
        database, schema = entity.get("database"), entity.get("schema")
        table = _physical_table(entity)
        columns = {
            spec["name"]: spec["data_type"] or "UNKNOWN"
            for spec in catalog_specs.get(uid, {}).values()
            if _is_real_column(spec["name"])
        }
        if not schema or not table or not columns:
            continue
        key = (str(database or "").lower(), schema.lower(), table.lower())
        if key in claimed:
            continue
        claimed.add(key)
        _add_relation(mapping, database, schema, table, columns)
        manifest_sourced.add(uid)
    return mapping, manifest_sourced


def _relation_map(models: dict[str, Any], sources: dict[str, Any]) -> dict[tuple, str]:
    mapping: dict[tuple, str] = {}
    for uid, entity in {**models, **sources}.items():
        if uid in models and _is_ephemeral(entity):
            continue
        key = (
            str(entity.get("database") or "").lower(),
            str(entity.get("schema") or "").lower(),
            _physical_table(entity).lower(),
        )
        mapping[key] = uid
    return mapping


def _has_projection_star(parsed: exp.Expression) -> bool:
    for select in parsed.find_all(exp.Select):
        for projection in select.expressions:
            if isinstance(projection, exp.Star):
                return True
            if isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
                return True
    return False


def _is_plain_projection(root) -> bool:
    for node in root.walk():
        if isinstance(node.source, exp.Table):
            continue
        expression = node.expression
        if isinstance(expression, exp.Alias):
            expression = expression.unalias()
        if not isinstance(expression, exp.Column):
            return False
    return True


def _is_real_column(name: str) -> bool:
    return bool(name) and name != "*"


def _explicit_output_names(parsed: exp.Expression) -> set[str]:
    """Lowercased output names the query projects by name (i.e. not via a star)."""
    return set(_output_projections(parsed))


def _leaf_columns(root, relation_map: dict[tuple, str]) -> list[tuple[str, str]]:
    leaves = []
    for node in root.walk():
        if not isinstance(node.source, exp.Table) or node.downstream:
            continue
        table = node.source
        key = (table.catalog.lower(), table.db.lower(), table.name.lower())
        upstream_uid = relation_map.get(key)
        if upstream_uid is None:
            continue
        column = node.name.rpartition(".")[2]
        # sqlglot emits a leaf literally named "*" when a `select *` branch reads a
        # table absent from the catalog schema -- a phantom "{uid}::*" endpoint, not
        # a real column. Skip it; the downstream column goes untraced instead.
        if not _is_real_column(column):
            continue
        leaves.append((upstream_uid, column))
    return leaves


def _foreign_relations(parsed: exp.Expression, relation_map: dict[tuple, str]) -> bool:
    """Does this query read a schema-qualified relation dbt does not own?

    CTEs and bare table names are not counted: the first are internal to the query
    and the second cannot be matched against a (database, schema, table) key with any
    confidence. Only a relation the SQL names in full and the project does not have.
    """
    ctes = {cte.alias_or_name.lower() for cte in parsed.find_all(exp.CTE)}
    for table in parsed.find_all(exp.Table):
        if not table.db or table.name.lower() in ctes:
            continue
        key = (table.catalog.lower(), table.db.lower(), table.name.lower())
        if key not in relation_map:
            return True
    return False


def _no_leaf_reason(root, relation_map: dict[tuple, str], foreign_relations: bool) -> TraceReason:
    """Why a lineage walk that succeeded still reached no upstream column.

    sqlglot leaves say which gap it is, and they are different fixes:

      a leaf named "*" over a relation the project owns  -- the star could not be
        expanded, so document the upstream and the whole subtree resolves
      a Table leaf whose relation is not a dbt model/source -- the SQL reads a table
        dbt does not own; declare it as a source
      a Placeholder leaf -- sqlglot could not resolve the upstream at all because it
        is in neither the catalog nor schema.yml. The single biggest real-world
        cluster: a dev catalog only holds what that developer built. When the query
        also names a relation outside the project that is the more fundamental
        finding, so it wins.
      no leaf at all -- a literal or constant with genuinely nothing upstream, which
        is a fact about the column rather than a gap in the build

    The star case wins a tie: it is the one that unblocks other columns too.
    """
    saw_star = False
    saw_foreign_table = False
    saw_placeholder = False
    for node in root.walk():
        if node.downstream:
            continue
        if isinstance(node.source, exp.Placeholder):
            saw_placeholder = True
        elif isinstance(node.source, exp.Table):
            table = node.source
            key = (table.catalog.lower(), table.db.lower(), table.name.lower())
            if relation_map.get(key) is None:
                saw_foreign_table = True
            elif not _is_real_column(node.name.rpartition(".")[2]):
                saw_star = True
    if saw_star:
        return TraceReason.STAR_NOT_EXPANDABLE
    if saw_foreign_table:
        return TraceReason.UPSTREAM_NOT_IN_PROJECT
    if saw_placeholder:
        return (
            TraceReason.UPSTREAM_NOT_IN_PROJECT
            if foreign_relations
            else TraceReason.UPSTREAM_NOT_IN_SCHEMA_MAP
        )
    return TraceReason.NO_UPSTREAM_COLUMNS


def _entity_name(uid: str, models: dict[str, Any], sources: dict[str, Any]) -> str:
    return str((models.get(uid) or sources.get(uid) or {}).get("name") or uid)


def _column_display_name(
    uid: str,
    leaf_column: str,
    column_specs: dict[str, dict[str, dict[str, Any]]],
) -> str:
    """A leaf column in the dbt spelling, never the parser's.

    sqlglot hands back dialect-normalized leaf names (SCREAMING_CASE on Snowflake),
    which is not how anyone refers to the column; the upstream's own resolved spec is.
    """
    spec = column_specs.get(uid, {}).get(leaf_column.lower())
    return spec["name"] if spec else leaf_column.lower()


def _upstream_column_label(
    uid: str,
    leaf_column: str,
    models: dict[str, Any],
    sources: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
) -> str:
    """`stg_orders.order_id` -- the dbt spelling on both sides."""
    name = _column_display_name(uid, leaf_column, column_specs)
    return f"{_entity_name(uid, models, sources)}.{name}"


def _defined_as(
    lower: str,
    projections: dict[str, exp.Expression],
    star_sql: str,
    has_star: bool,
    leaves: list[tuple[str, str]],
    models: dict[str, Any],
    sources: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
) -> tuple[DefinedAs | None, tuple[str, str] | None]:
    """(the column's defining projection, the single upstream column it reads).

    The definition is None when the SQL does not name the column: "this build cannot
    say", never "nothing" -- a column documented in schema.yml but absent from the
    projection has no definition to show, and saying so is the point of #148's shared
    surface with the untraced reason.

    The second element is the chain link for #162's origin walk, set only when the
    projection reads exactly one upstream column -- which is what makes a hop
    unambiguous, for a star the same way as for a named passthrough.
    """
    distinct = list(dict.fromkeys(leaves))
    ref = distinct[0] if len(distinct) == 1 else None
    projection = projections.get(lower)
    if projection is None:
        if not has_star:
            return None, None
        upstreams = list(dict.fromkeys(uid for uid, _ in leaves))
        # Deliberately not name-matching a multi-leaf star against its single upstream
        # relation: `select *` does carry columns through by name, but that link is an
        # inference (_star_fallback_edges grades its own name matches INFERRED) and the
        # origin is stated in the panel as a fact. Measured on the Smitten project it
        # would have added 56 links and resolved 0 further origins -- all cost, no
        # answer. Every hop the walk follows is one sqlglot resolved.
        return (
            DefinedAs(
                kind="star",
                sql=star_sql,
                upstream=_entity_name(upstreams[0], models, sources)
                if len(upstreams) == 1
                else None,
            ),
            ref,
        )
    inner = projection.unalias() if isinstance(projection, exp.Alias) else projection
    if isinstance(inner, exp.Column) and not isinstance(inner.this, exp.Star):
        upstream = (
            _upstream_column_label(*distinct[0], models, sources, column_specs) if ref else None
        )
        return (
            DefinedAs(
                kind="passthrough",
                sql=_truncate_sql(inner.sql(dialect=_DIALECT)),
                upstream=upstream,
            ),
            ref,
        )
    # an expression is its own origin, so it needs no link even when it reads one column
    return DefinedAs(kind="expression", sql=_truncate_sql(inner.sql(dialect=_DIALECT))), None


def _column_origin(
    start: str,
    traces: dict[str, ColumnTrace],
    models: dict[str, Any],
    sources: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
) -> DefinedOrigin | None:
    """Walk `start`'s passthrough chain to where the column was defined (#162).

    Every hop is a single unambiguous upstream column (ColumnTrace.upstream_ref), so
    the walk is exact rather than a name match. It ends on the first hop that is not
    itself a passthrough: a computed expression, or a source column, which is a
    lineage root and as far upstream as anything goes.

    Returns None when the chain dies instead of ending -- an ambiguous hop, an
    upstream this build never traced, a passthrough of a column no longer in the
    graph. Reporting the last reachable hop there would put a model name next to the
    word "defined" without having established that anything is defined in it.
    """
    ref = traces[start].upstream_ref
    seen = {start}
    hops = 0
    while ref is not None:
        uid, leaf = ref
        node_id = column_node_id(uid, leaf)
        # a dbt DAG is acyclic, so this is a guard against our own bookkeeping
        if node_id in seen:
            return None
        seen.add(node_id)
        hops += 1
        if uid in sources:
            return DefinedOrigin(
                kind="source",
                model=_entity_name(uid, models, sources),
                column=_column_display_name(uid, leaf, column_specs),
                hops=hops,
            )
        upstream = traces.get(node_id)
        if upstream is None or upstream.defined_as is None:
            return None
        if upstream.defined_as.kind == "expression":
            return DefinedOrigin(
                kind="expression",
                model=_entity_name(uid, models, sources),
                column=_column_display_name(uid, leaf, column_specs),
                sql=upstream.defined_as.sql,
                hops=hops,
            )
        ref = upstream.upstream_ref
    return None


def _resolve_origins(
    traces: dict[str, ColumnTrace],
    models: dict[str, Any],
    sources: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """Fill in every passthrough's origin, in place.

    A second pass rather than part of the per-model one: a chain runs downstream to
    upstream in no particular model order, so the links only all exist once every
    model has been walked.
    """
    for node_id, trace in traces.items():
        if trace.upstream_ref is None or trace.defined_as is None:
            continue
        trace.defined_as.origin = _column_origin(node_id, traces, models, sources, column_specs)


def _feeds_edges(
    models: dict[str, Any],
    sources: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
    schema_mapping: MappingSchema,
    manifest_sourced: set[str],
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[list[Edge], dict[str, ColumnTrace]]:
    """(feeds edges, per-column-node-id trace metadata).

    The traces are the by-product the sqlglot pass used to throw away: the reason a
    column produced no edge (#147) and the projection that produced the ones it did
    (#148). Keyed by column node id, model columns only -- a source column is a
    lineage root with no SQL behind it.
    """
    relation_map = _relation_map(models, sources)
    edges: list[Edge] = []
    traces: dict[str, ColumnTrace] = {}

    model_ids = sorted(models)
    for done, uid in enumerate(model_ids, start=1):
        model_edges, model_traces = _model_feeds_edges(
            uid,
            models[uid],
            models,
            sources,
            column_specs,
            schema_mapping,
            manifest_sourced,
            relation_map,
        )
        edges.extend(model_edges)
        traces.update(model_traces)
        if on_progress is not None:
            on_progress(done, len(model_ids))
    _resolve_origins(traces, models, sources, column_specs)
    return edges, traces


def _model_feeds_edges(
    uid: str,
    model: dict[str, Any],
    models: dict[str, Any],
    sources: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
    schema_mapping: MappingSchema,
    manifest_sourced: set[str],
    relation_map: dict[tuple, str],
) -> tuple[list[Edge], dict[str, ColumnTrace]]:
    specs = column_specs.get(uid, {})
    real_specs = {lower: spec for lower, spec in specs.items() if _is_real_column(spec["name"])}

    def all_untraced(reason: TraceReason) -> dict[str, ColumnTrace]:
        return {
            column_node_id(uid, spec["name"]): ColumnTrace(reason=reason)
            for spec in real_specs.values()
        }

    compiled = model.get("compiled_code") or model.get("compiled_sql") or ""
    if not compiled.strip():
        return [], all_untraced(TraceReason.NO_COMPILED_CODE)
    try:
        parsed = sqlglot.parse_one(compiled, dialect=_DIALECT)
    except SqlglotError:
        return [], all_untraced(TraceReason.UNPARSEABLE_SQL)
    has_star = _has_projection_star(parsed)
    projections = _output_projections(parsed)
    explicit_outputs = set(projections)
    star_sql = _star_projection_sql(parsed) if has_star else "*"
    foreign_relations = _foreign_relations(parsed, relation_map)
    deps = [
        dep
        for dep in dict.fromkeys((model.get("depends_on") or {}).get("nodes") or [])
        if dep in models or dep in sources
    ]

    edges: list[Edge] = []
    traces: dict[str, ColumnTrace] = {}
    for lower, spec in real_specs.items():
        target_id = column_node_id(uid, spec["name"])
        try:
            root = _sqlglot_lineage(
                spec["name"], parsed.copy(), schema=schema_mapping, dialect=_DIALECT
            )
        except SqlglotError:
            fallback = (
                _star_fallback_edges(lower, target_id, deps, column_specs) if has_star else []
            )
            edges.extend(fallback)
            # An output the query names and sqlglot still cannot walk is a parser
            # limit. One the query does NOT name is not a failure at all: it either
            # arrives through a star that would not expand, or it exists only as a
            # schema.yml claim about a column the SQL never projects.
            traces[target_id] = ColumnTrace(
                defined_as=_defined_as(
                    lower, projections, star_sql, has_star, [], models, sources, column_specs
                )[0],
                reason=None
                if fallback
                else (
                    TraceReason.LINEAGE_FAILED
                    if lower in explicit_outputs
                    else (
                        TraceReason.STAR_NOT_EXPANDABLE
                        if has_star
                        else TraceReason.COLUMN_NOT_IN_SQL
                    )
                ),
            )
            continue
        leaves = _leaf_columns(root, relation_map)
        defined_as, upstream_ref = _defined_as(
            lower, projections, star_sql, has_star, leaves, models, sources, column_specs
        )
        if not leaves:
            traces[target_id] = ColumnTrace(
                defined_as=defined_as,
                reason=_no_leaf_reason(root, relation_map, foreign_relations),
            )
            continue
        traces[target_id] = ColumnTrace(defined_as=defined_as, upstream_ref=upstream_ref)
        confidence = Confidence.EXACT if _is_plain_projection(root) else Confidence.PARSED
        evidence = {
            "source": "sqlglot.lineage",
            "sql": root.expression.sql(dialect=_DIALECT),
        }
        star_expanded = has_star and lower not in explicit_outputs
        for upstream_uid, leaf_column in dict.fromkeys(leaves):
            # a star expanded over an upstream we only know from schema.yml is a
            # name match dressed up as resolution -- same grade as the fallback below
            if star_expanded and upstream_uid in manifest_sourced:
                edge_confidence = Confidence.INFERRED
                edge_evidence = {**evidence, "schema_source": "manifest_columns"}
            else:
                edge_confidence, edge_evidence = confidence, evidence
            edges.append(
                Edge(
                    from_=column_node_id(upstream_uid, leaf_column),
                    to=target_id,
                    edge_type=EdgeType.FEEDS,
                    confidence=edge_confidence,
                    evidence=edge_evidence,
                )
            )
    return edges, traces


def _star_fallback_edges(
    lower: str,
    target_id: str,
    deps: list[str],
    column_specs: dict[str, dict[str, dict[str, Any]]],
) -> list[Edge]:
    edges = []
    for dep in deps:
        dep_spec = column_specs.get(dep, {}).get(lower)
        if dep_spec is None:
            continue
        edges.append(
            Edge(
                from_=column_node_id(dep, dep_spec["name"]),
                to=target_id,
                edge_type=EdgeType.FEEDS,
                confidence=Confidence.INFERRED,
                evidence={"source": "star-expansion name match"},
            )
        )
    return edges


def _make_target_resolver(models: dict[str, Any]):
    def resolve(target_raw: str) -> tuple[str | None, str | None]:
        parts = [part for part in str(target_raw).strip().split(".") if part]
        if not parts:
            return None, "empty target"
        name = parts[-1].lower()
        schema = parts[-2].lower() if len(parts) >= 2 else None
        matches = [
            uid
            for uid, model in models.items()
            if name
            in {
                str(model.get("name") or "").lower(),
                str(model.get("alias") or model.get("name") or "").lower(),
            }
            and (schema is None or str(model.get("schema") or "").lower() == schema)
        ]
        if not matches:
            return None, "target model not found"
        if len(matches) > 1:
            return None, "target model ambiguous"
        return matches[0], None

    return resolve


def _relationship_tests(
    manifest_nodes: dict[str, Any], models: dict[str, Any]
) -> list[dict[str, Any]]:
    resolve_target = _make_target_resolver(models)
    tests = []
    for uid, node in manifest_nodes.items():
        if node.get("resource_type") != "test":
            continue
        test_metadata = node.get("test_metadata") or {}
        if test_metadata.get("name") != "relationships":
            continue
        kwargs = test_metadata.get("kwargs") or {}
        fk_column = node.get("column_name") or kwargs.get("column_name")
        to_column = kwargs.get("field")
        deps = [dep for dep in (node.get("depends_on") or {}).get("nodes") or [] if dep in models]
        to_uid = None
        ref_match = _REF_RE.search(str(kwargs.get("to") or ""))
        if ref_match:
            to_uid, _ = resolve_target(ref_match.group(1))
        fk_uid = node.get("attached_node")
        if fk_uid not in models:
            candidates = [dep for dep in deps if dep != to_uid]
            fk_uid = candidates[0] if len(candidates) == 1 else None
        if to_uid is None:
            candidates = [dep for dep in deps if dep != fk_uid]
            to_uid = candidates[0] if len(candidates) == 1 else None
        if fk_uid and fk_column and to_uid and to_column:
            tests.append(
                {
                    "test_id": uid,
                    "fk_uid": fk_uid,
                    "fk_column": str(fk_column).lower(),
                    "to_uid": to_uid,
                    "to_column": str(to_column).lower(),
                }
            )
    return tests


def _relates_to_edges(
    manifest_nodes: dict[str, Any],
    models: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
    fk_meta_keys: tuple[str, str] = _DEFAULT_FK_META_KEYS,
    cardinality_meta_key: str = _DEFAULT_CARDINALITY_META_KEY,
) -> tuple[list[Edge], list[str]]:
    resolve_target = _make_target_resolver(models)
    tests = _relationship_tests(manifest_nodes, models)
    test_lookup = {
        (test["fk_uid"], test["fk_column"], test["to_uid"], test["to_column"]): test["test_id"]
        for test in tests
    }
    consumed: set[tuple] = set()
    dangling: list[str] = []
    edges: dict[tuple[str, str], Edge] = {}

    def add_column_edge(
        from_uid: str, from_column: str, to_uid: str, to_column: str, evidence: dict[str, Any]
    ) -> None:
        key = (from_uid, from_column.lower(), to_uid, to_column.lower())
        from_id = column_node_id(from_uid, from_column)
        to_id = column_node_id(to_uid, to_column)
        confidence = Confidence.DECLARED
        if key in test_lookup:
            confidence = Confidence.VALIDATED
            consumed.add(key)
            evidence = {**evidence, "validated_by": test_lookup[key]}
        existing = edges.get((from_id, to_id))
        if existing is not None and (
            existing.confidence == Confidence.VALIDATED or confidence == Confidence.DECLARED
        ):
            return
        edges[(from_id, to_id)] = Edge(
            from_=from_id,
            to=to_id,
            edge_type=EdgeType.RELATES_TO,
            confidence=confidence,
            evidence=evidence,
        )

    def declare(
        from_uid: str, from_column: str, target_raw: str, to_column: str, evidence: dict[str, Any]
    ) -> None:
        model_name = models[from_uid].get("name") or from_uid
        label = f"{model_name}.{from_column} -> {target_raw}.{to_column}"
        if from_column.lower() not in column_specs.get(from_uid, {}):
            dangling.append(f"{label}: source column not found")
            return
        to_uid, error = resolve_target(target_raw)
        if to_uid is None:
            dangling.append(f"{label}: {error}")
            return
        if to_column.lower() not in column_specs.get(to_uid, {}):
            dangling.append(f"{label}: target column not found")
            return
        add_column_edge(from_uid, from_column, to_uid, to_column, evidence)

    for uid in sorted(models):
        model = models[uid]
        model_name = model.get("name") or uid
        for column_name, column in (model.get("columns") or {}).items():
            column_meta = _merged_meta(column)
            fk_table = column_meta.get(fk_meta_keys[0])
            if fk_table:
                fk_field = column_meta.get(fk_meta_keys[1]) or column_name
                declare(
                    uid,
                    str(column_name),
                    str(fk_table),
                    str(fk_field),
                    {
                        "source": "column_meta",
                        "keys": list(fk_meta_keys),
                        "relationship_type": column_meta.get(cardinality_meta_key),
                    },
                )
            for constraint in column.get("constraints") or []:
                if constraint.get("type") != "foreign_key":
                    continue
                target_raw, to_column = _parse_fk_constraint(constraint)
                if not target_raw:
                    dangling.append(
                        f"{model_name}.{column_name} -> ?: unparseable foreign_key constraint"
                    )
                    continue
                declare(
                    uid,
                    str(column_name),
                    target_raw,
                    to_column or str(column_name),
                    {"source": "contract_constraint", "constraint": constraint},
                )

        for entry in _merged_meta(model).get("stitch.relationships") or []:
            if not isinstance(entry, dict):
                continue
            target_raw = str(entry.get("to") or "")
            relationship_type = entry.get("type")
            pairs = entry.get("columns")
            if relationship_type == "related":
                to_uid, error = resolve_target(target_raw)
                if to_uid is None:
                    dangling.append(f"{model_name} -> {target_raw}: {error}")
                    continue
                edges[(uid, to_uid)] = Edge(
                    from_=uid,
                    to=to_uid,
                    edge_type=EdgeType.RELATES_TO,
                    confidence=Confidence.DECLARED,
                    evidence={
                        "source": "model_meta:stitch.relationships",
                        "shape": "conceptual",
                        "relationship_type": "related",
                        "note": entry.get("note"),
                    },
                )
            elif pairs:
                for pair in pairs:
                    if not isinstance(pair, list | tuple) or len(pair) != 2:
                        dangling.append(
                            f"{model_name} -> {target_raw}: malformed composite column pair"
                        )
                        continue
                    declare(
                        uid,
                        str(pair[0]),
                        target_raw,
                        str(pair[1]),
                        {
                            "source": "model_meta:stitch.relationships",
                            "shape": "composite",
                            "columns": pairs,
                            "relationship_type": relationship_type,
                        },
                    )
            else:
                dangling.append(
                    f"{model_name} -> {target_raw}: stitch.relationships entry has no "
                    "columns and is not conceptual"
                )

    for test in tests:
        key = (test["fk_uid"], test["fk_column"], test["to_uid"], test["to_column"])
        if key in consumed:
            continue
        model_name = models[test["fk_uid"]].get("name") or test["fk_uid"]
        target_name = models[test["to_uid"]].get("name") or test["to_uid"]
        label = f"{model_name}.{test['fk_column']} -> {target_name}.{test['to_column']}"
        if test["fk_column"] not in column_specs.get(test["fk_uid"], {}):
            dangling.append(f"{label}: source column not found")
            continue
        if test["to_column"] not in column_specs.get(test["to_uid"], {}):
            dangling.append(f"{label}: target column not found")
            continue
        from_id = column_node_id(test["fk_uid"], test["fk_column"])
        to_id = column_node_id(test["to_uid"], test["to_column"])
        # A relationships test states that the two columns join; it has no field for
        # the ARITY, so that is read from the FK column's own meta (#134). Without
        # this a one-to-one drawn in the app came back as a many-to-one after the
        # next build, and the ERD drew the wrong relationship.
        fk_meta = _merged_meta(
            (models[test["fk_uid"]].get("columns") or {}).get(test["fk_column"]) or {}
        )
        edges[(from_id, to_id)] = Edge(
            from_=from_id,
            to=to_id,
            edge_type=EdgeType.RELATES_TO,
            confidence=Confidence.VALIDATED,
            evidence={
                "source": "relationships_test",
                "test": test["test_id"],
                "relationship_type": fk_meta.get(cardinality_meta_key),
            },
        )

    return list(edges.values()), dangling


def _parse_fk_constraint(constraint: dict[str, Any]) -> tuple[str | None, str | None]:
    to = constraint.get("to")
    to_columns = constraint.get("to_columns") or []
    if to:
        ref_match = _REF_RE.search(str(to))
        target = ref_match.group(1) if ref_match else str(to)
        return target, str(to_columns[0]) if to_columns else None
    expression = constraint.get("expression")
    if expression:
        match = _FK_EXPRESSION_RE.match(str(expression))
        if match:
            return match.group("target"), match.group("column").split(",")[0].strip()
    return None, None
