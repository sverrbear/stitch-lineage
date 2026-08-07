"""Resolve dbt manifest + catalog into graph nodes and edges (SPEC.md sections 7.1, 7.3, 8.1)."""

import re
from collections.abc import Callable
from typing import Any

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage as _sqlglot_lineage

from stitch_lineage.graph.schema import (
    Confidence,
    Edge,
    EdgeType,
    Node,
    NodeType,
    column_node_id,
)

_DIALECT = "snowflake"
_REF_RE = re.compile(r"ref\(\s*(?:['\"][^'\"]+['\"]\s*,\s*)?['\"]([^'\"]+)['\"]")
_FK_EXPRESSION_RE = re.compile(r"^\s*(?P<target>[^()]+?)\s*\(\s*(?P<column>[^()]+?)\s*\)\s*$")


class DbtResolution(BaseModel):
    """Output of resolve_dbt: the dbt side of the graph plus its coverage counters.

    Coverage fields map 1:1 onto graph.schema.Coverage (columns_traced/columns_total/
    columns_inferred/untraced_columns/dangling_relationships/seed_snapshot_dependencies);
    the CLI copies them over.

    nodes/edges come out in resolver order -- io.graph_store canonicalizes on write.
    """

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
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

    Produces:
      * source/model Nodes (node_id = dbt unique_id) from manifest nodes/sources.
      * column Nodes (node_id via schema.column_node_id) -- types from the catalog,
        manifest columns as fallback for views/ephemerals absent from the catalog.
      * `references` edges (upstream model -> downstream model) from manifest
        depends_on, confidence exact. Seeds and snapshots are not node types in the
        graph, so those dependencies carry no edge and are counted in
        seed_snapshot_dependencies instead of vanishing silently.
      * `feeds` edges (upstream column -> downstream column) via sqlglot.lineage over
        each model's compiled_code, dialect="snowflake", schema-qualified from the
        catalog. Plain projections/renames -> confidence exact; expressions -> parsed
        (one edge per input column); star-expansion fallback by name-matching ->
        inferred. Unparseable model -> fail soft: keep its `references` edges, add its
        columns to untraced_columns, never blank the graph. Ephemeral hops attribute
        to the parent model; VARIANT sub-paths land on the VARIANT column. A feeds
        edge never carries a "*" or empty column endpoint: when a star branch cannot
        resolve to real upstream columns the downstream column goes untraced instead.
      * `relates_to` edges (FK column -> referenced PK column) read from column meta
        (metabase.fk_target_table/field + relationship_type), model-level
        stitch.relationships meta, existing relationships tests, and contract
        constraints. Meta-only -> confidence declared; backed by a relationships
        test -> validated. Declarations that point at a missing model/column go to
        dangling_relationships (formatted "model.column -> target"), not the edge list.

    Coverage counts model columns only: source columns are lineage roots, so they are
    excluded from columns_total, columns_traced and untraced_columns. A model column is
    traced when at least one feeds edge points into it, and inferred when any of those
    edges came from the star-expansion name-matching fallback.

    Pure: dicts in, nodes/edges out. No filesystem or network access.
    """
    manifest_nodes = manifest.get("nodes") or {}
    models = {
        uid: node for uid, node in manifest_nodes.items() if node.get("resource_type") == "model"
    }
    sources = dict(manifest.get("sources") or {})

    column_specs = _column_specs(models, sources, catalog)
    entity_nodes = _entity_nodes(models, sources)
    column_nodes = _column_nodes(models, sources, column_specs)
    references, seed_snapshot_deps = _references_edges(models, sources, manifest_nodes)
    feeds = _feeds_edges(models, sources, column_specs, catalog, on_progress)
    relates, dangling = _relates_to_edges(
        manifest_nodes, models, column_specs, tuple(fk_meta_keys), cardinality_meta_key
    )

    model_column_ids = {
        column_node_id(uid, spec["name"])
        for uid in models
        for spec in column_specs.get(uid, {}).values()
    }
    fed = {edge.to for edge in feeds}
    inferred_targets = {edge.to for edge in feeds if edge.confidence == Confidence.INFERRED}
    traced = model_column_ids & fed

    return DbtResolution(
        nodes=entity_nodes + column_nodes,
        edges=references + feeds + relates,
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


def _column_specs(
    models: dict[str, Any], sources: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, dict[str, dict[str, Any]]]:
    """Per entity: {lowercased column name: {name, data_type, description}}.

    Catalog is authoritative (column set + data_type) when the entity is present;
    manifest columns fill descriptions and act as full fallback otherwise.
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
                    "name": display,
                    "data_type": column.get("type"),
                    "description": manifest_column.get("description") or None,
                }
        else:
            for lower, column in manifest_columns.items():
                entity_specs[lower] = {
                    "name": column.get("name") or lower,
                    "data_type": column.get("data_type"),
                    "description": column.get("description") or None,
                }
        specs[uid] = entity_specs
    return specs


