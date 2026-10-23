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
        "strip_model_prefixes": [],
        "table_prefixes": [],
        "staging_enabled": False,
        "apply_enabled": False,
        "mend_enabled": False,
    }


def test_api_meta_tolerates_no_metabase_url(graph_path):
    meta = TestClient(create_app(graph_path, None)).get("/api/meta").json()
    assert meta["metabase_url"] is None


def test_retired_suggestion_api_is_absent_even_when_staging_is_enabled(graph_path):
    store = graph_path.parent / "staged_relationships.yml"
    api = create_app(graph_path, None, staged_path=store)
    client = TestClient(api)
    assert all("suggestion" not in route.path for route in api.routes)
    assert client.get("/api/suggestions").status_code == 404
    assert client.post("/api/suggestions/old/dismiss").status_code in (404, 405)
    assert not (graph_path.parent / "layout.yml").exists()
    assert client.get("/api/staged-relationships").json() == {"relationships": []}


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


def test_api_meta_carries_the_table_prefixes(graph_path):
    """metabase.databases[].table_prefix reaches the app so it can hide a dev alias
    (sis_fct_matches) from the physical names it displays (#80). Display only: the
    graph, and the bindings that stripped it to match, are untouched."""
    client = TestClient(create_app(graph_path, None, table_prefixes=["sis_"]))
    assert client.get("/api/meta").json()["table_prefixes"] == ["sis_"]


@pytest.mark.parametrize("bind_host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_server_rejects_rebound_host_even_on_reads(graph_path, bind_host):
    client = TestClient(create_app(graph_path, None, bind_host=bind_host))
    response = client.get("/api/graph", headers={"Host": "attacker.example:8787"})
    assert response.status_code == 403
    response = client.post(
        "/api/staged-relationships",
        headers={"Host": "attacker.example:8787", "Origin": "http://attacker.example:8787"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8787", "localhost:8787", "[::1]:8787"])
def test_loopback_server_accepts_loopback_hosts(graph_path, host):
    client = TestClient(create_app(graph_path, None, bind_host="127.0.0.1"))
    assert client.get("/api/graph", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize(
    "host", ["localhost.attacker.example", "127.0.0.1.attacker.example", "localhost:bad"]
)
def test_loopback_server_rejects_misleading_or_malformed_hosts(graph_path, host):
    client = TestClient(create_app(graph_path, None, bind_host="127.0.0.1"))
    assert client.get("/api/graph", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("bind_host", ["0.0.0.0", "::", "192.0.2.1", "stitch.internal"])
def test_explicit_network_bind_preserves_custom_hostnames(graph_path, bind_host):
    client = TestClient(create_app(graph_path, None, bind_host=bind_host))
    assert client.get("/api/graph", headers={"Host": "stitch.internal:8787"}).status_code == 200
