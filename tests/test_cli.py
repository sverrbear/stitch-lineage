from typer.testing import CliRunner

from stitch_lineage import __version__
from stitch_lineage.cli import app

runner = CliRunner()

VALID_CONFIG = """
metabase:
  url: https://mb.example.com
  api_key: ${STITCH_METABASE_API_KEY}
  databases:
    - metabase_name: Analytics
      dbt_database: ANALYTICS
"""


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


def test_build_reports_not_implemented(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STITCH_METABASE_API_KEY", "mb_test_key")
    (tmp_path / "stitch.yml").write_text(VALID_CONFIG)
    result = runner.invoke(app, ["build"])
    assert result.exit_code == 2
    assert "not yet implemented" in result.output


def test_search_without_graph_points_at_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["search", "match_intensity"])
    assert result.exit_code == 1
    assert "stitch build" in result.output
