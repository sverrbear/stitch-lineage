import json
from pathlib import Path

import pytest

from stitch_lineage.graph.schema import (
    Confidence,
    DataTypeSource,
    EdgeType,
    Graph,
    NodeType,
    column_node_id,
)
from stitch_lineage.io.graph_store import write_graph
from stitch_lineage.resolve.dbt import DbtResolution, resolve_dbt

FIXTURES = Path(__file__).parent / "fixtures" / "dbt"

STG_USERS = "model.demo.stg_users"
STG_PAYMENTS = "model.demo.stg_payments"
INT_USER_FLAGS = "model.demo.int_user_flags"
DIM_USERS = "model.demo.dim_users"
FCT_ORDERS = "model.demo.fct_orders"
MART_PAYMENTS = "model.demo.mart_payments"
MART_PIVOT = "model.demo.mart_pivot"
RAW_USERS = "source.demo.app.raw_users"
RAW_PAYMENTS = "source.demo.app.raw_payments"


def _load_fixture_pair():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    catalog = json.loads((FIXTURES / "catalog.json").read_text())
    return manifest, catalog


@pytest.fixture(scope="module")
def resolution() -> DbtResolution:
    return resolve_dbt(*_load_fixture_pair())


def _edges(resolution, edge_type):
    return [edge for edge in resolution.edges if edge.edge_type == edge_type]


def _edge_pairs(resolution, edge_type):
    return {(edge.from_, edge.to) for edge in _edges(resolution, edge_type)}


def _node(resolution, node_id):
    return next(node for node in resolution.nodes if node.node_id == node_id)


def _model(name, schema="marts", columns=None, compiled=None, deps=(), meta=None):
    return {
        "unique_id": f"model.demo.{name}",
        "resource_type": "model",
        "name": name,
        "alias": name,
        "database": "analytics",
        "schema": schema,
        "description": "",
        "tags": [],
        "meta": meta or {},
        "config": {"materialized": "table", "meta": {}},
        "original_file_path": f"models/{name}.sql",
        "depends_on": {"nodes": list(deps)},
        **({"compiled_code": compiled} if compiled else {}),
        "columns": columns or {},
    }


def _column(name, constraints=None, meta=None):
    return {
        "name": name,
        "description": "",
        "meta": meta or {},
        "constraints": constraints or [],
    }


# --- node inventory -------------------------------------------------------


def test_entity_node_inventory(resolution):
    models = {n.node_id for n in resolution.nodes if n.node_type == NodeType.MODEL}
    sources = {n.node_id for n in resolution.nodes if n.node_type == NodeType.SOURCE}
    assert models == {
        STG_USERS,
        STG_PAYMENTS,
        INT_USER_FLAGS,
        DIM_USERS,
        FCT_ORDERS,
        MART_PAYMENTS,
        MART_PIVOT,
    }
    assert sources == {RAW_USERS, RAW_PAYMENTS}
    all_ids = {n.node_id for n in resolution.nodes}
    assert "seed.demo.country_codes" not in all_ids
    assert not any(node_id.startswith("test.") for node_id in all_ids)


def test_model_node_payload(resolution):
    fct = _node(resolution, FCT_ORDERS)
    assert fct.name == "fct_orders"
    assert fct.database == "analytics"
    assert fct.schema_ == "marts"
    assert fct.table == "fct_orders"
    assert fct.description == "One row per paid order."
    assert fct.owner == "sverrir"
    assert fct.properties["materialization"] == "table"
    assert fct.properties["tags"] == ["core", "revenue"]
    assert fct.properties["path"] == "models/marts/fct_orders.sql"


def test_source_node_payload(resolution):
    src = _node(resolution, RAW_USERS)
    assert src.node_type == NodeType.SOURCE
    assert src.table == "raw_users"
    assert src.schema_ == "raw"
    assert src.properties["source_name"] == "app"


def test_ephemeral_model_flagged(resolution):
    eph = _node(resolution, INT_USER_FLAGS)
    assert eph.node_type == NodeType.MODEL
    assert eph.properties["is_ephemeral"] is True
    assert eph.properties["materialization"] == "ephemeral"


def test_column_nodes_take_dbt_casing_and_catalog_types(resolution):
    # the warehouse spells it ORDER_TOTAL; the project -- and everyone reading the app --
    # spells it order_total, so that is the name; the catalog only supplies the type
    col = _node(resolution, column_node_id(FCT_ORDERS, "ORDER_TOTAL"))
    assert col.node_id == f"{FCT_ORDERS}::order_total"
    assert col.name == "order_total"
    assert col.column == "order_total"
    assert col.properties["warehouse_name"] == "ORDER_TOTAL"
    assert col.data_type == "FLOAT"
    assert col.description == "Order amount in USD."
    assert col.table == "fct_orders"


def test_star_expanded_column_inherits_the_upstream_dbt_casing(resolution):
    # mart_payments is `select * from stg_payments`: sqlglot expands the star against
    # the upper-cased schema map, so the spelling has to come from the parent model
    col = _node(resolution, column_node_id(MART_PAYMENTS, "PAYMENT_ID"))
    assert col.name == "payment_id"
    assert col.properties["warehouse_name"] == "PAYMENT_ID"


def test_source_columns_take_the_schema_yml_casing(resolution):
    col = _node(resolution, column_node_id(RAW_USERS, "FULL_NAME"))
    assert col.name == "full_name"
    assert col.properties["warehouse_name"] == "FULL_NAME"


def test_catalog_fallback_undoes_the_dialect_case_folding(resolution):
    # mart_pivot's SQL does not parse and it documents no columns, so the catalog is all
    # there is -- and Snowflake's PIVOT_A is a storage artefact, not a chosen spelling
    col = _node(resolution, column_node_id(MART_PIVOT, "PIVOT_A"))
    assert col.name == "pivot_a"
    assert col.properties["warehouse_name"] == "PIVOT_A"


def test_a_deliberately_quoted_identifier_keeps_its_casing(resolution):
    # mixed case can only come from a quoted identifier: that IS somebody's choice
    col = _node(resolution, column_node_id(MART_PIVOT, "quotedCase"))
    assert col.name == "quotedCase"
    assert "warehouse_name" not in col.properties


def test_column_nodes_manifest_fallback(resolution):
    col = _node(resolution, column_node_id(STG_PAYMENTS, "amount_usd"))
    assert col.name == "amount_usd"
    assert col.data_type == "float"
    assert col.description == "Amount in USD."
    created = _node(resolution, column_node_id(STG_PAYMENTS, "created_time"))
    assert created.data_type is None


# --- references -----------------------------------------------------------


