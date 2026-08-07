"""End-to-end pipeline tests: build -> check -> no-metabase rebuild -> impact -> search
-> doctor -> export, all through the real CLI against a tmp dbt project with the
Metabase API mocked via `responses`."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import responses
from typer.testing import CliRunner

from stitch_lineage.cli import app
from stitch_lineage.graph.schema import EdgeType, Graph
from stitch_lineage.io.graph_store import graphs_semantically_equal, read_graph

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"
MB_URL = "https://mb.example.com"

STITCH_YML = """\
dbt:
  project_dir: .
  target_path: target/
metabase:
  url: ${STITCH_METABASE_URL}
  api_key: ${STITCH_METABASE_API_KEY}
  databases:
    - metabase_name: "Analytics"
      dbt_database: analytics
output:
  dir: .stitch/
"""

MB_ENDPOINTS = (
    ("/api/session/properties", "session_properties"),
    ("/api/database", "databases"),
    ("/api/database/2/metadata", "database_metadata_2"),
    ("/api/card", "cards"),
    ("/api/dashboard", "dashboards"),
    ("/api/dashboard/301", "dashboard_301"),
    ("/api/dashboard/302", "dashboard_302"),
    ("/api/collection", "collections"),
)

ORDER_TOTAL = "model.demo.fct_orders::order_total"
AMOUNT_USD = "model.demo.stg_payments::amount_usd"


def _register_metabase(rsps: responses.RequestsMock) -> None:
    for path, fixture in MB_ENDPOINTS:
        payload = json.loads((FIXTURES / "metabase" / f"{fixture}.json").read_text())
        rsps.add(responses.GET, f"{MB_URL}{path}", json=payload)


@pytest.fixture
def project(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    shutil.copy(FIXTURES / "dbt" / "manifest.json", target / "manifest.json")
    shutil.copy(FIXTURES / "dbt" / "catalog.json", target / "catalog.json")
    (tmp_path / "stitch.yml").write_text(STITCH_YML)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STITCH_METABASE_URL", MB_URL)
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "test-key")
    return tmp_path


def _full_build():
    with responses.RequestsMock() as rsps:
        _register_metabase(rsps)
        result = runner.invoke(app, ["build"])
    assert result.exit_code == 0, result.output
    return result


def _graph(project: Path) -> Graph:
    return read_graph(project / ".stitch" / "graph.json")


def _edit_json(path: Path, mutate) -> None:
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def _drop_stg_payments_amount_usd(project: Path) -> None:
    def mutate(manifest):
        stg = manifest["nodes"]["model.demo.stg_payments"]
        del stg["columns"]["amount_usd"]
        stg["compiled_code"] = stg["compiled_code"].replace(
            "\n    , amount * fx_rate as amount_usd", ""
        )

    _edit_json(project / "target" / "manifest.json", mutate)


def _git(project: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=e2e@example.com", "-c", "user.name=e2e", *args],
        cwd=project,
        check=True,
        capture_output=True,
    )


# --- build ------------------------------------------------------------------


def test_full_build_produces_unbroken_end_to_end_chain(project):
    result = _full_build()
    graph = _graph(project)
    edges = {(e.from_, e.to, e.edge_type.value) for e in graph.edges}
    chain = [
        ("source.demo.app.raw_payments::amount", AMOUNT_USD, "feeds"),
        (AMOUNT_USD, ORDER_TOTAL, "feeds"),
        (ORDER_TOTAL, "mb_field::102", "binds_to"),
        ("mb_field::102", "mb_card::201", "consumed_by"),
        ("mb_card::201", "mb_dash::301", "appears_on"),
    ]
    for hop in chain:
        assert hop in edges, f"missing hop: {hop}"

    cov = graph.coverage
    assert cov.models_bound == 1
    assert cov.models_total == 7
    assert "model.demo.fct_orders" not in cov.unbound_models
    assert (cov.mbql_cards_resolved, cov.mbql_cards_total) == (7, 8)
    assert (cov.native_cards_resolved, cov.native_cards_total) == (0, 1)
    assert (cov.dashboards, cov.dashboards_total) == (2, 2)
    assert (cov.columns_traced, cov.columns_total) == (24, 26)
    assert set(cov.unresolved_cards) == {205, 208}
    ghost = [r for r in cov.unresolved_field_refs if r.get("card_id") == 208]
    assert ghost and ghost[0]["reason"] == "unresolvable field name"
    assert graph.metabase_version == "v0.53.2"
    assert graph.dbt_invocation_id == "11111111-2222-3333-4444-555555555555"

    assert "models bound" in result.output
    assert "dbt column lineage" in result.output
    assert "case-only mismatch" in result.output


def test_build_progress_renders_to_stderr_and_keeps_stdout_clean(project):
    result = _full_build()
    graph = _graph(project)
    lines = result.stdout.splitlines()
    # stdout starts with the summary line and carries the coverage report, exactly as
    # without progress -- the progress display goes to stderr (free-form there)
    assert lines[0] == (
        f"wrote .stitch/graph.json ({len(graph.nodes)} nodes, {len(graph.edges)} edges)"
    )
    assert lines[1].startswith("models bound")
    for stage in (
        "loading artifacts",
        "tracing column lineage",
        "fetching Metabase",
        "resolving cards",
        "writing graph",
    ):
        assert stage not in result.stdout, f"progress stage '{stage}' leaked to stdout"


def test_rebuild_is_deterministic_modulo_volatile_header(project):
    graph_path = project / ".stitch" / "graph.json"
    with responses.RequestsMock() as rsps:
        _register_metabase(rsps)
        assert runner.invoke(app, ["build"]).exit_code == 0
        first = graph_path.read_text()
        assert runner.invoke(app, ["build"]).exit_code == 0
    second = graph_path.read_text()

    assert graphs_semantically_equal(
        Graph.model_validate_json(first), Graph.model_validate_json(second)
    )
    changed = [
        (a, b) for a, b in zip(first.splitlines(), second.splitlines(), strict=True) if a != b
    ]
    assert all("generated_at" in a for a, _ in changed)


def test_build_check_green_then_detects_drift(project):
    graph_path = project / ".stitch" / "graph.json"
    with responses.RequestsMock() as rsps:
        _register_metabase(rsps)
        assert runner.invoke(app, ["build"]).exit_code == 0
        committed = graph_path.read_text()

        in_sync = runner.invoke(app, ["build", "--check"])
        assert in_sync.exit_code == 0, in_sync.output
        assert "up to date" in in_sync.output
        assert graph_path.read_text() == committed  # --check never writes

        payload = json.loads(committed)
        payload["nodes"] = [n for n in payload["nodes"] if n["node_id"] != ORDER_TOTAL]
        tweaked = json.dumps(payload)
        graph_path.write_text(tweaked)

        drifted = runner.invoke(app, ["build", "--check"])
    assert drifted.exit_code == 1
    assert "drift" in drifted.output
    assert graph_path.read_text() == tweaked  # --check never writes


def test_build_no_metabase_reuses_baseline_and_recomputes_binds(project, monkeypatch):
    _full_build()
    baseline = _graph(project)

    def mutate(manifest):
        fct = manifest["nodes"]["model.demo.fct_orders"]
        del fct["columns"]["order_total"]
        fct["compiled_code"] = fct["compiled_code"].replace(
            "\n    , payments.amount_usd as order_total", ""
        )

    _edit_json(project / "target" / "manifest.json", mutate)

    def mutate_catalog(catalog):
        del catalog["nodes"]["model.demo.fct_orders"]["columns"]["ORDER_TOTAL"]

    _edit_json(project / "target" / "catalog.json", mutate_catalog)

    monkeypatch.delenv("STITCH_METABASE_URL")
    monkeypatch.delenv("STITCH_METABASE_API_KEY")
    with responses.RequestsMock() as rsps:
        result = runner.invoke(app, ["build", "--no-metabase"])
        assert result.exit_code == 0, result.output
        assert len(rsps.calls) == 0

    rebuilt = _graph(project)

    def mb_nodes(graph):
        return {
            n.node_id
            for n in graph.nodes
            if n.node_id.startswith(("mb_field::", "mb_card::", "mb_dash::"))
        }

    def mb_edges(graph):
        return {
            (e.from_, e.to, e.edge_type.value)
            for e in graph.edges
            if e.edge_type in (EdgeType.CONSUMED_BY, EdgeType.APPEARS_ON)
        }

    assert mb_nodes(rebuilt) == mb_nodes(baseline)
    assert mb_edges(rebuilt) == mb_edges(baseline)
    assert rebuilt.metabase_version == baseline.metabase_version
    assert rebuilt.coverage.mbql_cards_total == baseline.coverage.mbql_cards_total
    assert rebuilt.coverage.dashboards == baseline.coverage.dashboards

    def binds(graph):
        return {(e.from_, e.to) for e in graph.edges if e.edge_type is EdgeType.BINDS_TO}

    assert (ORDER_TOTAL, "mb_field::102") in binds(baseline)
    assert (ORDER_TOTAL, "mb_field::102") not in binds(rebuilt)
    assert (f"{ORDER_TOTAL.rsplit('::', 1)[0]}::order_id", "mb_field::100") in binds(rebuilt)


def test_build_no_metabase_without_baseline_is_dbt_only(project, monkeypatch):
    monkeypatch.delenv("STITCH_METABASE_URL")
    monkeypatch.delenv("STITCH_METABASE_API_KEY")
    with responses.RequestsMock() as rsps:
        result = runner.invoke(app, ["build", "--no-metabase"])
        assert result.exit_code == 0, result.output
        assert len(rsps.calls) == 0
    assert "dbt-only" in result.output
    graph = _graph(project)
    assert not any(n.node_id.startswith("mb_") for n in graph.nodes)
    assert graph.coverage.models_bound == 0
    assert graph.coverage.columns_traced == 24

    # a second run over the dbt-only baseline must not pretend a Metabase side exists
    rerun = runner.invoke(app, ["build", "--no-metabase"])
    assert rerun.exit_code == 0, rerun.output
    assert "no Metabase side" in rerun.output
    assert "models bound" not in rerun.output


# --- impact -----------------------------------------------------------------


def test_impact_reports_downstream_blast_radius(project, monkeypatch):
    _full_build()
    _git(project, "init", "-b", "main")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "baseline graph")

    _drop_stg_payments_amount_usd(project)
    with responses.RequestsMock():
        assert runner.invoke(app, ["build", "--no-metabase"]).exit_code == 0

    monkeypatch.delenv("STITCH_METABASE_URL")
    monkeypatch.delenv("STITCH_METABASE_API_KEY")

    result = runner.invoke(app, ["impact", "--base", "main", "--format", "github-comment"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "1 column removed or renamed" in out
    assert "stg_payments.amount_usd" in out
    assert "downstream model" in out
    assert "fct_orders" in out
    assert "mart_payments" in out
    assert "#201 Orders overview" in out
    assert "Orders Board" in out
    assert "remove+add" in out

    failing = runner.invoke(
        app, ["impact", "--base", "main", "--format", "github-comment", "--fail-on-impact"]
    )
    assert failing.exit_code == 1

    text = runner.invoke(app, ["impact", "--base", "main"])
    assert text.exit_code == 0, text.output
    assert "stg_payments.amount_usd" in text.output

    slack = runner.invoke(app, ["impact", "--base", "main", "--format", "slack"])
    assert slack.exit_code == 0, slack.output
    assert "*⚠ 1 column removed or renamed*" in slack.output
    assert "*stg_payments.amount_usd* → removed" in slack.output
    assert "    • #201 Orders overview (Orders Board, Sverrir)" in slack.output
    assert "Orders Board" in slack.output
    assert "├" not in slack.output and "└" not in slack.output


def test_impact_no_change_is_quiet_and_passes_fail_on_impact(project):
    _full_build()
    _git(project, "init", "-b", "main")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "baseline graph")
    result = runner.invoke(app, ["impact", "--base", "main", "--fail-on-impact"])
    assert result.exit_code == 0, result.output
    assert "no downstream-impacting column changes" in result.output


def test_impact_missing_baseline_is_a_clear_error(project):
    _full_build()
    _git(project, "init", "-b", "main")
    result = runner.invoke(app, ["impact", "--base", "main"])
    assert result.exit_code == 1
    assert "baseline" in result.output


def test_impact_baseline_missing_at_ref_names_the_fix(project):
    _full_build()
    _git(project, "init", "-b", "main")
    _git(project, "add", "stitch.yml")
    _git(project, "commit", "-m", "base ref without a committed graph")
    result = runner.invoke(app, ["impact", "--base", "main"])
    assert result.exit_code == 1
    assert "no committed baseline at main:.stitch/graph.json" in result.output
    assert "diffs the current build against the graph committed on the base ref" in result.output
    assert "run 'stitch build' and commit .stitch/graph.json" in result.output
    assert "--base <ref>" in result.output


def test_impact_bad_base_ref_keeps_the_raw_git_error(project):
    _full_build()
    _git(project, "init", "-b", "main")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "baseline graph")
    result = runner.invoke(app, ["impact", "--base", "nosuchref"])
    assert result.exit_code == 1
    assert "could not load the baseline" in result.output
    assert "nosuchref" in result.output
    assert "no committed baseline" not in result.output


# --- search / export / doctor ------------------------------------------------


def test_search_cli_hits_models_and_cards(project, monkeypatch):
    _full_build()
    monkeypatch.delenv("STITCH_METABASE_URL")
    monkeypatch.delenv("STITCH_METABASE_API_KEY")

    table = runner.invoke(app, ["search", "orders"])
    assert table.exit_code == 0, table.output
    assert "fct_orders" in table.output
    assert "Orders overview" in table.output

    piped = runner.invoke(app, ["search", "orders", "--json"])
    assert piped.exit_code == 0
    rows = [json.loads(line) for line in piped.output.splitlines() if line.strip()]
    types = {row["node_type"] for row in rows}
    assert "model" in types
    assert "mb_card" in types


def test_export_writes_deterministic_jsonl(project, monkeypatch):
    _full_build()
    monkeypatch.delenv("STITCH_METABASE_URL")
    monkeypatch.delenv("STITCH_METABASE_API_KEY")

    assert runner.invoke(app, ["export"]).exit_code == 0
    nodes_path = project / ".stitch" / "export" / "nodes.jsonl"
    edges_path = project / ".stitch" / "export" / "edges.jsonl"
    first_nodes, first_edges = nodes_path.read_text(), edges_path.read_text()
    for line in (*first_nodes.splitlines(), *first_edges.splitlines()):
        json.loads(line)

    assert runner.invoke(app, ["export"]).exit_code == 0
    assert nodes_path.read_text() == first_nodes
    assert edges_path.read_text() == first_edges


def test_export_default_out_follows_config_parent(project, monkeypatch):
    _full_build()
    monkeypatch.delenv("STITCH_METABASE_URL")
    monkeypatch.delenv("STITCH_METABASE_API_KEY")
    elsewhere = project / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    result = runner.invoke(app, ["export", "--config", str(project / "stitch.yml")])
    assert result.exit_code == 0, result.output
    assert (project / ".stitch" / "export" / "nodes.jsonl").is_file()
    assert not (elsewhere / ".stitch").exists()


def test_doctor_unresolved_cards_without_env(project, monkeypatch):
    _full_build()
    monkeypatch.delenv("STITCH_METABASE_URL")
    monkeypatch.delenv("STITCH_METABASE_API_KEY")
    result = runner.invoke(app, ["doctor", "--unresolved-cards"])
    assert result.exit_code == 0, result.output
    assert "unresolved cards (2)" in result.output
    assert "card 205: native SQL" in result.output
    assert "card 208: unresolvable field name" in result.output
    assert "ghost_column" in result.output


def test_doctor_happy_path(project):
    _full_build()
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            f"{MB_URL}/api/session/properties",
            json=json.loads((FIXTURES / "metabase" / "session_properties.json").read_text()),
        )
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "v0.53.2" in result.output
    assert "fail" not in result.output


def test_doctor_missing_artifacts_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STITCH_METABASE_URL", raising=False)
    monkeypatch.delenv("STITCH_METABASE_API_KEY", raising=False)
    (tmp_path / "stitch.yml").write_text(STITCH_YML)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "manifest.json" in result.output
    assert "skip" in result.output  # metabase check skipped, not failed


def test_doctor_unbound_and_untraced_without_env(project, monkeypatch):
    _full_build()
    monkeypatch.delenv("STITCH_METABASE_URL")
    monkeypatch.delenv("STITCH_METABASE_API_KEY")

    unbound = runner.invoke(app, ["doctor", "--unbound"])
    assert unbound.exit_code == 0, unbound.output
    assert "model.demo.mart_payments" in unbound.output
    assert "model.demo.fct_orders" not in unbound.output

    untraced = runner.invoke(app, ["doctor", "--untraced"])
    assert untraced.exit_code == 0, untraced.output
    assert "model.demo.mart_pivot::pivot_a" in untraced.output


def test_doctor_list_databases(project):
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET,
            f"{MB_URL}/api/database",
            json=json.loads((FIXTURES / "metabase" / "databases.json").read_text()),
        )
        result = runner.invoke(app, ["doctor", "--list-databases"])
    assert result.exit_code == 0, result.output
    assert "Analytics" in result.output
    assert "Sample Database" in result.output
