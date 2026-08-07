import json
from pathlib import Path

import uvicorn
from typer.testing import CliRunner

from stitch_lineage import __version__
from stitch_lineage.cli import _print_coverage, app, console
from stitch_lineage.graph.schema import Coverage, Graph, Node, NodeType
from stitch_lineage.io.dbt_runner import StitchDbtRunnerError
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
    for command in ("build", "search", "doctor", "export", "init", "serve"):
        assert command in result.output


def test_impact_is_shelved_hidden_but_invocable():
    # shelved: hidden from --help, but the command keeps working when invoked directly
    top_help = runner.invoke(app, ["--help"])
    assert top_help.exit_code == 0
    assert "impact" not in top_help.output

    own_help = runner.invoke(app, ["impact", "--help"])
    assert own_help.exit_code == 0
    assert "Shelved pending the committed-baseline workflow" in own_help.output


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_init_is_phase_1():
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "Phase 1" in result.output


def _stub_uvicorn(monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda served, **kwargs: calls.append((served, kwargs)))
    return calls


def test_serve_without_graph_points_at_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = _stub_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve", "--no-open"])
    assert result.exit_code == 1
    assert "stitch build" in result.output
    assert calls == []


def test_serve_binds_the_requested_host_and_port(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_graph(tmp_path)
    calls = _stub_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve", "--no-open", "--host", "0.0.0.0", "--port", "9123"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0][1]["host"] == "0.0.0.0"
    assert calls[0][1]["port"] == 9123
    assert "http://0.0.0.0:9123" in result.output


def test_serve_defaults_to_localhost_8787_and_opens_a_browser(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_graph(tmp_path)
    calls = _stub_uvicorn(monkeypatch)
    opened = []
    monkeypatch.setattr("stitch_lineage.cli._open_browser_soon", opened.append)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    assert calls[0][1] == {"host": "127.0.0.1", "port": 8787, "log_level": "warning"}
    assert opened == ["http://127.0.0.1:8787"]


def test_serve_no_open_does_not_open_a_browser(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_graph(tmp_path)
    _stub_uvicorn(monkeypatch)
    opened = []
    monkeypatch.setattr("stitch_lineage.cli._open_browser_soon", opened.append)
    runner.invoke(app, ["serve", "--no-open"])
    assert opened == []


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
    for command in (["search", "x"], ["impact"], ["export"], ["doctor"], ["build"], ["serve"]):
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
    assert "jsonl, site" in result.output


def test_export_site_inlines_the_graph(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    _write_graph(tmp_path, [Node(node_id="model.demo.fct", node_type=NodeType.MODEL, name="fct")])
    result = runner.invoke(app, ["export", "--format", "site"])
    assert result.exit_code == 0, result.output
    index_html = (tmp_path / ".stitch" / "site" / "index.html").read_text()
    assert "__STITCH_INLINE_DATA__" not in index_html
    assert "window.__STITCH_GRAPH__" in index_html
    assert '"metabase_url":"https://mb.example.com"' in index_html
    assert (tmp_path / ".stitch" / "site" / "assets").is_dir()


def test_export_site_honours_out_and_works_without_a_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_graph(tmp_path)
    out = tmp_path / "public"
    result = runner.invoke(app, ["export", "--format", "site", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert '"metabase_url":null' in (out / "index.html").read_text()


def test_export_site_without_graph_points_at_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["export", "--format", "site"])
    assert result.exit_code == 1
    assert "stitch build" in result.output


def test_export_site_skips_an_unresolved_metabase_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_URL", raising=False)
    (tmp_path / "stitch.yml").write_text(
        VALID_CONFIG.replace("https://mb.example.com", "${STITCH_METABASE_URL}")
    )
    _write_graph(tmp_path)
    result = runner.invoke(app, ["export", "--format", "site"])
    assert result.exit_code == 0, result.output
    assert '"metabase_url":null' in (tmp_path / ".stitch" / "site" / "index.html").read_text()


# --- coverage report ---------------------------------------------------------


def _coverage_output(coverage: Coverage, case_mismatch_count: int = 0) -> str:
    with console.capture() as capture:
        _print_coverage(coverage, metabase_side=True, case_mismatch_count=case_mismatch_count)
    return capture.get()


def test_coverage_report_surfaces_unverified_fields_and_seed_deps():
    output = _coverage_output(
        Coverage(unverified_field_count=3, seed_snapshot_dependencies=2), case_mismatch_count=1
    )
    assert "warning: 3 Metabase fields left unbound" in output
    assert "note: 2 seed/snapshot dependencies not represented" in output
    assert "case-only mismatch" in output


def test_coverage_report_stays_quiet_when_counters_are_zero():
    output = _coverage_output(Coverage())
    assert "left unbound" not in output
    assert "seed/snapshot" not in output


# --- build --docs / dbt.auto_docs -------------------------------------------

AUTO_DOCS_CONFIG = (
    VALID_CONFIG
    + """
dbt:
  auto_docs: true
  docs_args: ["--target", "prod"]
"""
)


def _record_docs_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "stitch_lineage.cli.run_docs_generate",
        lambda project_dir, extra_args: calls.append((project_dir, extra_args)),
    )
    return calls


def test_build_docs_flag_runs_docs_generate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    calls = _record_docs_calls(monkeypatch)
    result = runner.invoke(app, ["build", "--docs"])
    assert len(calls) == 1
    project_dir, extra_args = calls[0]
    assert Path(project_dir).resolve() == tmp_path.resolve()
    assert extra_args == []
    assert "running dbt docs generate" in result.output


def test_build_auto_docs_config_runs_docs_generate_with_docs_args(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(AUTO_DOCS_CONFIG)
    calls = _record_docs_calls(monkeypatch)
    result = runner.invoke(app, ["build"])
    assert len(calls) == 1
    assert calls[0][1] == ["--target", "prod"]
    assert "running dbt docs generate" in result.output


def test_build_no_docs_flag_overrides_auto_docs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(AUTO_DOCS_CONFIG)
    calls = _record_docs_calls(monkeypatch)
    result = runner.invoke(app, ["build", "--no-docs"])
    assert calls == []
    assert "running dbt docs generate" not in result.output


def test_build_docs_absent_defaults_to_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    calls = _record_docs_calls(monkeypatch)
    result = runner.invoke(app, ["build"])
    assert calls == []
    assert "running dbt docs generate" not in result.output


def test_build_docs_runner_failure_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)

    def _boom(project_dir, extra_args):
        raise StitchDbtRunnerError("dbt executable not found on PATH -- install dbt")

    monkeypatch.setattr("stitch_lineage.cli.run_docs_generate", _boom)
    result = runner.invoke(app, ["build", "--docs"])
    assert result.exit_code == 1
    assert "dbt executable not found on PATH" in result.output