def test_references_direction_is_upstream_to_downstream(resolution):
    pairs = _edge_pairs(resolution, EdgeType.REFERENCES)
    assert (RAW_USERS, STG_USERS) in pairs
    assert (STG_USERS, RAW_USERS) not in pairs
    assert (STG_PAYMENTS, FCT_ORDERS) in pairs
    assert (FCT_ORDERS, STG_PAYMENTS) not in pairs
    assert pairs == {
        (RAW_USERS, STG_USERS),
        (RAW_PAYMENTS, STG_PAYMENTS),
        (STG_USERS, INT_USER_FLAGS),
        (INT_USER_FLAGS, DIM_USERS),
        (STG_PAYMENTS, FCT_ORDERS),
        (DIM_USERS, FCT_ORDERS),
        (STG_PAYMENTS, MART_PAYMENTS),
        (FCT_ORDERS, MART_PIVOT),
    }
    for edge in _edges(resolution, EdgeType.REFERENCES):
        assert edge.confidence == Confidence.EXACT
        assert edge.evidence == {"source": "manifest.depends_on"}


# --- feeds ----------------------------------------------------------------


def test_feeds_rename_is_exact(resolution):
    edge = next(
        e
        for e in _edges(resolution, EdgeType.FEEDS)
        if e.from_ == column_node_id(RAW_USERS, "id")
        and e.to == column_node_id(STG_USERS, "user_id")
    )
    assert edge.confidence == Confidence.EXACT
    assert edge.evidence["source"] == "sqlglot.lineage"


def test_feeds_expression_gets_parsed_edge_per_input(resolution):
    target = column_node_id(STG_PAYMENTS, "amount_usd")
    incoming = [e for e in _edges(resolution, EdgeType.FEEDS) if e.to == target]
    assert {e.from_ for e in incoming} == {
        column_node_id(RAW_PAYMENTS, "amount"),
        column_node_id(RAW_PAYMENTS, "fx_rate"),
    }
    assert all(e.confidence == Confidence.PARSED for e in incoming)


def test_feeds_star_over_manifest_columns_is_inferred(resolution):
    # stg_payments is absent from the catalog, so mart_payments' `select *` expands
    # against its schema.yml columns -- a name match, graded inferred like the fallback
    incoming = [
        e for e in _edges(resolution, EdgeType.FEEDS) if e.to.startswith(f"{MART_PAYMENTS}::")
    ]
    assert len(incoming) == 5
    for edge in incoming:
        assert edge.confidence == Confidence.INFERRED
        assert edge.evidence["schema_source"] == "manifest_columns"
        assert edge.from_.split("::")[1] == edge.to.split("::")[1]
        assert edge.from_.startswith(f"{STG_PAYMENTS}::")


def test_feeds_through_ephemeral_cte_land_on_real_upstream(resolution):
    flag_edge = next(
        e
        for e in _edges(resolution, EdgeType.FEEDS)
        if e.to == column_node_id(DIM_USERS, "is_iceland")
    )
    assert flag_edge.from_ == column_node_id(STG_USERS, "country_code")
    assert flag_edge.confidence == Confidence.PARSED

    id_edge = next(
        e
        for e in _edges(resolution, EdgeType.FEEDS)
        if e.to == column_node_id(DIM_USERS, "user_id")
    )
    assert id_edge.from_ == column_node_id(STG_USERS, "user_id")
    assert id_edge.confidence == Confidence.EXACT


def test_ephemeral_models_own_columns_are_traced(resolution):
    edge = next(
        e
        for e in _edges(resolution, EdgeType.FEEDS)
        if e.to == column_node_id(INT_USER_FLAGS, "user_id")
    )
    assert edge.from_ == column_node_id(STG_USERS, "user_id")


def test_unparseable_model_fails_soft(resolution):
    pivot_columns = {
        column_node_id(MART_PIVOT, name) for name in ("PIVOT_A", "PIVOT_B", "quotedCase")
    }
    feeds = _edges(resolution, EdgeType.FEEDS)
    assert not any(e.to in pivot_columns or e.from_ in pivot_columns for e in feeds)
    assert set(resolution.untraced_columns) == pivot_columns
    assert (FCT_ORDERS, MART_PIVOT) in _edge_pairs(resolution, EdgeType.REFERENCES)


# --- relates_to -----------------------------------------------------------


def test_column_meta_fk_promoted_to_validated_by_matching_test(resolution):
    matching = [
        e
        for e in _edges(resolution, EdgeType.RELATES_TO)
        if e.from_ == column_node_id(FCT_ORDERS, "customer_id")
        and e.to == column_node_id(DIM_USERS, "user_id")
    ]
    assert len(matching) == 1
    edge = matching[0]
    assert edge.confidence == Confidence.VALIDATED
    assert edge.evidence["source"] == "column_meta"
    assert edge.evidence["relationship_type"] == "many-to-one"
    assert edge.evidence["validated_by"] == (
        "test.demo.relationships_fct_orders_customer_id__user_id__ref_dim_users_"
    )


def test_composite_relationship_one_edge_per_pair(resolution):
    composite = [
        e for e in _edges(resolution, EdgeType.RELATES_TO) if e.evidence.get("shape") == "composite"
    ]
    assert {(e.from_, e.to) for e in composite} == {
        (column_node_id(MART_PAYMENTS, "user_id"), column_node_id(DIM_USERS, "user_id")),
        (
            column_node_id(MART_PAYMENTS, "country_code"),
            column_node_id(DIM_USERS, "country_code"),
        ),
    }
    expected_pairs = [["user_id", "user_id"], ["country_code", "country_code"]]
    for edge in composite:
        assert edge.confidence == Confidence.DECLARED
        assert edge.evidence["columns"] == expected_pairs
        assert edge.evidence["relationship_type"] == "many-to-one"


def test_conceptual_relationship_links_model_nodes(resolution):
    conceptual = [
        e
        for e in _edges(resolution, EdgeType.RELATES_TO)
        if e.evidence.get("shape") == "conceptual"
    ]
    assert len(conceptual) == 1
    edge = conceptual[0]
    assert edge.from_ == FCT_ORDERS
    assert edge.to == MART_PAYMENTS
    assert edge.confidence == Confidence.DECLARED
    assert edge.evidence["note"] == "Both describe the payments grain."


def test_contract_constraint_fk(resolution):
    edge = next(
        e
        for e in _edges(resolution, EdgeType.RELATES_TO)
        if e.evidence.get("source") == "contract_constraint"
    )
    assert edge.from_ == column_node_id(FCT_ORDERS, "order_id")
    assert edge.to == column_node_id(STG_PAYMENTS, "payment_id")
    assert edge.confidence == Confidence.DECLARED


def test_dangling_declaration_recorded_not_emitted(resolution):
    assert resolution.dangling_relationships == [
        "stg_payments.user_id -> dim_missing.user_id: target model not found"
    ]
    assert not any(
        "dim_missing" in e.from_ or "dim_missing" in e.to
        for e in _edges(resolution, EdgeType.RELATES_TO)
    )


# --- coverage & determinism ------------------------------------------------


def test_coverage_numbers(resolution):
    assert resolution.columns_total == 27
    assert resolution.columns_traced == 24
    assert resolution.columns_inferred == 5
    assert resolution.untraced_columns == [
        f"{MART_PIVOT}::pivot_a",
        f"{MART_PIVOT}::pivot_b",
        f"{MART_PIVOT}::quotedcase",
    ]


