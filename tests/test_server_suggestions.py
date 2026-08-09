"""The suggestion API (issue #30): list, dismiss, and the accept path through staging."""

import pytest
from fastapi.testclient import TestClient

from stitch_lineage.app.server import create_app
from stitch_lineage.graph.schema import (
    Confidence,
    Edge,
    EdgeType,
    Graph,
    Node,
    NodeType,
    column_node_id,
    mb_card_node_id,
    mb_field_node_id,
    relationship_id,
)
from stitch_lineage.io.graph_store import write_graph
from stitch_lineage.io.layout_store import layout_path, read_dismissed
from stitch_lineage.io.staged_store import staged_path

ORDERS = "model.demo.fct_orders"
CUSTOMERS = "model.demo.dim_customers"
SUGGESTED_ID = relationship_id("fct_orders", "customer_id", "dim_customers", "customer_id")


def _node(node_id, node_type, name, **kwargs):
    return Node(node_id=node_id, node_type=node_type, name=name, **kwargs)


@pytest.fixture
def graph_path(tmp_path):
    """fct_orders.customer_id -> dim_customers.customer_id, witnessed by one card."""
    graph = Graph(
        generated_at="2026-08-09T00:00:00+00:00",
        nodes=[
            _node(ORDERS, NodeType.MODEL, "fct_orders", schema_="MARTS"),
            _node(column_node_id(ORDERS, "customer_id"), NodeType.COLUMN, "customer_id"),
            _node(CUSTOMERS, NodeType.MODEL, "dim_customers", schema_="MARTS"),
            _node(column_node_id(CUSTOMERS, "customer_id"), NodeType.COLUMN, "customer_id"),
            _node(
                mb_field_node_id(501),
                NodeType.MB_FIELD,
                "Customer ID",
                properties={"fk_target_field_id": 502},
            ),
            _node(mb_field_node_id(502), NodeType.MB_FIELD, "ID"),
            _node(mb_card_node_id(901), NodeType.MB_CARD, "Orders by country"),
        ],
        edges=[
            Edge(
                from_=column_node_id(ORDERS, "customer_id"),
                to=mb_field_node_id(501),
                edge_type=EdgeType.BINDS_TO,
                confidence=Confidence.EXACT,
            ),
            Edge(
                from_=column_node_id(CUSTOMERS, "customer_id"),
                to=mb_field_node_id(502),
                edge_type=EdgeType.BINDS_TO,
                confidence=Confidence.EXACT,
            ),
            Edge(
                from_=mb_field_node_id(501),
                to=mb_card_node_id(901),
                edge_type=EdgeType.CONSUMED_BY,
                confidence=Confidence.EXACT,
                evidence={"clauses": ["fields"], "implicit_join": True},
            ),
        ],
    )
    path = tmp_path / ".stitch" / "graph.json"
    write_graph(graph, path)
    return path


@pytest.fixture
def store(tmp_path):
    return staged_path(tmp_path / ".stitch")


@pytest.fixture
def layout(tmp_path):
    return layout_path(tmp_path / ".stitch")


@pytest.fixture
def client(graph_path, store, layout):
    return TestClient(create_app(graph_path, None, None, store, layout))


@pytest.fixture
def read_only(graph_path):
    """The app as the static export configures it: no staging, so no suggestions."""
    return TestClient(create_app(graph_path, None))


def test_list_returns_the_suggestion_with_its_evidence(client):
    response = client.get("/api/suggestions")
    assert response.status_code == 200
    [suggestion] = response.json()["suggestions"]
    assert suggestion["id"] == SUGGESTED_ID
    assert suggestion["from_model"] == "fct_orders"
    assert suggestion["from_column"] == "customer_id"
    assert suggestion["to_model"] == "dim_customers"
    assert suggestion["to_column"] == "customer_id"
    assert suggestion["cardinality_guess"] == "many-to-one"
    assert suggestion["source"] == "implicit_join"
    assert suggestion["score"] == 1.0
    assert suggestion["evidence"]["card_ids"] == ["mb_card::901"]


