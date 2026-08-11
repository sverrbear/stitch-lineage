import json
import re
from pathlib import Path

import uvicorn
from typer.testing import CliRunner

from stitch_lineage import __version__
from stitch_lineage.cli import _print_coverage, app, console
from stitch_lineage.graph.schema import (
    Confidence,
    Coverage,
    Edge,
    EdgeType,
    Graph,
    Node,
    NodeType,
    column_node_id,
    relationship_id,
)
from stitch_lineage.io.dbt_runner import StitchDbtRunnerError
from stitch_lineage.io.graph_store import write_graph
from stitch_lineage.io.layout_store import LAYOUT_FILENAME, add_dismissed

runner = CliRunner()

VALID_CONFIG = """
metabase:
  url: https://mb.example.com
  api_key: ${STITCH_METABASE_API_KEY}
  databases:
    - metabase_name: Analytics
      dbt_database: ANALYTICS
"""


SCOPED_CONFIG = VALID_CONFIG + 'serve:\n  erd_default_scope: "schema:MARTS"\n'


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _uncoloured(output: str) -> str:
    """CI sets GITHUB_ACTIONS, which makes typer force-colour its help -- and rich's
    option highlighter then splits '--base-file' across style codes mid-token."""
    return _ANSI.sub("", output)


def _write_graph(tmp_path, nodes=()):
    graph = Graph(generated_at="2026-08-06T00:00:00+00:00", nodes=list(nodes))
    write_graph(graph, tmp_path / ".stitch" / "graph.json")


def _marts_model():
    return Node(node_id="model.demo.fct", node_type=NodeType.MODEL, name="fct", schema_="MARTS")


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = _uncoloured(result.output)
    for command in (
        "build",
        "search",
        "suggest",
        "doctor",
        "export",
        "impact",
        "init",
        "serve",
        "history",
    ):
        assert command in output