def test_source_columns_excluded_from_coverage(resolution):
    source_columns = [
        n
        for n in resolution.nodes
        if n.node_type == NodeType.COLUMN and n.node_id.startswith("source.")
    ]
    assert len(source_columns) == 9
    model_columns = [
        n
        for n in resolution.nodes
        if n.node_type == NodeType.COLUMN and n.node_id.startswith("model.")
    ]
    assert resolution.columns_total == len(model_columns) == 27


def test_output_is_deterministic(tmp_path):
    first = resolve_dbt(*_load_fixture_pair())
    second = resolve_dbt(*_load_fixture_pair())
    assert [n.model_dump(by_alias=True) for n in first.nodes] == [
        n.model_dump(by_alias=True) for n in second.nodes
    ]
    assert [e.model_dump(by_alias=True) for e in first.edges] == [
        e.model_dump(by_alias=True) for e in second.edges
    ]

    # the resolver emits in resolver order; canonical ordering is graph_store's job,
    # so nothing downstream depends on it pre-sorting what the writer sorts again
    path = tmp_path / "graph.json"
    write_graph(Graph(nodes=first.nodes, edges=first.edges), path)
    payload = json.loads(path.read_text())
    node_ids = [node["node_id"] for node in payload["nodes"]]
    assert node_ids == sorted(node_ids)
    edge_keys = [(e["from"], e["to"], e["edge_type"]) for e in payload["edges"]]
    assert edge_keys == sorted(edge_keys)


def test_seed_and_snapshot_dependencies_are_counted_not_dropped():
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.fct_a": _model(
                "fct_a",
                columns={"user_id": _column("user_id")},
                deps=(
                    "seed.demo.country_codes",
                    "snapshot.demo.users_snapshot",
                    "model.demo.dim_b",
                ),
            ),
            "model.demo.dim_b": _model("dim_b", columns={"user_id": _column("user_id")}),
            "seed.demo.country_codes": {"resource_type": "seed", "name": "country_codes"},
            "snapshot.demo.users_snapshot": {"resource_type": "snapshot", "name": "users_snapshot"},
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, {})
    assert result.seed_snapshot_dependencies == 2
    assert _edge_pairs(result, EdgeType.REFERENCES) == {("model.demo.dim_b", "model.demo.fct_a")}


# --- inline-manifest edge cases --------------------------------------------


def test_relationships_test_without_meta_emits_validated_edge():
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.fct_a": _model("fct_a", columns={"user_id": _column("user_id")}),
            "model.demo.dim_b": _model(
                "dim_b", schema="dims", columns={"user_id": _column("user_id")}
            ),
            "test.demo.rel": {
                "unique_id": "test.demo.rel",
                "resource_type": "test",
                "name": "rel",
                "attached_node": "model.demo.fct_a",
                "column_name": "user_id",
                "test_metadata": {
                    "name": "relationships",
                    "kwargs": {"to": "ref('dim_b')", "field": "user_id"},
                },
                "depends_on": {"nodes": ["model.demo.dim_b", "model.demo.fct_a"]},
            },
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, {})
    relates = _edges(result, EdgeType.RELATES_TO)
    assert len(relates) == 1
    edge = relates[0]
    assert edge.from_ == column_node_id("model.demo.fct_a", "user_id")
    assert edge.to == column_node_id("model.demo.dim_b", "user_id")
    assert edge.confidence == Confidence.VALIDATED
    # no cardinality meta on the column, so the arity is simply unknown -- the edge
    # still exists and is still validated (#134)
    assert edge.evidence == {
        "source": "relationships_test",
        "test": "test.demo.rel",
        "relationship_type": None,
    }


def test_contract_constraint_expression_form():
    constraint = {"type": "foreign_key", "expression": "dims.dim_b (user_id)"}
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.fct_a": _model(
                "fct_a", columns={"user_id": _column("user_id", constraints=[constraint])}
            ),
            "model.demo.dim_b": _model(
                "dim_b", schema="dims", columns={"user_id": _column("user_id")}
            ),
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, {})
    relates = _edges(result, EdgeType.RELATES_TO)
    assert len(relates) == 1
    edge = relates[0]
    assert edge.from_ == column_node_id("model.demo.fct_a", "user_id")
    assert edge.to == column_node_id("model.demo.dim_b", "user_id")
    assert edge.confidence == Confidence.DECLARED
    assert edge.evidence["source"] == "contract_constraint"


def test_model_without_compiled_code_is_untraced():
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.fct_a": _model("fct_a", columns={"user_id": _column("user_id")}),
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, {})
    assert _edges(result, EdgeType.FEEDS) == []
    assert result.columns_total == 1
    assert result.columns_traced == 0
    assert result.untraced_columns == [column_node_id("model.demo.fct_a", "user_id")]


def test_custom_fk_meta_keys_and_cardinality_key():
    meta = {"fk_table": "dim_b", "fk_col": "user_id", "cardinality": "many-to-one"}
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.fct_a": _model("fct_a", columns={"user_id": _column("user_id", meta=meta)}),
            "model.demo.dim_b": _model(
                "dim_b", schema="dims", columns={"user_id": _column("user_id")}
            ),
        },
        "sources": {},
    }
    assert _edges(resolve_dbt(manifest, {}), EdgeType.RELATES_TO) == []  # custom keys need config
    result = resolve_dbt(
        manifest, {}, fk_meta_keys=["fk_table", "fk_col"], cardinality_meta_key="cardinality"
    )
    relates = _edges(result, EdgeType.RELATES_TO)
    assert len(relates) == 1
    edge = relates[0]
    assert edge.from_ == column_node_id("model.demo.fct_a", "user_id")
    assert edge.to == column_node_id("model.demo.dim_b", "user_id")
    assert edge.evidence["keys"] == ["fk_table", "fk_col"]
    assert edge.evidence["relationship_type"] == "many-to-one"


def test_union_star_branch_never_emits_phantom_star_edge():
    # sqlglot emits a lineage leaf literally named "*" when a column resolves through
    # one branch of a UNION while another branch is `select *` over a table absent
    # from the catalog -- must never become a "{uid}::*" feeds endpoint
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.fct_subs": _model("fct_subs", compiled="select 1"),
            "model.demo.dim_known": _model(
                "dim_known", compiled="select 1", columns={"user_id": _column("user_id")}
            ),
            "model.demo.mart_union": _model(
                "mart_union",
                compiled=(
                    "select user_id from analytics.marts.dim_known "
                    "union all select * from analytics.marts.fct_subs"
                ),
                deps=["model.demo.dim_known", "model.demo.fct_subs"],
                columns={"user_id": _column("user_id")},
            ),
        },
        "sources": {},
    }
    catalog = {
        "nodes": {
            "model.demo.dim_known": {
                "metadata": {"database": "analytics", "schema": "marts", "name": "dim_known"},
                "columns": {"user_id": {"name": "user_id", "type": "INT"}},
            },
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, catalog)
    feeds = _edges(result, EdgeType.FEEDS)
    assert not any("*" in e.from_ or "*" in e.to for e in feeds)
    assert {(e.from_, e.to) for e in feeds} == {
        (
            column_node_id("model.demo.dim_known", "user_id"),
            column_node_id("model.demo.mart_union", "user_id"),
        )
    }


def test_star_over_upstream_without_columns_goes_untraced():
    # upstream absent from catalog AND without manifest columns: the star fallback
    # finds nothing to name-match -> no feeds edges at all, downstream columns untraced
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.fct_subs": _model("fct_subs", compiled="select 1"),
            "model.demo.viz_subs": _model(
                "viz_subs",
                compiled="select * from analytics.marts.fct_subs",
                deps=["model.demo.fct_subs"],
                columns={"revenue_usd": _column("revenue_usd")},
            ),
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, {})
    assert _edges(result, EdgeType.FEEDS) == []
    assert result.untraced_columns == [column_node_id("model.demo.viz_subs", "revenue_usd")]