def _column_nodes(
    models: dict[str, Any],
    sources: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
) -> list[Node]:
    entities = {**models, **sources}
    nodes = []
    for uid, entity_specs in column_specs.items():
        entity = entities[uid]
        for spec in entity_specs.values():
            nodes.append(
                Node(
                    node_id=column_node_id(uid, spec["name"]),
                    node_type=NodeType.COLUMN,
                    name=spec["name"],
                    database=entity.get("database"),
                    schema_=entity.get("schema"),
                    table=_physical_table(entity),
                    column=spec["name"],
                    data_type=spec["data_type"],
                    description=spec["description"],
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


def _sqlglot_schema(catalog: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    entries = {**(catalog.get("nodes") or {}), **(catalog.get("sources") or {})}
    for entry in entries.values():
        metadata = entry.get("metadata") or {}
        database, schema, table = (
            metadata.get("database"),
            metadata.get("schema"),
            metadata.get("name"),
        )
        if not schema or not table:
            continue
        columns = {
            (column.get("name") or str(key)): (column.get("type") or "TEXT")
            for key, column in (entry.get("columns") or {}).items()
        }
        if database:
            mapping.setdefault(database, {}).setdefault(schema, {})[table] = columns
        else:
            mapping.setdefault(schema, {})[table] = columns
    return mapping


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


def _feeds_edges(
    models: dict[str, Any],
    sources: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
    catalog: dict[str, Any],
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Edge]:
    schema_mapping = _sqlglot_schema(catalog)
    relation_map = _relation_map(models, sources)
    edges: list[Edge] = []

    model_ids = sorted(models)
    for done, uid in enumerate(model_ids, start=1):
        edges.extend(
            _model_feeds_edges(
                uid, models[uid], models, sources, column_specs, schema_mapping, relation_map
            )
        )
        if on_progress is not None:
            on_progress(done, len(model_ids))
    return edges


def _model_feeds_edges(
    uid: str,
    model: dict[str, Any],
    models: dict[str, Any],
    sources: dict[str, Any],
    column_specs: dict[str, dict[str, dict[str, Any]]],
    schema_mapping: dict[str, Any],
    relation_map: dict[tuple, str],
) -> list[Edge]:
    specs = column_specs.get(uid, {})
    compiled = model.get("compiled_code") or model.get("compiled_sql") or ""
    if not compiled.strip():
        return []
    try:
        parsed = sqlglot.parse_one(compiled, dialect=_DIALECT)
    except SqlglotError:
        return []
    has_star = _has_projection_star(parsed)
    deps = [
        dep
        for dep in dict.fromkeys((model.get("depends_on") or {}).get("nodes") or [])
        if dep in models or dep in sources
    ]

    edges: list[Edge] = []
    for lower, spec in specs.items():
        if not _is_real_column(spec["name"]):
            continue
        target_id = column_node_id(uid, spec["name"])
        try:
            root = _sqlglot_lineage(
                spec["name"], parsed.copy(), schema=schema_mapping, dialect=_DIALECT
            )
        except SqlglotError:
            if has_star:
                edges.extend(_star_fallback_edges(lower, target_id, deps, column_specs))
            continue
        leaves = _leaf_columns(root, relation_map)
        if not leaves:
            continue
        confidence = Confidence.EXACT if _is_plain_projection(root) else Confidence.PARSED
        evidence = {
            "source": "sqlglot.lineage",
            "sql": root.expression.sql(dialect=_DIALECT),
        }
        for upstream_uid, leaf_column in dict.fromkeys(leaves):
            edges.append(
                Edge(
                    from_=column_node_id(upstream_uid, leaf_column),
                    to=target_id,
                    edge_type=EdgeType.FEEDS,
                    confidence=confidence,
                    evidence=evidence,
                )
            )
    return edges


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
        edges[(from_id, to_id)] = Edge(
            from_=from_id,
            to=to_id,
            edge_type=EdgeType.RELATES_TO,
            confidence=Confidence.VALIDATED,
            evidence={"source": "relationships_test", "test": test["test_id"]},
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