def test_results_are_sorted_by_score_descending(client, graph_path):
    graph = Graph.model_validate_json(graph_path.read_text())
    graph.nodes.extend(
        [
            _node("model.demo.dim_products", NodeType.MODEL, "dim_products", schema_="MARTS"),
            _node(
                column_node_id("model.demo.dim_products", "product_id"),
                NodeType.COLUMN,
                "product_id",
            ),
            _node(column_node_id(ORDERS, "product_id"), NodeType.COLUMN, "product_id"),
        ]
    )
    write_graph(graph, graph_path)
    scores = [entry["score"] for entry in client.get("/api/suggestions").json()["suggestions"]]
    assert scores == sorted(scores, reverse=True)
    assert len(scores) == 2


def test_dismiss_is_204_and_the_suggestion_stays_gone(client, layout):
    assert client.post(f"/api/suggestions/{SUGGESTED_ID}/dismiss").status_code == 204
    assert client.get("/api/suggestions").json() == {"suggestions": []}
    # persisted, not just in-process: a restart re-reads layout.yml
    assert read_dismissed(layout) == [SUGGESTED_ID]


def test_dismissing_an_unknown_id_is_404(client):
    response = client.post("/api/suggestions/nosuchid/dismiss")
    assert response.status_code == 404
    assert "nosuchid" in response.json()["detail"]


def test_dismissing_twice_is_404_the_second_time(client):
    assert client.post(f"/api/suggestions/{SUGGESTED_ID}/dismiss").status_code == 204
    assert client.post(f"/api/suggestions/{SUGGESTED_ID}/dismiss").status_code == 404


def test_accepting_goes_through_staging_and_the_suggestion_disappears(client):
    """No accept endpoint: the frontend posts the pair, exclusion does the rest."""
    accepted = client.post(
        "/api/staged-relationships",
        json={
            "from_model": "fct_orders",
            "from_column": "customer_id",
            "to_model": "dim_customers",
            "to_column": "customer_id",
        },
    )
    assert accepted.status_code == 201
    assert accepted.json()["relationship"]["id"] == SUGGESTED_ID
    assert client.get("/api/suggestions").json() == {"suggestions": []}


def test_un_staging_brings_the_suggestion_back(client):
    client.post(
        "/api/staged-relationships",
        json={
            "from_model": "fct_orders",
            "from_column": "customer_id",
            "to_model": "dim_customers",
            "to_column": "customer_id",
        },
    )
    client.delete(f"/api/staged-relationships/{SUGGESTED_ID}")
    assert [entry["id"] for entry in client.get("/api/suggestions").json()["suggestions"]] == [
        SUGGESTED_ID
    ]


def test_the_static_export_has_no_suggestion_api(read_only):
    routes = {getattr(route, "path", None) for route in read_only.app.routes}
    assert "/api/suggestions" not in routes
    assert "/api/suggestions/{suggestion_id}/dismiss" not in routes
    # unregistered, so the request falls through to the static mount rather than the API
    assert read_only.get("/api/suggestions").status_code == 404
    assert read_only.post(f"/api/suggestions/{SUGGESTED_ID}/dismiss").status_code != 204
    assert read_only.get("/api/meta").json()["staging_enabled"] is False


def test_a_corrupt_layout_file_is_a_503_not_a_crash(client, layout):
    layout.parent.mkdir(parents=True, exist_ok=True)
    layout.write_text("dismissed_suggestions: [\n")
    response = client.get("/api/suggestions")
    assert response.status_code == 503
    assert "layout.yml" in response.json()["detail"]


def test_layout_defaults_beside_the_staged_store(graph_path, store):
    """`create_app` without an explicit layout path still persists dismissals."""
    client = TestClient(create_app(graph_path, None, None, store))
    assert client.post(f"/api/suggestions/{SUGGESTED_ID}/dismiss").status_code == 204
    assert read_dismissed(layout_path(store.parent)) == [SUGGESTED_ID]