def test_history_works_without_a_config_and_points_at_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 0, result.output
    assert "no graph baselines stored in .stitch/history" in result.output
    assert "keyed by the HEAD commit" in result.output

    as_json = runner.invoke(app, ["history", "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output) == {
        "baselines": [],
        "dir": ".stitch/history",
        "retention": 20,
    }


def test_impact_help_documents_every_baseline_source():
    own_help = runner.invoke(app, ["impact", "--help"])
    assert own_help.exit_code == 0
    output = _uncoloured(own_help.output)
    assert "--base-file" in output
    assert "graph.prev.json" in output
    # the --base local-history path (issue #87) and the point query (issue #86)
    assert "history" in output
    assert "--column" in output


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_init_outside_a_dbt_project_fails_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "no dbt_project.yml" in result.output


def test_init_runs_the_wizard(tmp_path, monkeypatch):
    # the wizard itself is covered in test_init_wizard.py; this is the CLI wiring
    monkeypatch.chdir(tmp_path)
    (tmp_path / "target").mkdir()
    (tmp_path / "dbt_project.yml").write_text("name: demo\n", encoding="utf-8")
    result = runner.invoke(app, ["init"], input="n\n")
    assert result.exit_code == 1
    assert "no manifest at" in result.output
    assert "dbt docs generate" in result.output


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


def test_serve_warns_once_about_an_erd_scope_the_graph_does_not_have(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(SCOPED_CONFIG.replace("schema:MARTS", "schema:nope"))
    _write_graph(tmp_path, [_marts_model()])
    calls = _stub_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve", "--no-open"])
    assert result.exit_code == 0, result.output
    assert "schema:nope" in result.output
    assert "schema:MARTS" in result.output
    assert len(calls) == 1  # it is a warning, not a failure


def test_serve_hands_the_configured_erd_scope_to_the_app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(SCOPED_CONFIG)
    _write_graph(tmp_path, [_marts_model()])
    calls = _stub_uvicorn(monkeypatch)
    result = runner.invoke(app, ["serve", "--no-open"])
    assert result.exit_code == 0, result.output
    assert "warning" not in result.output
    meta = TestClient(calls[0][0]).get("/api/meta").json()
    assert meta["erd_default_scope"] == "schema:MARTS"


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


def _write_artifacts(tmp_path):
    (tmp_path / "target").mkdir(exist_ok=True)
    (tmp_path / "target" / "manifest.json").write_text('{"metadata": {}, "nodes": {}}')
    (tmp_path / "target" / "catalog.json").write_text('{"nodes": {}, "sources": {}}')


def test_build_without_metabase_env_fails_before_http(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    _write_artifacts(tmp_path)
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert "STITCH_METABASE_API_KEY" in result.output


def test_build_without_metabase_env_fails_before_loading_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    (tmp_path / "stitch.yml").write_text(AUTO_DOCS_CONFIG)
    _write_artifacts(tmp_path)  # valid artifacts: the env check must still fire first

    def _never_called(*args, **kwargs):
        raise AssertionError("build did artifact work before checking the Metabase env")

    monkeypatch.setattr("stitch_lineage.cli.load_manifest", _never_called)
    monkeypatch.setattr("stitch_lineage.cli.load_catalog", _never_called)
    docs_calls = _record_docs_calls(monkeypatch)

    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert docs_calls == []  # not even 'dbt docs generate' runs
    assert "running dbt docs generate" not in result.output


def test_build_missing_env_message_names_the_fix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    lines = [line.rstrip() for line in result.output.splitlines()]
    assert lines[:4] == [
        "error: environment variable STITCH_METABASE_API_KEY is referenced in stitch.yml "
        "but not set",
        "  stitch build needs it to call the Metabase API.",
        "  fix: set it in your environment (create a key in Metabase: "
        "Admin settings -> Authentication -> API keys),",
        "  or run 'stitch build --no-metabase' for a dbt-only graph.",
    ]


def test_build_no_metabase_needs_no_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    _write_artifacts(tmp_path)
    result = runner.invoke(app, ["build", "--no-metabase"])
    assert result.exit_code == 0, result.output
    assert "dbt-only" in result.output


def test_multiple_missing_env_vars_are_all_listed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    monkeypatch.delenv("STITCH_METABASE_URL", raising=False)
    (tmp_path / "stitch.yml").write_text(
        VALID_CONFIG.replace("https://mb.example.com", "${STITCH_METABASE_URL}")
    )
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 1
    assert (
        "environment variables STITCH_METABASE_URL, STITCH_METABASE_API_KEY "
        "are referenced in stitch.yml but not set" in result.output
    )
    assert "needs them to call the Metabase API" in result.output


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


def _suggestible_graph(tmp_path):
    """fct_orders.customer_id names dim_customers' grain -- one naming suggestion."""
    orders, customers = "model.demo.fct_orders", "model.demo.dim_customers"
    _write_graph(
        tmp_path,
        [
            Node(node_id=orders, node_type=NodeType.MODEL, name="fct_orders"),
            Node(
                node_id=column_node_id(orders, "customer_id"),
                node_type=NodeType.COLUMN,
                name="customer_id",
            ),
            Node(node_id=customers, node_type=NodeType.MODEL, name="dim_customers"),
            Node(
                node_id=column_node_id(customers, "customer_id"),
                node_type=NodeType.COLUMN,
                name="customer_id",
            ),
        ],
    )


def test_suggest_prints_a_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    _suggestible_graph(tmp_path)
    result = runner.invoke(app, ["suggest"])
    assert result.exit_code == 0, result.output
    for expected in ("source", "score", "naming", "fct_orders", "dim_customers", "grain"):
        assert expected in result.output, result.output


def test_suggest_json_carries_the_same_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    _suggestible_graph(tmp_path)
    result = runner.invoke(app, ["suggest", "--json"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["from_model"] == "fct_orders"
    assert rows[0]["to_column"] == "customer_id"
    assert rows[0]["source"] == "naming"
    assert rows[0]["id"] == relationship_id(
        "fct_orders", "customer_id", "dim_customers", "customer_id"
    )


def test_suggest_honours_dismissals_from_layout_yml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    _suggestible_graph(tmp_path)
    dismissed = relationship_id("fct_orders", "customer_id", "dim_customers", "customer_id")
    add_dismissed(dismissed, tmp_path / ".stitch" / LAYOUT_FILENAME)
    result = runner.invoke(app, ["suggest"])
    assert result.exit_code == 0, result.output
    assert "no suggestions" in result.output


def test_suggest_limit_caps_the_list(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    _suggestible_graph(tmp_path)
    result = runner.invoke(app, ["suggest", "--json", "--limit", "0"])
    assert len([line for line in result.output.splitlines() if line.strip()]) == 1
    capped = runner.invoke(app, ["suggest", "--json", "--limit", "1"])
    assert len([line for line in capped.output.splitlines() if line.strip()]) == 1


def test_suggest_without_graph_points_at_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["suggest"])
    assert result.exit_code == 1
    assert "stitch build" in result.output


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


def test_impact_without_a_baseline_names_both_ways_out(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    _write_graph(tmp_path)
    result = runner.invoke(app, ["impact"])
    assert result.exit_code == 1
    assert "no baseline at .stitch/graph.prev.json" in result.output
    assert "run 'stitch build' twice" in result.output
    assert "--base-file <path>" in result.output


def test_impact_base_file_must_exist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    _write_graph(tmp_path)
    result = runner.invoke(app, ["impact", "--base-file", "nope.json"])
    assert result.exit_code == 1
    assert "baseline file not found: nope.json" in result.output


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


def test_export_site_inlines_the_configured_erd_scope(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(SCOPED_CONFIG)
    _write_graph(tmp_path, [_marts_model()])
    result = runner.invoke(app, ["export", "--format", "site"])
    assert result.exit_code == 0, result.output
    assert (
        '"erd_default_scope":"schema:MARTS"'
        in (tmp_path / ".stitch" / "site" / "index.html").read_text()
    )
    assert "warning" not in result.output


def test_export_site_warns_about_an_erd_scope_the_graph_does_not_have(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(SCOPED_CONFIG.replace("schema:MARTS", "tag:nope"))
    _write_graph(tmp_path, [_marts_model()])
    result = runner.invoke(app, ["export", "--format", "site"])
    assert result.exit_code == 0, result.output
    assert "tag:nope" in result.output
    assert "schema:MARTS" in result.output  # names what is available
    # still exported, and the app is told what was configured so it can say so too
    assert (
        '"erd_default_scope":"tag:nope"'
        in (tmp_path / ".stitch" / "site" / "index.html").read_text()
    )


def test_export_site_inlines_the_configured_table_prefixes(tmp_path, monkeypatch):
    """The dev alias prefix reaches the app as display config (#80)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG + "      table_prefix: sis_\n")
    _write_graph(tmp_path, [_marts_model()])
    result = runner.invoke(app, ["export", "--format", "site"])
    assert result.exit_code == 0, result.output
    assert '"table_prefixes":["sis_"]' in (tmp_path / ".stitch" / "site" / "index.html").read_text()


def test_export_site_drops_an_unresolved_table_prefix(tmp_path, monkeypatch):
    """An unresolved ${USER_PREFIX} must not be shown to the app as a literal prefix."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_USER_PREFIX", raising=False)
    (tmp_path / "stitch.yml").write_text(
        VALID_CONFIG + "      table_prefix: ${STITCH_USER_PREFIX}_\n"
    )
    _write_graph(tmp_path, [_marts_model()])
    result = runner.invoke(app, ["export", "--format", "site"])
    assert result.exit_code == 0, result.output
    assert '"table_prefixes":[]' in (tmp_path / ".stitch" / "site" / "index.html").read_text()


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


def _coverage_output(
    coverage: Coverage, case_mismatch_count: int = 0, bindings_total: int = 0
) -> str:
    with console.capture() as capture:
        _print_coverage(
            coverage,
            metabase_side=True,
            case_mismatch_count=case_mismatch_count,
            bindings_total=bindings_total,
        )
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


def test_some_case_mismatches_stay_a_warning():
    output = _coverage_output(Coverage(), case_mismatch_count=3, bindings_total=100)
    assert "warning: 3 column bindings matched on a case-only mismatch" in output


def test_case_mismatch_everywhere_is_informational_not_alarming():
    # a Snowflake warehouse upper-cases every identifier: nothing here is actionable
    output = _coverage_output(Coverage(), case_mismatch_count=1210, bindings_total=1210)
    assert "note: 1210/1210 column bindings matched on a case-only mismatch" in output
    assert "warning" not in output


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
    # the docs step runs after the Metabase env check, so these cases need the key set
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
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
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    (tmp_path / "stitch.yml").write_text(AUTO_DOCS_CONFIG)
    calls = _record_docs_calls(monkeypatch)
    result = runner.invoke(app, ["build"])
    assert len(calls) == 1
    assert calls[0][1] == ["--target", "prod"]
    assert "running dbt docs generate" in result.output


def test_build_no_docs_flag_overrides_auto_docs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    (tmp_path / "stitch.yml").write_text(AUTO_DOCS_CONFIG)
    calls = _record_docs_calls(monkeypatch)
    result = runner.invoke(app, ["build", "--no-docs"])
    assert calls == []
    assert "running dbt docs generate" not in result.output


def test_build_docs_absent_defaults_to_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    calls = _record_docs_calls(monkeypatch)
    result = runner.invoke(app, ["build"])
    assert calls == []
    assert "running dbt docs generate" not in result.output


def test_build_docs_runner_failure_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)

    def _boom(project_dir, extra_args):
        raise StitchDbtRunnerError("dbt executable not found on PATH -- install dbt")

    monkeypatch.setattr("stitch_lineage.cli.run_docs_generate", _boom)
    result = runner.invoke(app, ["build", "--docs"])
    assert result.exit_code == 1
    assert "dbt executable not found on PATH" in result.output


# --- stitch impact --column: point-query blast radius (issue #86) --------------------


def _plain(output):
    """Rich output with the styling taken back out, for substring assertions.

    Under CI rich force-enables terminal mode (it treats GITHUB_ACTIONS as a tty) and
    renders at 80 columns, so it both splits a token like '--column' across ANSI style
    codes and wraps long messages mid-sentence. Strip the escapes and collapse runs of
    whitespace, and the assertion checks the text rather than the terminal it ran in.
    """
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", output).split())


def _write_impact_graph(tmp_path):
    """Synthetic chain: fct_matches.match_intensity -> mart_engagement -> field -> card."""
    fct, mart = "model.demo.fct_matches", "model.demo.mart_engagement"
    fct_col, mart_col = column_node_id(fct, "match_intensity"), column_node_id(mart, "m_i")
    nodes = [
        Node(node_id=fct, node_type=NodeType.MODEL, name="fct_matches"),
        Node(node_id=mart, node_type=NodeType.MODEL, name="mart_engagement"),
        Node(node_id=fct_col, node_type=NodeType.COLUMN, name="match_intensity"),
        Node(node_id=mart_col, node_type=NodeType.COLUMN, name="m_i"),
        Node(
            node_id=column_node_id(mart, "match_intensity"),
            node_type=NodeType.COLUMN,
            name="match_intensity",
        ),
        Node(node_id="mb_field::101", node_type=NodeType.MB_FIELD, name="Match Intensity"),
        Node(node_id="mb_card::412", node_type=NodeType.MB_CARD, name="Intensity by country"),
        Node(node_id="mb_dash::9", node_type=NodeType.MB_DASHBOARD, name="Board"),
    ]
    edges = [
        Edge(from_=fct_col, to=mart_col, edge_type=EdgeType.FEEDS, confidence=Confidence.EXACT),
        Edge(
            from_=mart_col,
            to="mb_field::101",
            edge_type=EdgeType.BINDS_TO,
            confidence=Confidence.EXACT,
        ),
        Edge(
            from_="mb_field::101",
            to="mb_card::412",
            edge_type=EdgeType.CONSUMED_BY,
            confidence=Confidence.EXACT,
        ),
        Edge(
            from_="mb_card::412",
            to="mb_dash::9",
            edge_type=EdgeType.APPEARS_ON,
            confidence=Confidence.EXACT,
        ),
    ]
    write_graph(Graph(nodes=nodes, edges=edges), tmp_path / ".stitch" / "graph.json")


def test_impact_column_prints_the_blast_radius_offline(tmp_path, monkeypatch):
    # no stitch.yml and no Metabase credentials: the point query reads graph.json only
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    _write_impact_graph(tmp_path)
    result = runner.invoke(app, ["impact", "--column", "fct_matches.match_intensity"])
    assert result.exit_code == 0, result.output
    assert "fct_matches.match_intensity" in result.output
    assert "1 downstream model: mart_engagement" in result.output
    assert "#412 Intensity by country  (Board)" in result.output
    assert "1 dashboard: Board" in result.output


def test_impact_column_json_is_pipeable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_impact_graph(tmp_path)
    result = runner.invoke(app, ["impact", "--column", "fct_matches.match_intensity", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["node_id"] == "model.demo.fct_matches::match_intensity"
    assert [card["card_id"] for card in payload["cards"]] == [412]
    assert [ref["label"] for ref in payload["models"]] == ["mart_engagement"]


def test_impact_column_ambiguous_reference_lists_candidates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_impact_graph(tmp_path)
    result = runner.invoke(app, ["impact", "--column", "match_intensity"])
    assert result.exit_code == 1
    output = _plain(result.output)
    assert "matches 2 columns" in output
    assert "qualify it as model.column" in output
    assert "fct_matches.match_intensity" in output


def test_impact_column_unknown_reference_suggests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_impact_graph(tmp_path)
    result = runner.invoke(app, ["impact", "--column", "fct_matches.match_intensety"])
    assert result.exit_code == 1
    output = _plain(result.output)
    assert "no column matching" in output
    assert "did you mean" in output
    assert "fct_matches.match_intensity" in output
    assert "stitch search" in output


def test_impact_column_on_a_model_names_its_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_impact_graph(tmp_path)
    result = runner.invoke(app, ["impact", "--column", "fct_matches"])
    assert result.exit_code == 1
    output = _plain(result.output)
    assert "is a model, not a column" in output
    assert "fct_matches.match_intensity" in output


def test_impact_json_and_format_are_scoped_to_their_own_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_impact_graph(tmp_path)
    no_column = runner.invoke(app, ["impact", "--json"])
    assert no_column.exit_code == 1
    assert "--json requires --column" in _plain(no_column.output)

    wrong_format = runner.invoke(
        app, ["impact", "--column", "fct_matches.match_intensity", "--format", "slack"]
    )
    assert wrong_format.exit_code == 1
    assert "use --json with --column" in _plain(wrong_format.output)


def test_impact_column_documented_on_the_command(tmp_path, monkeypatch):
    # impact is no longer hidden: the previous-build snapshot (#53) gave it a default
    # baseline, so the top-level help lists it again
    monkeypatch.chdir(tmp_path)
    top_help = runner.invoke(app, ["--help"])
    assert "impact" in _plain(top_help.output)
    own_help = runner.invoke(app, ["impact", "--help"])
    assert own_help.exit_code == 0
    assert "--column" in _plain(own_help.output)


def test_impact_column_rejects_a_baseline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for extra in (["--base", "main"], ["--base-file", "graph.json"]):
        result = runner.invoke(app, ["impact", "--column", "fct.col", *extra])
        assert result.exit_code == 1
        assert "takes no baseline" in _plain(result.output)


def test_impact_column_without_a_graph_points_at_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["impact", "--column", "fct_matches.match_intensity"])
    assert result.exit_code == 1
    assert "stitch build" in _plain(result.output)


def test_migrate_relationships_is_registered_and_documented():
    """#135: the command exists, and its help says what it will and will not touch."""
    result = runner.invoke(app, ["migrate-relationships", "--help"])
    assert result.exit_code == 0
    for promise in ("--dry-run", "--force", "cardinality key stays"):
        assert promise in result.stdout.replace("\n", " ").replace("  ", " ")


def test_migrate_relationships_declines_when_the_target_form_is_meta(tmp_path):
    """Migrating to the form you are already in is a no-op worth saying out loud."""
    config = tmp_path / "stitch.yml"
    config.write_text(
        "metabase:\n"
        "  url: https://mb.example.com\n"
        "  api_key: ${STITCH_METABASE_API_KEY}\n"
        "  databases:\n"
        "    - metabase_name: Analytics\n"
        "      dbt_database: ANALYTICS\n"
        "relationships:\n"
        "  write_to: meta\n"
    )
    result = runner.invoke(app, ["migrate-relationships", "--config", str(config)])
    assert result.exit_code == 0
    assert "nothing to migrate to" in result.stdout
