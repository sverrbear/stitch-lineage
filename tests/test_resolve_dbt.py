import json
from pathlib import Path

import pytest

from stitch_lineage.graph.schema import Confidence, EdgeType, NodeType, column_node_id
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


def test_column_nodes_from_catalog(resolution):
    col = _node(resolution, column_node_id(FCT_ORDERS, "ORDER_TOTAL"))
    assert col.node_id == f"{FCT_ORDERS}::order_total"
    assert col.name == "ORDER_TOTAL"
    assert col.data_type == "FLOAT"
    assert col.description == "Order amount in USD."
    assert col.table == "fct_orders"


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
    pivot_columns = {column_node_id(MART_PIVOT, "PIVOT_A"), column_node_id(MART_PIVOT, "PIVOT_B")}
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
    assert resolution.columns_total == 26
    assert resolution.columns_traced == 24
    assert resolution.columns_inferred == 5
    assert resolution.untraced_columns == [
        f"{MART_PIVOT}::pivot_a",
        f"{MART_PIVOT}::pivot_b",
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
    assert resolution.columns_total == len(model_columns) == 26


def test_output_is_deterministic():
    first = resolve_dbt(*_load_fixture_pair())
    second = resolve_dbt(*_load_fixture_pair())
    assert [n.model_dump(by_alias=True) for n in first.nodes] == [
        n.model_dump(by_alias=True) for n in second.nodes
    ]
    assert [e.model_dump(by_alias=True) for e in first.edges] == [
        e.model_dump(by_alias=True) for e in second.edges
    ]


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
    assert edge.evidence == {"source": "relationships_test", "test": "test.demo.rel"}


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


def test_catalog_stays_authoritative_over_manifest_columns():
    # schema.yml is stale (documents legacy_col, misses payment_id); the built table
    # in the catalog decides both what the star expands to and the confidence grade
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
            column_node_id("model.demo.stg_payments", "amount_usd"),
            column_node_id("model.demo.mart_payments", "amount_usd"),
        )
    }
    assert feeds[0].confidence == Confidence.EXACT
    assert "schema_source" not in feeds[0].evidence
    assert column_node_id("model.demo.mart_payments", "legacy_col") in result.untraced_columns
    # the catalog column set also decides which column nodes exist at all
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
