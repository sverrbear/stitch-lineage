"""The staging write API (SPEC.md section 8.2) -- the contract the ERD draws against."""

import pytest
from fastapi.testclient import TestClient

from stitch_lineage.app.server import create_app
from stitch_lineage.graph.schema import Graph, Node, NodeType, column_node_id
from stitch_lineage.io.graph_store import write_graph
from stitch_lineage.io.staged_store import relationship_id, staged_path

ORDERS = "model.demo.fct_orders"
CUSTOMERS = "model.demo.dim_customers"

DRAWN = {
    "from_model": "fct_orders",
    "from_column": "customer_id",
    "to_model": "dim_customers",
    "to_column": "customer_id",
    "cardinality": "many-to-one",
    "shape": "simple",
}
DRAWN_ID = relationship_id("fct_orders", "customer_id", "dim_customers", "customer_id")


def _model(node_id, name):
    return Node(node_id=node_id, node_type=NodeType.MODEL, name=name, schema_="MARTS")


def _column(model_id, name):
    return Node(node_id=column_node_id(model_id, name), node_type=NodeType.COLUMN, name=name)


@pytest.fixture
def graph_path(tmp_path):
    graph = Graph(
        generated_at="2026-08-06T00:00:00+00:00",
        nodes=[
            _model(ORDERS, "fct_orders"),
            _column(ORDERS, "customer_id"),
            _column(ORDERS, "order_total"),
            _model(CUSTOMERS, "dim_customers"),
            _column(CUSTOMERS, "customer_id"),
        ],
    )
    path = tmp_path / ".stitch" / "graph.json"
    write_graph(graph, path)
    return path


@pytest.fixture
def store(tmp_path):
    return staged_path(tmp_path / ".stitch")


@pytest.fixture
def client(graph_path, store):
    return TestClient(create_app(graph_path, None, None, store))


@pytest.fixture
def read_only(graph_path):
    """The app as the static export configures it: no staged_path, so no write surface."""
    return TestClient(create_app(graph_path, None))


def test_list_starts_empty(client):
    response = client.get("/api/staged-relationships")
    assert response.status_code == 200
    assert response.json() == {"relationships": []}


def test_post_returns_201_with_the_stored_entry(client):
    response = client.post("/api/staged-relationships", json=DRAWN)
    assert response.status_code == 201
    payload = response.json()
    assert payload["created"] is True
    entry = payload["relationship"]
    assert entry["id"] == DRAWN_ID
    assert entry["from_model"] == "fct_orders"
    assert entry["to_column"] == "customer_id"
    assert entry["cardinality"] == "many-to-one"
    assert entry["shape"] == "simple"
    assert entry["created_at"]


def test_a_posted_relationship_is_listed_and_persisted(client, store):
    client.post("/api/staged-relationships", json=DRAWN)
    listed = client.get("/api/staged-relationships").json()["relationships"]
    assert [entry["id"] for entry in listed] == [DRAWN_ID]
    # survives a restart: the store is the state, not the process
    assert DRAWN_ID in store.read_text()


def test_cardinality_defaults_so_the_ui_can_post_endpoints_only(client):
    minimal = {key: DRAWN[key] for key in ("from_model", "from_column", "to_model", "to_column")}
    entry = client.post("/api/staged-relationships", json=minimal).json()["relationship"]
    assert entry["cardinality"] == "many-to-one"
    assert entry["shape"] == "simple"


def test_reposting_dedupes_by_id(client):
    client.post("/api/staged-relationships", json=DRAWN)
    response = client.post("/api/staged-relationships", json={**DRAWN, "cardinality": "one-to-one"})
    assert response.status_code == 200
    assert response.json()["created"] is False
    assert len(client.get("/api/staged-relationships").json()["relationships"]) == 1


def test_delete_removes_the_entry(client):
    client.post("/api/staged-relationships", json=DRAWN)
    assert client.delete(f"/api/staged-relationships/{DRAWN_ID}").status_code == 204
    assert client.get("/api/staged-relationships").json() == {"relationships": []}


def test_delete_of_an_unknown_id_is_404(client):
    response = client.delete("/api/staged-relationships/nosuchid")
    assert response.status_code == 404
    assert "nosuchid" in response.json()["detail"]


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"from_model": "fct_ghost"}, "unknown model 'fct_ghost'"),
        ({"to_model": "dim_ghost"}, "unknown model 'dim_ghost'"),
        (
            {"from_column": "ghost_id"},
            "from column 'ghost_id' is not a column of model 'fct_orders'",
        ),
        (
            {"to_column": "ghost_id"},
            "to column 'ghost_id' is not a column of model 'dim_customers'",
        ),
    ],
)
def test_unknown_endpoints_are_422_with_a_clear_message(client, patch, expected):
    response = client.post("/api/staged-relationships", json={**DRAWN, **patch})
    assert response.status_code == 422
    assert expected in response.json()["detail"]


def test_a_rejected_relationship_is_not_staged(client):
    client.post("/api/staged-relationships", json={**DRAWN, "to_model": "dim_ghost"})
    assert client.get("/api/staged-relationships").json() == {"relationships": []}


def test_an_unsupported_cardinality_is_422(client):
    response = client.post("/api/staged-relationships", json={**DRAWN, "cardinality": "sideways"})
    assert response.status_code == 422
    assert "many-to-one" in response.json()["detail"]


def test_many_to_many_is_not_a_stored_shape(client):
    response = client.post("/api/staged-relationships", json={**DRAWN, "shape": "many-to-many"})
    assert response.status_code == 422
    assert "shape" in response.json()["detail"]


def test_a_column_cannot_relate_to_itself(client):
    self_join = {**DRAWN, "to_model": "fct_orders", "to_column": "customer_id"}
    response = client.post("/api/staged-relationships", json=self_join)
    assert response.status_code == 422


def test_a_self_referencing_model_is_allowed_on_a_different_column(client):
    hierarchy = {
        "from_model": "fct_orders",
        "from_column": "order_total",
        "to_model": "fct_orders",
        "to_column": "customer_id",
    }
    assert client.post("/api/staged-relationships", json=hierarchy).status_code == 201


def test_unknown_body_fields_are_rejected(client):
    response = client.post("/api/staged-relationships", json={**DRAWN, "severity": "error"})
    assert response.status_code == 422


def test_meta_advertises_staging_to_the_spa(client):
    assert client.get("/api/meta").json()["staging_enabled"] is True


def test_the_static_export_has_no_staging_endpoints(read_only):
    """No staged_path -> no write surface at all: the export is read-only by construction."""
    assert read_only.get("/api/meta").json()["staging_enabled"] is False
    # the SPA mount answers unknown paths, so absence shows up as "not the API"
    assert read_only.get("/api/staged-relationships").status_code != 200
    assert read_only.post("/api/staged-relationships", json=DRAWN).status_code != 201
    assert read_only.delete(f"/api/staged-relationships/{DRAWN_ID}").status_code != 204


def test_the_read_only_app_never_creates_a_store(read_only, store):
    read_only.post("/api/staged-relationships", json=DRAWN)
    assert not store.exists()


def test_a_corrupt_store_is_a_503_not_a_500(client, store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("relationships: [unclosed\n")
    response = client.get("/api/staged-relationships")
    assert response.status_code == 503
    assert "delete the file" in response.json()["detail"]