def test_star_over_upstream_with_known_columns_still_inferred():
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.fct_subs": _model(
                "fct_subs", compiled="select 1", columns={"revenue_usd": _column("revenue_usd")}
            ),
            "model.demo.viz_subs": _model(
                "viz_subs",
                compiled="select * from analytics.marts.fct_subs",
                deps=["model.demo.fct_subs"],
                columns={"revenue_usd": _column("revenue_usd")},
            ),
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, {})
    feeds = _edges(result, EdgeType.FEEDS)
    inferred = [e for e in feeds if e.to == column_node_id("model.demo.viz_subs", "revenue_usd")]
    assert len(inferred) == 1
    assert inferred[0].from_ == column_node_id("model.demo.fct_subs", "revenue_usd")
    assert inferred[0].confidence == Confidence.INFERRED
    assert not any("*" in e.from_ or "*" in e.to for e in feeds)


# --- manifest columns as the sqlglot schema fallback ------------------------


def _join_manifest(upstream_columns):
    """fct_orders projects an unqualified column across a join: needs a schema map."""
    return {
        "metadata": {},
        "nodes": {
            "model.demo.stg_payments": _model(
                "stg_payments", schema="staging", compiled="select 1", columns=upstream_columns
            ),
            "model.demo.dim_users": _model(
                "dim_users",
                schema="dims",
                compiled="select 1",
                columns={"user_id": _column("user_id")},
            ),
            "model.demo.fct_orders": _model(
                "fct_orders",
                compiled=(
                    "select amount_usd * 2 as double_usd "
                    "from analytics.staging.stg_payments as payments "
                    "left join analytics.dims.dim_users as users "
                    "on payments.payment_id = users.user_id"
                ),
                deps=["model.demo.stg_payments", "model.demo.dim_users"],
                columns={"double_usd": _column("double_usd")},
            ),
        },
        "sources": {},
    }


def test_manifest_columns_resolve_upstream_absent_from_catalog():
    # a dev catalog holds only what that developer built; documented columns still
    # let sqlglot attribute the unqualified amount_usd to the right side of the join
    columns = {"payment_id": _column("payment_id"), "amount_usd": _column("amount_usd")}
    result = resolve_dbt(_join_manifest(columns), {})
    double_usd = column_node_id("model.demo.fct_orders", "double_usd")
    assert {(e.from_, e.to) for e in _edges(result, EdgeType.FEEDS)} == {
        (column_node_id("model.demo.stg_payments", "amount_usd"), double_usd)
    }
    assert _edges(result, EdgeType.FEEDS)[0].confidence == Confidence.PARSED
    assert result.columns_traced == 1
    assert double_usd not in result.untraced_columns


def test_upstream_absent_from_catalog_and_manifest_stays_untraced():
    result = resolve_dbt(_join_manifest(None), {})
    assert _edges(result, EdgeType.FEEDS) == []
    assert result.columns_traced == 0
    assert column_node_id("model.demo.fct_orders", "double_usd") in result.untraced_columns


def test_catalog_stays_authoritative_where_sql_cannot_speak():
    # stg_payments' SQL projects no nameable output, so its set falls back to the built
    # table: stale schema.yml legacy_col stays out, catalog-only payment_id stays in.
    # mart_payments' star expands against that same catalog schema.
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.stg_payments": _model(
                "stg_payments",
                schema="staging",
                compiled="select 1",
                columns={"amount_usd": _column("amount_usd"), "legacy_col": _column("legacy_col")},
            ),
            "model.demo.mart_payments": _model(
                "mart_payments",
                compiled="select * from analytics.staging.stg_payments",
                deps=["model.demo.stg_payments"],
                columns={"amount_usd": _column("amount_usd"), "legacy_col": _column("legacy_col")},
            ),
        },
        "sources": {},
    }
    catalog = {
        "nodes": {
            "model.demo.stg_payments": {
                "metadata": {"database": "analytics", "schema": "staging", "name": "stg_payments"},
                "columns": {
                    "payment_id": {"name": "payment_id", "type": "INT"},
                    "amount_usd": {"name": "amount_usd", "type": "FLOAT"},
                },
            }
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, catalog)
    feeds = _edges(result, EdgeType.FEEDS)
    assert {(e.from_, e.to) for e in feeds} == {
        (
            column_node_id("model.demo.stg_payments", "payment_id"),
            column_node_id("model.demo.mart_payments", "payment_id"),
        ),
        (
            column_node_id("model.demo.stg_payments", "amount_usd"),
            column_node_id("model.demo.mart_payments", "amount_usd"),
        ),
    }
    assert {e.confidence for e in feeds} == {Confidence.EXACT}
    assert all("schema_source" not in e.evidence for e in feeds)
    # the star told us mart_payments has payment_id; schema.yml still claims legacy_col
    assert column_node_id("model.demo.mart_payments", "legacy_col") in result.untraced_columns
    assert column_node_id("model.demo.stg_payments", "payment_id") in result.untraced_columns
    assert column_node_id("model.demo.stg_payments", "legacy_col") not in result.untraced_columns


def test_catalog_and_manifest_relation_casing_do_not_collide():
    # catalog carries warehouse casing, the manifest project casing -- one schema map
    # must hold both spellings of analytics.marts or a whole database silently vanishes
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.dim_users": _model("dim_users", compiled="select 1"),
            "model.demo.stg_new": _model(
                "stg_new",
                compiled="select 1",
                columns={"user_id": _column("user_id"), "amount_usd": _column("amount_usd")},
            ),
            "model.demo.fct_orders": _model(
                "fct_orders",
                compiled=(
                    "select country_code, amount_usd "
                    "from analytics.marts.dim_users as users "
                    "left join analytics.marts.stg_new as new_rows "
                    "on users.user_id = new_rows.user_id"
                ),
                deps=["model.demo.dim_users", "model.demo.stg_new"],
                columns={
                    "country_code": _column("country_code"),
                    "amount_usd": _column("amount_usd"),
                },
            ),
        },
        "sources": {},
    }
    catalog = {
        "nodes": {
            "model.demo.dim_users": {
                "metadata": {"database": "ANALYTICS", "schema": "MARTS", "name": "DIM_USERS"},
                "columns": {
                    "USER_ID": {"name": "USER_ID", "type": "INT"},
                    "COUNTRY_CODE": {"name": "COUNTRY_CODE", "type": "TEXT"},
                },
            }
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, catalog)
    assert {(e.from_, e.to) for e in _edges(result, EdgeType.FEEDS)} == {
        (
            column_node_id("model.demo.dim_users", "COUNTRY_CODE"),
            column_node_id("model.demo.fct_orders", "country_code"),
        ),
        (
            column_node_id("model.demo.stg_new", "amount_usd"),
            column_node_id("model.demo.fct_orders", "amount_usd"),
        ),
    }


def test_stale_manifest_duplicate_never_grafts_columns_onto_a_built_relation():
    # a source declared over a mart the catalog already describes: sqlglot merges the
    # two spellings of one relation, so the stale schema.yml column must not sneak in
    source = {
        "unique_id": "source.demo.app.dim_users",
        "resource_type": "source",
        "name": "dim_users",
        "source_name": "app",
        "identifier": "dim_users",
        "database": "analytics",
        "schema": "marts",
        "columns": {"user_id": _column("user_id"), "stale_col": _column("stale_col")},
    }
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.dim_users": _model("dim_users", compiled="select 1"),
            "model.demo.mart_x": _model(
                "mart_x",
                compiled="select * from analytics.marts.dim_users",
                deps=["model.demo.dim_users"],
                columns={"user_id": _column("user_id"), "stale_col": _column("stale_col")},
            ),
        },
        "sources": {"source.demo.app.dim_users": source},
    }
    catalog = {
        "nodes": {
            "model.demo.dim_users": {
                "metadata": {"database": "ANALYTICS", "schema": "MARTS", "name": "DIM_USERS"},
                "columns": {"USER_ID": {"name": "USER_ID", "type": "INT"}},
            }
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, catalog)
    traced = {e.to for e in _edges(result, EdgeType.FEEDS)}
    assert column_node_id("model.demo.mart_x", "user_id") in traced
    assert column_node_id("model.demo.mart_x", "stale_col") in result.untraced_columns


def test_star_over_relation_missing_from_the_manifest_still_name_matches():
    # the compiled SQL reads a relation no dbt node owns: nothing to expand the star
    # against, so the name-matching fallback (inferred) is still the last resort
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.stg_payments": _model(
                "stg_payments",
                schema="staging",
                compiled="select 1",
                columns={"amount_usd": _column("amount_usd")},
            ),
            "model.demo.mart_payments": _model(
                "mart_payments",
                compiled="select * from raw.legacy.payments",
                deps=["model.demo.stg_payments"],
                columns={"amount_usd": _column("amount_usd")},
            ),
        },
        "sources": {},
    }
    feeds = _edges(resolve_dbt(manifest, {}), EdgeType.FEEDS)
    assert len(feeds) == 1
    assert feeds[0].from_ == column_node_id("model.demo.stg_payments", "amount_usd")
    assert feeds[0].confidence == Confidence.INFERRED
    assert feeds[0].evidence == {"source": "star-expansion name match"}


