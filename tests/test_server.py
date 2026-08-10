import pytest
from fastapi.testclient import TestClient

from stitch_lineage.app import frontend_dist
from stitch_lineage.app.server import create_app
from stitch_lineage.io.graph_store import write_graph

MB_URL = "https://mb.example.com"


@pytest.fixture
def graph_path(tmp_path, sample_graph):
    path = tmp_path / ".stitch" / "graph.json"
    write_graph(sample_graph, path)
    return path


@pytest.fixture
def client(graph_path):
    return TestClient(create_app(graph_path, MB_URL))


def test_api_graph_round_trips_the_graph(client, sample_graph):
    response = client.get("/api/graph")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == sample_graph.schema_version
    assert payload["generated_at"] == sample_graph.generated_at
    assert len(payload["nodes"]) == len(sample_graph.nodes)
    assert len(payload["edges"]) == len(sample_graph.edges)


def test_api_graph_uses_aliases_and_drops_nulls(client):
    payload = client.get("/api/graph").json()
    model = next(node for node in payload["nodes"] if node["node_type"] == "model")
    assert model["schema"] == "MARTS"
    assert "schema_" not in model
    assert "owner" not in model
    assert all("from" in edge and "to" in edge for edge in payload["edges"])


def test_api_graph_reflects_a_rebuild_without_restart(client, graph_path, sample_graph):
    assert len(client.get("/api/graph").json()["nodes"]) == len(sample_graph.nodes)
    write_graph(sample_graph.model_copy(update={"nodes": sample_graph.nodes[:1]}), graph_path)
    assert len(client.get("/api/graph").json()["nodes"]) == 1


def test_api_meta_shape(client, sample_graph):
    meta = client.get("/api/meta").json()
    assert meta == {
        "metabase_url": MB_URL,
        "generated_at": sample_graph.generated_at,
        "schema_version": sample_graph.schema_version,
        "erd_default_scope": None,
        "strip_model_prefixes": [],
        "staging_enabled": False,
    }


def test_api_meta_tolerates_no_metabase_url(graph_path):
    meta = TestClient(create_app(graph_path, None)).get("/api/meta").json()
    assert meta["metabase_url"] is None


def test_api_meta_carries_the_configured_erd_scope(graph_path):
    client = TestClient(create_app(graph_path, None, "schema:MARTS"))
    assert client.get("/api/meta").json()["erd_default_scope"] == "schema:MARTS"


def test_api_meta_passes_an_unknown_erd_scope_through_for_the_app_to_flag(graph_path):
    # the app falls back to its auto-picked scope and notes the mismatch itself
    client = TestClient(create_app(graph_path, None, "tag:nope"))
    assert client.get("/api/meta").json()["erd_default_scope"] == "tag:nope"


def test_index_html_is_served_at_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root">' in response.text
    # served mode leaves the injection point untouched; the SPA fetches api/graph
    assert "__STITCH_INLINE_DATA__" in response.text


def test_assets_are_served(client):
    asset = next(path for path in (frontend_dist() / "assets").iterdir() if path.suffix == ".js")
    response = client.get(f"/assets/{asset.name}")
    assert response.status_code == 200
    assert len(response.content) == asset.stat().st_size


def test_missing_graph_returns_503_naming_the_fix(tmp_path):
    client = TestClient(create_app(tmp_path / "nope" / "graph.json", None))
    for endpoint in ("/api/graph", "/api/meta"):
        response = client.get(endpoint)
        assert response.status_code == 503, endpoint
        assert "stitch build" in response.json()["detail"]


def test_unparseable_graph_returns_503(tmp_path):
    path = tmp_path / "graph.json"
    path.write_text("{not json")
    response = TestClient(create_app(path, None)).get("/api/graph")
    assert response.status_code == 503
    assert "does not parse" in response.json()["detail"]


def test_api_meta_carries_the_configured_display_prefixes(graph_path):
    """serve.strip_model_prefixes reaches the app -- it is display-only, so the
    graph keeps the real dbt names and only /api/meta says what to hide."""
    client = TestClient(create_app(graph_path, None, strip_model_prefixes=["viz_", "sv_"]))
    assert client.get("/api/meta").json()["strip_model_prefixes"] == ["viz_", "sv_"]
