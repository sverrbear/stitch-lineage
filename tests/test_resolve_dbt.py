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


def test_feeds_star_fallback_is_inferred(resolution):
    incoming = [
        e for e in _edges(resolution, EdgeType.FEEDS) if e.to.startswith(f"{MART_PAYMENTS}::")
    ]
    assert len(incoming) == 5
    for edge in incoming:
        assert edge.confidence == Confidence.INFERRED
        assert edge.evidence == {"source": "star-expansion name match"}
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