# --- pre-deploy column sets from compiled SQL -------------------------------


def _built_catalog(columns, uid="model.demo.stg_payments", schema="staging", name="stg_payments"):
    return {
        "nodes": {
            uid: {
                "metadata": {"database": "analytics", "schema": schema, "name": name},
                "columns": {column: {"name": column, "type": type_} for column, type_ in columns},
            }
        },
        "sources": {},
    }


def _stg_manifest(compiled, columns, extra_nodes=None):
    return {
        "metadata": {},
        "nodes": {
            "model.demo.stg_payments": _model(
                "stg_payments", schema="staging", compiled=compiled, columns=columns
            ),
            **(extra_nodes or {}),
        },
        "sources": {},
    }


def _column_ids(result, uid):
    return {
        node.node_id
        for node in result.nodes
        if node.node_type == NodeType.COLUMN and node.node_id.startswith(f"{uid}::")
    }


def test_model_sql_casing_beats_the_catalog():
    # sqlglot normalizes every output name to Snowflake's upper case, so the display
    # spelling has to be read off the unnormalized parse -- here `Amount_USD`
    manifest = _stg_manifest("select amount as Amount_USD from analytics.raw.raw_payments", {})
    result = resolve_dbt(manifest, _built_catalog([("AMOUNT_USD", "FLOAT")]))
    col = _node(result, column_node_id("model.demo.stg_payments", "amount_usd"))
    assert col.name == "Amount_USD"
    assert col.properties["warehouse_name"] == "AMOUNT_USD"


def test_column_dropped_from_sql_disappears_though_the_warehouse_still_has_it():
    # the whole point of issue #10: the PR removed amount_usd, the built table has not
    # caught up yet, and a graph diff must see the removal now rather than post-deploy
    manifest = _stg_manifest(
        "select payment_id from analytics.raw.raw_payments",
        {"payment_id": _column("payment_id")},
    )
    catalog = _built_catalog([("payment_id", "INT"), ("amount_usd", "FLOAT")])
    result = resolve_dbt(manifest, catalog)
    assert _column_ids(result, "model.demo.stg_payments") == {
        column_node_id("model.demo.stg_payments", "payment_id")
    }
    assert result.columns_total == 1


def test_column_added_in_sql_but_not_yet_built_gets_a_node_without_a_type():
    manifest = _stg_manifest(
        "select payment_id, amount * fx_rate as amount_usd from analytics.raw.raw_payments",
        {"payment_id": _column("payment_id")},
    )
    result = resolve_dbt(manifest, _built_catalog([("payment_id", "INT")]))
    added = _node(result, column_node_id("model.demo.stg_payments", "amount_usd"))
    assert added.data_type is None
    assert added.description is None
    # types still flow from the catalog for the columns that do exist there
    assert _node(result, column_node_id("model.demo.stg_payments", "payment_id")).data_type == "INT"
    assert result.columns_total == 2


def test_documented_column_absent_from_the_projection_is_kept():
    # schema.yml is a claim about this model; dropping it silently would read as a
    # deliberate removal in a diff, so the set is projection UNION documentation
    manifest = _stg_manifest(
        "select payment_id from analytics.raw.raw_payments",
        {"payment_id": _column("payment_id"), "amount_usd": _column("amount_usd")},
    )
    result = resolve_dbt(manifest, {})
    assert _column_ids(result, "model.demo.stg_payments") == {
        column_node_id("model.demo.stg_payments", "payment_id"),
        column_node_id("model.demo.stg_payments", "amount_usd"),
    }


def test_unparseable_model_keeps_the_catalog_column_set():
    manifest = _stg_manifest("select payment_id sum(x) pivot for in (,,) from", {})
    catalog = _built_catalog([("payment_id", "INT"), ("amount_usd", "FLOAT")])
    result = resolve_dbt(manifest, catalog)
    assert _column_ids(result, "model.demo.stg_payments") == {
        column_node_id("model.demo.stg_payments", "payment_id"),
        column_node_id("model.demo.stg_payments", "amount_usd"),
    }


