import json

from typer.testing import CliRunner

from stitch_lineage import __version__
from stitch_lineage.cli import app
from stitch_lineage.graph.schema import Graph, Node, NodeType
from stitch_lineage.io.graph_store import write_graph

runner = CliRunner()

VALID_CONFIG = """
metabase:
  url: https://mb.example.com
  api_key: ${STITCH_METABASE_API_KEY}
  databases:
    - metabase_name: Analytics
      dbt_database: ANALYTICS
"""


def _write_graph(tmp_path, nodes=()):
    graph = Graph(generated_at="2026-08-06T00:00:00+00:00", nodes=list(nodes))
    write_graph(graph, tmp_path / ".stitch" / "graph.json")


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("build", "impact", "search", "doctor", "export", "init", "serve"):
        assert command in result.output


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_init_is_phase_1():
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "Phase 1" in result.output


def test_serve_is_phase_1():
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 2
    assert "Phase 1" in result.output


def test_build_without_config_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert "config file not found" in result.output


def test_build_with_literal_api_key_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    literal = VALID_CONFIG.replace("${STITCH_METABASE_API_KEY}", "mb_live_secret")
    (tmp_path / "stitch.yml").write_text(literal)
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert "literal key" in result.output


def test_build_without_artifacts_names_the_fix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert "manifest.json" in result.output
    assert "dbt docs generate" in result.output


def test_build_without_metabase_env_fails_before_http(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text('{"metadata": {}, "nodes": {}}')
    (tmp_path / "target" / "catalog.json").write_text('{"nodes": {}, "sources": {}}')
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert "STITCH_METABASE_API_KEY" in result.output


def test_search_without_graph_points_at_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["search", "match_intensity"])
    assert result.exit_code == 1
    assert "stitch build" in result.output


def test_search_works_without_metabase_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    node = Node(node_id="model.demo.fct_orders", node_type=NodeType.MODEL, name="fct_orders")
    _write_graph(tmp_path, [node])
    result = runner.invoke(app, ["search", "fct_orders", "--json"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert rows[0]["node_id"] == "model.demo.fct_orders"


def test_impact_without_candidate_graph_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    result = runner.invoke(app, ["impact"])
    assert result.exit_code == 1
    assert "stitch build" in result.output


def test_explicitly_passed_missing_config_is_a_hard_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for command in (["search", "x"], ["impact"], ["export"], ["doctor"], ["build"]):
        result = runner.invoke(app, [*command, "--config", "nope.yml"])
        assert result.exit_code == 1, command
        assert "config file not found" in result.output


def test_impact_rejects_unknown_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["impact", "--format", "sms"])
    assert result.exit_code == 1
    assert "unsupported --format" in result.output


def test_export_rejects_unknown_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["export", "--format", "parquet"])
    assert result.exit_code == 1
    assert "unsupported --format" in result.output
