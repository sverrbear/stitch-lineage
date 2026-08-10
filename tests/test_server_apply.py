"""The staging workspace API: description edits (#70), relationship edits (#71), apply (#72).

The apply endpoints run the same engine `stitch apply` runs, in a throwaway git repo, so the
guards asserted here are the CLI's guards -- with no force path from the app.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from stitch_lineage import apply as apply_service
from stitch_lineage.app.server import create_app
from stitch_lineage.config import load_config
from stitch_lineage.graph.schema import Graph, Node, NodeType, column_node_id
from stitch_lineage.io.graph_store import read_graph, write_graph
from stitch_lineage.io.staged_store import (
    StagedRelationship,
    description_id,
    descriptions_path,
    read_descriptions,
    read_staged,
    relationship_id,
    staged_path,
    write_staged,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dbt_repo"
MARTS = "models/marts/_schema.yml"
EVENTS = "models/events/_schema.yml"
ORDERS = "model.demo.fct_orders"
CUSTOMERS = "model.demo.dim_customers"

CONFIG = """
metabase:
  url: https://mb.example.com
  api_key: ${STITCH_METABASE_API_KEY}
  databases:
    - metabase_name: Analytics
      dbt_database: ANALYTICS
relationships:
  write_to: relationships_test
"""

DRAWN = {
    "from_model": "fct_orders",
    "from_column": "customer_id",
    "to_model": "dim_customers",
    "to_column": "customer_id",
}
DRAWN_ID = relationship_id("fct_orders", "customer_id", "dim_customers", "customer_id")
EDIT = {"entity": "fct_orders", "column": "customer_id", "new_description": "FK to dim_customers"}
EDIT_ID = description_id("fct_orders", "customer_id")


def _manifest_node(name, schema="marts", patch=MARTS):
    return {
        "resource_type": "model",
        "name": name,
        "schema": schema,
        "patch_path": f"demo://{patch}" if patch else None,
    }


MANIFEST = {
    "metadata": {"dbt_version": "1.9.0"},
    "nodes": {
        ORDERS: _manifest_node("fct_orders"),
        CUSTOMERS: _manifest_node("dim_customers"),
        "model.demo.fct_events": _manifest_node("fct_events", "events", EVENTS),
        "model.demo.dim_stores": _manifest_node("dim_stores", patch=None),
    },
}


def _model(node_id, name):
    return Node(node_id=node_id, node_type=NodeType.MODEL, name=name, schema_="MARTS")


def _column(model_id, name):
    return Node(node_id=column_node_id(model_id, name), node_type=NodeType.COLUMN, name=name)


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path):
    """A dbt project with stitch.yml, a manifest, a graph and clean git history."""
    root = tmp_path / "repo"
    shutil.copytree(FIXTURES, root)
    (root / "stitch.yml").write_text(CONFIG)
    (root / "target").mkdir()
    (root / "target" / "manifest.json").write_text(json.dumps(MANIFEST))
    graph = Graph(
        generated_at="2026-08-10T00:00:00+00:00",
        nodes=[
            _model(ORDERS, "fct_orders"),
            _column(ORDERS, "customer_id"),
            _column(ORDERS, "order_id"),
            _model(CUSTOMERS, "dim_customers"),
            _column(CUSTOMERS, "customer_id"),
            _model("model.demo.fct_events", "fct_events"),
            _column("model.demo.fct_events", "event_id"),
        ],
    )
    write_graph(graph, root / ".stitch" / "graph.json")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


@pytest.fixture
def stores(repo):
    return staged_path(repo / ".stitch"), descriptions_path(repo / ".stitch")


@pytest.fixture
def client(repo, stores):
    config = repo / "stitch.yml"
    context = apply_service.ApplyContext(config=config, cfg=load_config(config))
    return TestClient(
        create_app(
            repo / ".stitch" / "graph.json",
            None,
            None,
            stores[0],
            repo / ".stitch" / "layout.yml",
            stores[1],
            context,
        )
    )


@pytest.fixture
def read_only(repo, stores):
    """The app without an apply context: staging works, the repo is untouchable."""
    return TestClient(
        create_app(repo / ".stitch" / "graph.json", None, None, stores[0], None, stores[1])
    )


def _yaml_description(repo, model, column=None):
    document = YAML(typ="safe").load((repo / MARTS).read_text())
    entry = next(item for item in document["models"] if item["name"] == model)
    if column is None:
        return entry.get("description")
    return next(item for item in entry["columns"] if item["name"] == column).get("description")


# --- staged descriptions (#70) ------------------------------------------------------------


def test_the_description_store_starts_empty(client):
    assert client.get("/api/staged-descriptions").json() == {"descriptions": []}


def test_put_stages_a_description_edit(client):
    response = client.put("/api/staged-descriptions", json=EDIT)
    assert response.status_code == 201
    payload = response.json()
    assert payload["created"] is True
    assert payload["description"]["id"] == EDIT_ID
    assert payload["description"]["new_description"] == "FK to dim_customers"
    assert payload["description"]["created_at"]
    assert client.get("/api/staged-descriptions").json()["descriptions"][0]["id"] == EDIT_ID


def test_a_second_put_replaces_the_edit_last_write_wins(client):
    client.put("/api/staged-descriptions", json=EDIT)
    response = client.put("/api/staged-descriptions", json={**EDIT, "new_description": "Newer"})
    assert response.status_code == 200
    assert response.json()["created"] is False
    listed = client.get("/api/staged-descriptions").json()["descriptions"]
    assert [entry["new_description"] for entry in listed] == ["Newer"]


def test_a_model_level_edit_omits_the_column(client):
    response = client.put(
        "/api/staged-descriptions", json={"entity": "fct_orders", "new_description": "Orders"}
    )
    assert response.status_code == 201
    assert response.json()["description"]["column"] is None


def test_a_multi_line_description_survives_the_store(client):
    text = "FK to dim_customers.\nNull for guest orders.\n"
    client.put("/api/staged-descriptions", json={**EDIT, "new_description": text})
    listed = client.get("/api/staged-descriptions").json()["descriptions"]
    assert listed[0]["new_description"] == text


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"entity": "fct_ghost"}, "unknown model 'fct_ghost'"),
        ({"column": "ghost_id"}, "'ghost_id' is not a column of model 'fct_orders'"),
        ({"new_description": "   "}, "a description cannot be empty"),
    ],
)
def test_invalid_description_edits_are_422(client, patch, expected):
    response = client.put("/api/staged-descriptions", json={**EDIT, **patch})
    assert response.status_code == 422
    assert expected in response.json()["detail"]
    assert client.get("/api/staged-descriptions").json() == {"descriptions": []}


def test_unknown_description_fields_are_rejected(client):
    response = client.put("/api/staged-descriptions", json={**EDIT, "severity": "error"})
    assert response.status_code == 422


def test_delete_removes_a_staged_description(client):
    client.put("/api/staged-descriptions", json=EDIT)
    assert client.delete(f"/api/staged-descriptions/{EDIT_ID}").status_code == 204
    assert client.get("/api/staged-descriptions").json() == {"descriptions": []}


def test_delete_of_an_unknown_description_is_404(client):
    response = client.delete("/api/staged-descriptions/nosuchid")
    assert response.status_code == 404
    assert "nosuchid" in response.json()["detail"]


# --- editing a staged relationship (#71) --------------------------------------------------


def test_put_edits_the_cardinality_in_place(client):
    client.post("/api/staged-relationships", json=DRAWN)
    response = client.put(
        f"/api/staged-relationships/{DRAWN_ID}", json={**DRAWN, "cardinality": "one-to-one"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["moved"] is False
    assert payload["relationship"]["cardinality"] == "one-to-one"
    listed = client.get("/api/staged-relationships").json()["relationships"]
    assert [entry["cardinality"] for entry in listed] == ["one-to-one"]


def test_put_that_changes_endpoints_rehashes_the_id(client):
    client.post("/api/staged-relationships", json=DRAWN)
    moved_to = {**DRAWN, "from_column": "order_id"}
    response = client.put(f"/api/staged-relationships/{DRAWN_ID}", json=moved_to)
    assert response.status_code == 200
    payload = response.json()
    assert payload["moved"] is True
    assert payload["relationship"]["id"] == relationship_id(
        "fct_orders", "order_id", "dim_customers", "customer_id"
    )
    assert [
        entry["id"] for entry in client.get("/api/staged-relationships").json()["relationships"]
    ] == [payload["relationship"]["id"]]


def test_put_keeps_when_it_was_staged(client, stores):
    write_staged([StagedRelationship(**DRAWN, created_at="2026-08-01T00:00:00+00:00")], stores[0])
    response = client.put(
        f"/api/staged-relationships/{DRAWN_ID}", json={**DRAWN, "cardinality": "one-to-one"}
    )
    assert response.json()["relationship"]["created_at"] == "2026-08-01T00:00:00+00:00"


def test_put_validates_endpoints_like_post(client):
    client.post("/api/staged-relationships", json=DRAWN)
    response = client.put(
        f"/api/staged-relationships/{DRAWN_ID}", json={**DRAWN, "to_column": "ghost_id"}
    )
    assert response.status_code == 422
    listed = client.get("/api/staged-relationships").json()["relationships"]
    assert [entry["id"] for entry in listed] == [DRAWN_ID]


def test_put_of_an_unknown_id_is_404(client):
    response = client.put("/api/staged-relationships/nosuchid", json=DRAWN)
    assert response.status_code == 404
    assert "nosuchid" in response.json()["detail"]


# --- apply preview (#72) ------------------------------------------------------------------


def test_preview_returns_per_file_diffs(client):
    client.post("/api/staged-relationships", json=DRAWN)
    client.put("/api/staged-descriptions", json=EDIT)

    payload = client.post("/api/apply/preview").json()
    assert payload["write_to"] == "relationships_test"
    assert payload["staged"] == {"relationships": 1, "descriptions": 1}
    assert [entry["path"] for entry in payload["files"]] == [MARTS]
    diff = payload["files"][0]["diff"]
    assert f"--- a/{MARTS}" in diff
    assert "+          - relationships:" in diff
    assert "FK to dim_customers" in diff
    assert payload["unappliable"] == []


def test_preview_writes_nothing(client, repo, stores):
    before = (repo / MARTS).read_text()
    client.post("/api/staged-relationships", json=DRAWN)
    client.post("/api/apply/preview")
    assert (repo / MARTS).read_text() == before
    assert len(read_staged(stores[0])) == 1


def test_preview_reports_unappliable_entries_with_reasons(client, stores):
    write_staged(
        [
            StagedRelationship(**DRAWN),
            StagedRelationship(
                from_model="dim_stores",
                from_column="region_id",
                to_model="dim_customers",
                to_column="customer_id",
            ),
        ],
        stores[0],
    )
    payload = client.post("/api/apply/preview").json()
    assert [problem["entry"]["kind"] for problem in payload["unappliable"]] == ["relationship"]
    assert "has no schema YAML file" in payload["unappliable"][0]["reason"]
    assert payload["unappliable"][0]["entry"]["label"].startswith("dim_stores.region_id")
    # the appliable one is still previewed
    assert [entry["path"] for entry in payload["files"]] == [MARTS]


def test_preview_reports_what_the_repo_already_says(client, repo):
    current = _yaml_description(repo, "fct_orders", "customer_id")
    client.put("/api/staged-descriptions", json={**EDIT, "new_description": current})
    payload = client.post("/api/apply/preview").json()
    assert payload["files"] == []
    assert payload["unchanged"][0]["entry"]["kind"] == "description"
    assert "already has this description" in payload["unchanged"][0]["reason"]


# --- apply (#72) --------------------------------------------------------------------------


def test_apply_writes_clears_and_patches_the_graph(client, repo, stores):
    client.post("/api/staged-relationships", json=DRAWN)
    client.put("/api/staged-descriptions", json=EDIT)

    payload = client.post("/api/apply").json()
    assert payload["written"] == [MARTS]
    assert payload["refused"] == []
    assert payload["applied"] == 2
    assert payload["still_staged"] == 0
    assert payload["graph"] == {
        "patched": True,
        "edges_added": 1,
        "descriptions_updated": 1,
        "skipped": [],
        "note": None,
    }

    written = (repo / MARTS).read_text()
    assert "- relationships:" in written
    assert _yaml_description(repo, "fct_orders", "customer_id") == "FK to dim_customers"
    assert read_staged(stores[0]) == []
    assert read_descriptions(stores[1]) == []

    graph = read_graph(repo / ".stitch" / "graph.json")
    relates = [edge for edge in graph.edges if edge.edge_type.value == "relates_to"]
    assert len(relates) == 1
    assert relates[0].evidence["source"] == "stitch apply"
    nodes = {node.node_id: node for node in graph.nodes}
    assert nodes[column_node_id(ORDERS, "customer_id")].description == "FK to dim_customers"


def test_the_app_never_forces_over_a_dirty_file(client, repo, stores):
    client.post("/api/staged-relationships", json=DRAWN)
    target = repo / MARTS
    target.write_text(target.read_text() + "\n# local edit\n")
    before = target.read_text()

    payload = client.post("/api/apply").json()
    assert payload["written"] == []
    assert [entry["path"] for entry in payload["refused"]] == [MARTS]
    assert "uncommitted changes" in payload["refused"][0]["reason"]
    assert "--force" in payload["refused"][0]["reason"]
    assert target.read_text() == before
    # nothing was applied, so nothing cleared
    assert len(read_staged(stores[0])) == 1


def test_a_clean_file_still_applies_when_another_is_dirty(client, repo, stores):
    client.post("/api/staged-relationships", json=DRAWN)
    staged = client.put(
        "/api/staged-descriptions",
        json={"entity": "fct_events", "column": "event_id", "new_description": "One per event"},
    )
    assert staged.status_code == 201
    dirty = repo / MARTS
    dirty.write_text(dirty.read_text() + "\n# local edit\n")

    payload = client.post("/api/apply").json()
    assert payload["written"] == [EVENTS]
    assert [entry["path"] for entry in payload["refused"]] == [MARTS]
    assert payload["applied"] == 1
    assert payload["still_staged"] == 1
    assert len(read_staged(stores[0])) == 1
    assert read_descriptions(stores[1]) == []


def test_apply_with_nothing_staged_is_a_quiet_success(client):
    payload = client.post("/api/apply").json()
    assert payload["written"] == []
    assert payload["applied"] == 0
    assert payload["graph"]["patched"] is False


def test_apply_reports_unappliable_entries_and_keeps_them_staged(client, stores):
    orphan = StagedRelationship(
        from_model="dim_stores",
        from_column="region_id",
        to_model="dim_customers",
        to_column="customer_id",
    )
    write_staged([StagedRelationship(**DRAWN), orphan], stores[0])

    payload = client.post("/api/apply").json()
    assert payload["written"] == [MARTS]
    assert payload["applied"] == 1
    assert [problem["entry"]["id"] for problem in payload["unappliable"]] == [orphan.id]
    assert [entry.id for entry in read_staged(stores[0])] == [orphan.id]


def test_a_corrupt_store_is_a_503_from_both_endpoints(client, stores):
    stores[1].parent.mkdir(parents=True, exist_ok=True)
    stores[1].write_text("descriptions: [unclosed\n")
    assert client.post("/api/apply/preview").status_code == 503
    assert client.post("/api/apply").status_code == 503


def test_a_missing_manifest_is_a_503(client, repo):
    (repo / "target" / "manifest.json").unlink()
    response = client.post("/api/apply/preview")
    assert response.status_code == 503
    assert "manifest.json" in response.json()["detail"]


def test_an_unimplemented_write_form_is_a_422(repo, stores):
    (repo / "stitch.yml").write_text(CONFIG.replace("relationships_test", "contract_constraint"))
    config = repo / "stitch.yml"
    client = TestClient(
        create_app(
            repo / ".stitch" / "graph.json",
            None,
            None,
            stores[0],
            None,
            stores[1],
            apply_service.ApplyContext(config=config, cfg=load_config(config)),
        )
    )
    response = client.post("/api/apply/preview")
    assert response.status_code == 422
    assert "not implemented" in response.json()["detail"]


def test_meta_advertises_apply(client, read_only):
    assert client.get("/api/meta").json()["apply_enabled"] is True
    assert read_only.get("/api/meta").json()["apply_enabled"] is False


def test_without_an_apply_context_the_repo_cannot_be_written(read_only, repo):
    before = (repo / MARTS).read_text()
    read_only.post("/api/staged-relationships", json=DRAWN)
    # the SPA mount answers unknown paths, so absence shows up as "not the API"
    assert read_only.post("/api/apply/preview").status_code != 200
    assert read_only.post("/api/apply").status_code != 200
    assert (repo / MARTS).read_text() == before