def test_unnameable_projection_keeps_the_catalog_column_set():
    # `select 1` / an unaliased expression: sqlglot can only call these "1" and
    # "_col_0", which match nothing -- fall back rather than invent a column set
    catalog = _built_catalog([("payment_id", "INT"), ("amount_usd", "FLOAT")])
    for compiled in ("select 1", "select max(amount_usd) from analytics.raw.raw_payments"):
        result = resolve_dbt(_stg_manifest(compiled, {}), catalog)
        assert _column_ids(result, "model.demo.stg_payments") == {
            column_node_id("model.demo.stg_payments", "payment_id"),
            column_node_id("model.demo.stg_payments", "amount_usd"),
        }, compiled


def test_star_over_known_upstream_expands_to_the_upstream_set():
    manifest = _stg_manifest(
        "select payment_id, amount_usd from analytics.raw.raw_payments",
        {},
        extra_nodes={
            "model.demo.mart_payments": _model(
                "mart_payments",
                compiled=(
                    "select * exclude (payment_id) rename (amount_usd as revenue_usd) "
                    "from analytics.staging.stg_payments"
                ),
                deps=["model.demo.stg_payments"],
                columns={},
            )
        },
    )
    result = resolve_dbt(manifest, _built_catalog([("payment_id", "INT"), ("amount_usd", "FLOAT")]))
    assert _column_ids(result, "model.demo.mart_payments") == {
        column_node_id("model.demo.mart_payments", "revenue_usd")
    }


def test_removal_propagates_through_a_star_to_the_next_model():
    # mart_payments is resolved after stg_payments, against stg_payments' new set --
    # otherwise the star would resurrect the removed column and feed it from a node
    # that no longer exists
    manifest = _stg_manifest(
        "select payment_id from analytics.raw.raw_payments",
        {"payment_id": _column("payment_id")},
        extra_nodes={
            "model.demo.mart_payments": _model(
                "mart_payments",
                compiled="select * from analytics.staging.stg_payments",
                deps=["model.demo.stg_payments"],
                columns={},
            )
        },
    )
    result = resolve_dbt(manifest, _built_catalog([("payment_id", "INT"), ("amount_usd", "FLOAT")]))
    assert _column_ids(result, "model.demo.mart_payments") == {
        column_node_id("model.demo.mart_payments", "payment_id")
    }
    column_ids = {node.node_id for node in result.nodes if node.node_type == NodeType.COLUMN}
    assert {e.from_ for e in _edges(result, EdgeType.FEEDS)} <= column_ids


def test_source_column_sets_stay_catalog_authoritative():
    source = {
        "unique_id": "source.demo.app.raw_payments",
        "resource_type": "source",
        "name": "raw_payments",
        "source_name": "app",
        "identifier": "raw_payments",
        "database": "analytics",
        "schema": "raw",
        "columns": {"documented_only": _column("documented_only")},
    }
    manifest = {"metadata": {}, "nodes": {}, "sources": {"source.demo.app.raw_payments": source}}
    catalog = {
        "sources": {
            "source.demo.app.raw_payments": {
                "metadata": {"database": "analytics", "schema": "raw", "name": "raw_payments"},
                "columns": {"id": {"name": "id", "type": "INT"}},
            }
        },
        "nodes": {},
    }
    result = resolve_dbt(manifest, catalog)
    assert _column_ids(result, "source.demo.app.raw_payments") == {
        column_node_id("source.demo.app.raw_payments", "id")
    }


def test_dangling_target_column_recorded():
    meta = {"metabase.fk_target_table": "dim_b", "metabase.fk_target_field": "missing_col"}
    manifest = {
        "metadata": {},
        "nodes": {
            "model.demo.fct_a": _model("fct_a", columns={"user_id": _column("user_id", meta=meta)}),
            "model.demo.dim_b": _model(
                "dim_b", schema="dims", columns={"user_id": _column("user_id")}
            ),
        },
        "sources": {},
    }
    result = resolve_dbt(manifest, {})
    assert _edges(result, EdgeType.RELATES_TO) == []
    assert result.dangling_relationships == [
        "fct_a.user_id -> dim_b.missing_col: target column not found"
    ]


# --- on_progress ------------------------------------------------------------


def test_on_progress_fires_per_model_with_fixed_total():
    manifest, catalog = _load_fixture_pair()
    model_count = sum(
        1 for node in manifest["nodes"].values() if node.get("resource_type") == "model"
    )
    calls: list[tuple[int, int]] = []

    with_progress = resolve_dbt(
        manifest, catalog, on_progress=lambda done, total: calls.append((done, total))
    )

    assert calls, "on_progress never fired"
    assert {total for _, total in calls} == {model_count}
    dones = [done for done, _ in calls]
    assert dones == sorted(dones), "done must be monotonically nondecreasing"
    assert dones[-1] == model_count, "progress must reach total"
    # the callback is observational only: the resolution is unchanged
    assert with_progress == resolve_dbt(manifest, catalog)


# --- why a data type is unknown (#122) ---------------------------------------


def _unknown_reason(resolution, uid, column):
    node = next(n for n in resolution.nodes if n.node_id == f"{uid}::{column}")
    return node.properties.get("unknown_type_reason")


def test_column_of_an_uncatalogued_relation_says_the_relation_is_missing():
    """A dev target that never built the model -- not a broken tool."""
    manifest = {
        "nodes": {
            "model.demo.only_in_manifest": {
                "resource_type": "model",
                "name": "only_in_manifest",
                "package_name": "demo",
                "database": "DB",
                "schema": "MARTS",
                "alias": "only_in_manifest",
                "columns": {"id": {"name": "id"}},
                "depends_on": {"nodes": []},
            }
        },
        "sources": {},
    }
    resolution = resolve_dbt(manifest, {"nodes": {}, "sources": {}})
    assert (
        _unknown_reason(resolution, "model.demo.only_in_manifest", "id")
        == "relation_not_in_catalog"
    )


def test_column_missing_from_a_built_relation_says_so_instead():
    """The model IS built; this column is in the SQL but not deployed yet."""
    manifest = {
        "nodes": {
            "model.demo.built": {
                "resource_type": "model",
                "name": "built",
                "package_name": "demo",
                "database": "DB",
                "schema": "MARTS",
                "alias": "built",
                "columns": {"id": {"name": "id"}, "brand_new": {"name": "brand_new"}},
                # the column exists in the SQL, which is what makes it undeployed
                # rather than lost -- the catalog simply has not caught up
                "compiled_code": "select 1 as id, 2 as brand_new",
                "depends_on": {"nodes": []},
            }
        },
        "sources": {},
    }
    catalog = {
        "nodes": {
            "model.demo.built": {
                "metadata": {"schema": "MARTS", "name": "built"},
                "columns": {"id": {"name": "id", "type": "NUMBER"}},
            }
        },
        "sources": {},
    }
    resolution = resolve_dbt(manifest, catalog)
    assert _unknown_reason(resolution, "model.demo.built", "brand_new") == "column_not_in_catalog"
    # a column the catalog typed makes no claim at all
    assert _unknown_reason(resolution, "model.demo.built", "id") is None


# --- trace status, the untraced-reason taxonomy and defined_as (#147, #148) ---


def _trace(resolution, uid, column):
    return _node(resolution, column_node_id(uid, column)).properties


def _reason(resolution, uid, column):
    return _trace(resolution, uid, column).get("trace_reason")


