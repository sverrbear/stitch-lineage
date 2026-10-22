"""Init through the real CLI/build/server, with synthetic HTTP and an in-memory OS store."""

import os
from urllib.parse import unquote

import pytest
import responses
from conftest import plain
from fastapi.testclient import TestClient
from test_e2e import MB_URL, _register_metabase
from test_init_wizard import API_KEY, API_KEY_VAR, URL_VAR, make_project
from typer.testing import CliRunner

from stitch_lineage.cli import app
from stitch_lineage.graph.impact import column_blast_radius
from stitch_lineage.graph.onboarding import first_impact_column
from stitch_lineage.graph.schema import EdgeType
from stitch_lineage.io import credentials
from stitch_lineage.io.graph_store import read_graph

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = make_project(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.delenv(API_KEY_VAR, raising=False)
    monkeypatch.delenv(URL_VAR, raising=False)
    return root


@pytest.fixture
def browser(monkeypatch):
    served, opened = [], []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: served.append((app, kwargs)))
    monkeypatch.setattr("stitch_lineage.cli._open_browser_soon", opened.append)
    return served, opened


def invoke_init(args=(), user_input=None):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        _register_metabase(rsps)
        result = runner.invoke(app, ["init", *args], input=user_input)
        assert all(call.request.method == "GET" for call in rsps.calls)
        assert all(call.request.headers["x-api-key"] == API_KEY for call in rsps.calls)
    return result


def test_one_command_connects_builds_and_opens_real_impact(project, browser):
    result = invoke_init(user_input=f"{MB_URL}\n{API_KEY}\n")
    assert result.exit_code == 0, result.output
    assert API_KEY not in result.output
    assert API_KEY_VAR not in os.environ
    served, opened = browser
    graph = read_graph(project / ".stitch/graph.json")
    assert graph.coverage.models_bound > 0
    assert graph.coverage.mbql_cards_resolved > 0
    assert len(opened) == len(served) == 1
    node = unquote(opened[0].split("/#/node/")[1])
    assert column_blast_radius(graph, node).cards
    client = TestClient(served[0][0], base_url="http://127.0.0.1:8787")
    assert client.get("/").status_code == 200
    assert client.get("/api/graph").json()["nodes"]
    assert "Baseline saved" in plain(result.output)
    for path in project.rglob("*"):
        if path.is_file():
            assert API_KEY not in path.read_text()


def test_retry_reuses_config_key_and_other_project_settings(project, browser):
    first = invoke_init(["--no-serve"], f"{MB_URL}\n{API_KEY}\n")
    assert first.exit_code == 0, first.output
    config = project / "stitch.yml"
    config.write_text(config.read_text() + '\nmend:\n  auto: ["repoint"]\n')
    before = config.read_bytes()
    second = invoke_init(["--no-serve"])
    assert second.exit_code == 0, second.output
    assert config.read_bytes() == before
    assert "Metabase API key:" not in second.output
    assert "Metabase URL:" not in second.output
    assert browser == ([], [])
    assert (project / ".stitch/graph.prev.json").exists()
    # Independent commands recover the key, without relying on init's in-memory environment.
    with responses.RequestsMock() as rsps:
        _register_metabase(rsps)
        build = runner.invoke(app, ["build", "--no-docs"])
    assert build.exit_code == 0, build.output


def test_build_failure_keeps_setup_for_retry_and_never_opens(project, browser, monkeypatch):
    import stitch_lineage.cli as cli

    actual_build = cli._run_build

    def failed_build(**kwargs):
        cli._fail("synthetic fetch failure")

    monkeypatch.setattr(cli, "_run_build", failed_build)
    failed = invoke_init(user_input=f"{MB_URL}\n{API_KEY}\n")
    assert failed.exit_code == 1
    assert "Setup is saved" in plain(failed.output)
    assert browser == ([], [])
    assert API_KEY_VAR not in os.environ
    monkeypatch.setattr(cli, "_run_build", actual_build)
    retried = invoke_init(["--no-serve"])
    assert retried.exit_code == 0, retried.output


def test_configure_only_does_not_build_or_launch(project, browser):
    (project / "target/catalog.json").unlink()
    result = invoke_init(["--configure-only"], f"{MB_URL}\n{API_KEY}\n")
    assert result.exit_code == 0, result.output
    assert not (project / ".stitch/graph.json").exists()
    assert browser == ([], [])


def test_headless_store_still_completes_current_setup(project, browser, monkeypatch):
    def unavailable():
        raise credentials.CredentialStoreUnavailable("unavailable")

    monkeypatch.setattr(credentials, "_backend", unavailable)
    result = invoke_init(["--no-open", "--port", "9192"], f"{MB_URL}\n{API_KEY}\n")
    assert result.exit_code == 0, result.output
    assert "continuing for this session" in plain(result.output)
    served, opened = browser
    assert len(served) == 1 and not opened
    assert served[0][1]["port"] == 9192
    assert (project / ".stitch/graph.json").exists()


def test_first_column_requires_active_report_evidence(sample_graph):
    graph = sample_graph
    column = first_impact_column(graph)
    assert column is not None
    for node in graph.nodes:
        if node.node_type.value == "mb_card":
            node.properties["archived"] = True
    assert first_impact_column(graph) is None
    for node in graph.nodes:
        node.properties.pop("archived", None)
    for edge in graph.edges:
        if edge.edge_type is EdgeType.BINDS_TO:
            edge.edge_type = EdgeType.RELATES_TO
    assert first_impact_column(graph) is None