def _defined_as(resolution, uid, column):
    return _trace(resolution, uid, column).get("defined_as")


def _traced_manifest(*models):
    return {"metadata": {}, "nodes": {model["unique_id"]: model for model in models}, "sources": {}}


def test_every_model_column_declares_a_trace_status(resolution):
    for node in resolution.nodes:
        if node.node_type != NodeType.COLUMN:
            continue
        owner = node.node_id.split("::")[0]
        if owner.startswith("source."):
            # a source column is a lineage root: warehouse-native, no SQL behind it,
            # so claiming it "failed to trace" would be a lie (#148 point 4)
            assert "trace_status" not in node.properties
            assert "defined_as" not in node.properties
        else:
            assert node.properties["trace_status"] in {"traced", "untraced"}


def test_trace_status_agrees_with_the_untraced_coverage_list(resolution):
    untraced = {
        node.node_id
        for node in resolution.nodes
        if node.properties.get("trace_status") == "untraced"
    }
    assert untraced == set(resolution.untraced_columns)


def test_untraced_columns_all_carry_a_reason(resolution):
    for node_id in resolution.untraced_columns:
        assert _node(resolution, node_id).properties.get("trace_reason")


def test_traced_columns_carry_no_reason(resolution):
    for node in resolution.nodes:
        if node.properties.get("trace_status") == "traced":
            assert "trace_reason" not in node.properties


def test_defined_as_passthrough_names_the_upstream_column(resolution):
    # dim_users.user_id comes through a CTE off stg_users -- a plain projection
    defined = _defined_as(resolution, DIM_USERS, "user_id")
    assert defined["kind"] == "passthrough"
    assert defined["sql"] == "user_id"
    assert defined["upstream"] == "stg_users.user_id"


def test_defined_as_expression_keeps_the_computed_sql(resolution):
    defined = _defined_as(resolution, INT_USER_FLAGS, "is_iceland")
    assert defined["kind"] == "expression"
    assert defined["sql"] == "CASE WHEN country_code = 'IS' THEN 1 ELSE 0 END"
    # an expression has more than one possible upstream, so it names none
    assert defined["upstream"] is None


def test_defined_as_renamed_passthrough_is_still_a_passthrough():
    upstream = _model(
        "stg_o",
        schema="staging",
        compiled="select 1 as amount",
        columns={"amount": _column("amount")},
    )
    downstream = _model(
        "fct_o",
        compiled="select o.amount as gross_amount from analytics.staging.stg_o o",
        deps=["model.demo.stg_o"],
    )
    result = resolve_dbt(_traced_manifest(upstream, downstream), {})
    defined = _defined_as(result, "model.demo.fct_o", "gross_amount")
    assert defined == {"kind": "passthrough", "sql": "o.amount", "upstream": "stg_o.amount"}


def test_defined_as_star_names_the_upstream_relation():
    upstream = _model(
        "stg_u",
        schema="staging",
        compiled="select 1 as user_id",
        columns={"user_id": _column("user_id")},
    )
    downstream = _model(
        "dim_u",
        compiled="select * from analytics.staging.stg_u",
        deps=["model.demo.stg_u"],
        columns={"user_id": _column("user_id")},
    )
    result = resolve_dbt(_traced_manifest(upstream, downstream), {})
    defined = _defined_as(result, "model.demo.dim_u", "user_id")
    assert defined == {"kind": "star", "sql": "*", "upstream": "stg_u"}
    assert _trace(result, "model.demo.dim_u", "user_id")["trace_status"] == "traced"


def test_defined_as_upstream_takes_the_dbt_spelling_not_the_parsers():
    # the catalog spells it SCREAMING_CASE; nobody refers to it that way (#44)
    upstream = _model("stg_u", schema="staging", compiled="select 1 as user_id")
    downstream = _model(
        "dim_u",
        compiled="select u.user_id from analytics.staging.stg_u u",
        deps=["model.demo.stg_u"],
    )
    catalog = {
        "nodes": {
            "model.demo.stg_u": {
                "metadata": {"database": "analytics", "schema": "staging", "name": "stg_u"},
                "columns": {"USER_ID": {"name": "USER_ID", "type": "NUMBER"}},
            }
        },
        "sources": {},
    }
    result = resolve_dbt(_traced_manifest(upstream, downstream), catalog)
    assert _defined_as(result, "model.demo.dim_u", "user_id")["upstream"] == "stg_u.user_id"


def test_defined_as_sql_is_truncated_for_display():
    long_expression = " || ".join(f"cast(part_{i} as varchar)" for i in range(60))
    model = _model(
        "wide", compiled=f"select {long_expression} as joined from analytics.marts.other"
    )
    defined = _defined_as(resolve_dbt(_traced_manifest(model), {}), "model.demo.wide", "joined")
    assert defined["kind"] == "expression"
    assert len(defined["sql"]) == 240
    assert defined["sql"].endswith("…")


def test_reason_no_compiled_code():
    model = _model("fct_a", columns={"user_id": _column("user_id")})
    result = resolve_dbt(_traced_manifest(model), {})
    assert _reason(result, "model.demo.fct_a", "user_id") == "no_compiled_code"
    # nothing was parsed, so there is no definition to claim either
    assert _defined_as(result, "model.demo.fct_a", "user_id") is None


def test_reason_unparseable_sql():
    # one exotic PIVOT must not blank the graph (SPEC 7.3) -- it must say why instead
    model = _model(
        "mart_exotic",
        compiled="select payment_id sum(amount_usd) pivot for in (,,) from",
        columns={"payment_id": _column("payment_id")},
    )
    result = resolve_dbt(_traced_manifest(model), {})
    assert _reason(result, "model.demo.mart_exotic", "payment_id") == "unparseable_sql"
    assert _defined_as(result, "model.demo.mart_exotic", "payment_id") is None


def test_reason_column_not_in_sql():
    model = _model(
        "fct_b",
        compiled="select 1 as a",
        columns={"a": _column("a"), "documented_only": _column("documented_only")},
    )
    result = resolve_dbt(_traced_manifest(model), {})
    assert _reason(result, "model.demo.fct_b", "documented_only") == "column_not_in_sql"
    # a schema.yml claim the SQL never projects has no definition to show
    assert _defined_as(result, "model.demo.fct_b", "documented_only") is None


def test_reason_star_not_expandable():
    # upstream in neither catalog nor schema.yml: the star has nothing to expand against
    upstream = _model("up", schema="staging", compiled="select 1")
    downstream = _model(
        "viz",
        compiled="select * from analytics.staging.up",
        deps=["model.demo.up"],
        columns={"revenue_usd": _column("revenue_usd")},
    )
    result = resolve_dbt(_traced_manifest(upstream, downstream), {})
    assert _reason(result, "model.demo.viz", "revenue_usd") == "star_not_expandable"
    # the reason and the definition share the panel slot: the star is still WHY
    assert _defined_as(result, "model.demo.viz", "revenue_usd") == {
        "kind": "star",
        "sql": "*",
        "upstream": None,
    }


def test_reason_upstream_not_in_schema_map():
    # every relation is a dbt model, but the upstream is documented nowhere -- the
    # dev-catalog case that takes whole subtrees untraced with it
    upstream = _model("undoc", schema="staging")
    downstream = _model(
        "fct_c",
        compiled="select user_id from analytics.staging.undoc",
        deps=["model.demo.undoc"],
        columns={"user_id": _column("user_id")},
    )
    result = resolve_dbt(_traced_manifest(upstream, downstream), {})
    assert _reason(result, "model.demo.fct_c", "user_id") == "upstream_not_in_schema_map"


def test_reason_upstream_not_in_project():
    model = _model("fct_d", compiled="select id from analytics.raw.not_a_dbt_relation")
    result = resolve_dbt(_traced_manifest(model), {})
    assert _reason(result, "model.demo.fct_d", "id") == "upstream_not_in_project"


def test_reason_no_upstream_columns():
    model = _model("fct_e", compiled="select current_timestamp() as loaded_at, 'v1' as version")
    result = resolve_dbt(_traced_manifest(model), {})
    assert _reason(result, "model.demo.fct_e", "loaded_at") == "no_upstream_columns"
    assert _reason(result, "model.demo.fct_e", "version") == "no_upstream_columns"
    # a constant genuinely has nothing upstream -- the definition says so
    assert _defined_as(result, "model.demo.fct_e", "version") == {
        "kind": "expression",
        "sql": "'v1'",
        "upstream": None,
    }


# --- the type waterfall's first step, and the catalog join it rests on (issue #149) ---


@pytest.mark.parametrize(
    ("label", "compiled", "catalog_columns"),
    [
        # the Snowflake shape: unquoted identifiers folded to upper case on the way in,
        # every dbt model and node id lower case. A join that dropped these would report
        # 'unknown' for types the catalog is holding -- silently, for the whole project.
        ("lowercase sql", "select payment_id, amount_usd from analytics.raw.raw_payments", None),
        ("uppercase sql", "select PAYMENT_ID, AMOUNT_USD from analytics.raw.raw_payments", None),
        ("no compiled sql", "", None),
    ],
)
def test_uppercase_catalog_identifiers_land_on_lowercased_column_nodes(
    label, compiled, catalog_columns
):
    manifest = _stg_manifest(
        compiled, {"payment_id": _column("payment_id"), "amount_usd": _column("amount_usd")}
    )
    catalog = _built_catalog(
        catalog_columns or [("PAYMENT_ID", "NUMBER(38,0)"), ("AMOUNT_USD", "FLOAT")]
    )
    result = resolve_dbt(manifest, catalog)
    types = {
        node.node_id: (node.data_type, node.data_type_source)
        for node in result.nodes
        if node.node_type is NodeType.COLUMN
    }
    assert types[column_node_id(STG_PAYMENTS, "payment_id")] == (
        "NUMBER(38,0)",
        DataTypeSource.CATALOG,
    )
    assert types[column_node_id(STG_PAYMENTS, "amount_usd")] == ("FLOAT", DataTypeSource.CATALOG)


def test_catalog_key_cased_differently_from_the_column_name_still_joins():
    """dbt keys catalog columns by the warehouse spelling, but the inner `name` is the
    authority -- a build where the two disagree in case must not drop the type."""
    catalog = _built_catalog([("PAYMENT_ID", "NUMBER(38,0)")])
    columns = catalog["nodes"][STG_PAYMENTS]["columns"]
    columns["payment_id"] = columns.pop("PAYMENT_ID")
    result = resolve_dbt(
        _stg_manifest("select payment_id from analytics.raw.raw_payments", {}), catalog
    )
    node = _node(result, column_node_id(STG_PAYMENTS, "payment_id"))
    assert (node.data_type, node.data_type_source) == ("NUMBER(38,0)", DataTypeSource.CATALOG)


def test_a_column_with_no_type_carries_no_source():
    result = resolve_dbt(_stg_manifest("select payment_id from analytics.raw.raw_payments", {}), {})
    node = _node(result, column_node_id(STG_PAYMENTS, "payment_id"))
    assert node.data_type is None and node.data_type_source is None


def test_type_inference_is_off_by_default():
    manifest = _stg_manifest("select amount * 2 as doubled from analytics.raw.raw_payments", {})
    assert resolve_dbt(manifest, {}).inferred_types == {}


def test_inferred_types_are_candidates_not_applied_types():
    """resolve_dbt hands inference results over rather than writing them onto the nodes:
    the waterfall ranks a parse guess below the warehouse's own answer, and it can only
    do that if the guess has not already been applied here."""
    catalog = _built_catalog(
        [("AMOUNT", "FLOAT")], uid=STG_USERS, schema="staging", name="stg_users"
    )
    manifest = _stg_manifest(
        "select amount * 2 as doubled from analytics.staging.stg_users",
        {},
        extra_nodes={
            STG_USERS: _model("stg_users", schema="staging", compiled="select 1 as amount")
        },
    )
    result = resolve_dbt(manifest, catalog, infer_types=True)
    node = _node(result, column_node_id(STG_PAYMENTS, "doubled"))
    assert node.data_type is None
    assert result.inferred_types[column_node_id(STG_PAYMENTS, "doubled")] == "DOUBLE"


def test_inference_records_nothing_for_expressions_sqlglot_cannot_type():
    """An unmapped UDF annotates to UNKNOWN: 'we parsed it and learned nothing' is the
    same state as never asking, so it must not be recorded as a type."""
    manifest = _stg_manifest(
        "select some_unmapped_udf(x) as weird, null as nothing from analytics.raw.raw_payments",
        {},
    )
    result = resolve_dbt(manifest, {}, infer_types=True)
    assert result.inferred_types == {}


def test_a_relationships_test_carries_the_arity_written_beside_it():
    """#134's round trip: the test states the join, the meta key states the arity.

    dbt has no field for cardinality on a relationships test, so `stitch apply`
    writes it to the column's meta. If the resolver did not read it back, drawing a
    one-to-one and rebuilding would hand back a many-to-one.
    """
    for cardinality in ("one-to-one", "many-to-one", "one-to-many"):
        manifest = {
            "metadata": {},
            "nodes": {
                "model.demo.fct_a": _model(
                    "fct_a",
                    columns={
                        "user_id": {
                            **_column("user_id"),
                            "meta": {"relationship_type": cardinality},
                        }
                    },
                ),
                "model.demo.dim_b": _model(
                    "dim_b", schema="dims", columns={"user_id": _column("user_id")}
                ),
                "test.demo.rel": {
                    "unique_id": "test.demo.rel",
                    "resource_type": "test",
                    "name": "rel",
                    "attached_node": "model.demo.fct_a",
                    "column_name": "user_id",
                    "test_metadata": {
                        "name": "relationships",
                        "kwargs": {"to": "ref('dim_b')", "field": "user_id"},
                    },
                    "depends_on": {"nodes": ["model.demo.dim_b", "model.demo.fct_a"]},
                },
            },
            "sources": {},
        }
        (edge,) = _edges(resolve_dbt(manifest, {}), EdgeType.RELATES_TO)
        assert edge.confidence == Confidence.VALIDATED
        assert edge.evidence["relationship_type"] == cardinality
