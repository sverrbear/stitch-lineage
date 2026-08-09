"""`stitch apply` end to end, in a throwaway git repo (issue #27, SPEC.md section 8.2)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stitch_lineage.cli import app
from stitch_lineage.io.staged_store import StagedRelationship, read_staged, write_staged

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures" / "dbt_repo"
MARTS = "models/marts/_schema.yml"
EVENTS = "models/events/_schema.yml"

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


def _node(name, schema="marts", patch=MARTS):
    return {
        "resource_type": "model",
        "name": name,
        "schema": schema,
        "patch_path": f"demo://{patch}" if patch else None,
    }


MANIFEST = {
    "metadata": {"dbt_version": "1.9.0"},
    "nodes": {
        "model.demo.fct_orders": _node("fct_orders"),
        "model.demo.dim_customers": _node("dim_customers"),
        "model.demo.fct_events": _node("fct_events", "events", EVENTS),
        "model.demo.dim_users": _node("dim_users", "events", EVENTS),
        "model.demo.dim_stores": _node("dim_stores", patch=None),
    },
}


def _entry(from_model="fct_orders", from_column="customer_id", **kwargs):
    return StagedRelationship(
        from_model=from_model,
        from_column=from_column,
        to_model=kwargs.pop("to_model", "dim_customers"),
        to_column=kwargs.pop("to_column", "customer_id"),
        **kwargs,
    )


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=test", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A dbt project with stitch.yml, artifacts and a clean git history."""
    root = tmp_path / "repo"
    shutil.copytree(FIXTURES, root)
    (root / "stitch.yml").write_text(CONFIG)
    (root / "target").mkdir()
    (root / "target" / "manifest.json").write_text(json.dumps(MANIFEST))
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def store(repo):
    return repo / ".stitch" / "staged_relationships.yml"


def _stage(store, *entries):
    write_staged(list(entries), store)


def _run(*args, **kwargs):
    return runner.invoke(app, ["apply", *args], **kwargs)


# --- nothing to do ----------------------------------------------------------------------


def test_an_empty_store_exits_zero_and_points_at_serve(repo):
    result = _run()
    assert result.exit_code == 0
    assert "nothing staged" in result.output
    assert "stitch serve" in result.output


def test_apply_is_listed_in_help():
    assert "apply" in runner.invoke(app, ["--help"]).output


# --- dry run -----------------------------------------------------------------------------


def test_dry_run_shows_the_diff_and_writes_nothing(repo, store):
    _stage(store, _entry())
    before = (repo / MARTS).read_text()

    result = _run("--dry-run")
    assert result.exit_code == 0
    assert f"a/{MARTS}" in result.output
    assert "+          - relationships:" in result.output
    assert "+              to: ref('dim_customers')" in result.output
    assert "--dry-run: nothing written" in result.output
    assert (repo / MARTS).read_text() == before


def test_dry_run_leaves_the_store_intact(repo, store):
    _stage(store, _entry())
    _run("--dry-run")
    assert len(read_staged(store)) == 1


def test_dry_run_never_prompts(repo, store):
    _stage(store, _entry())
    # no stdin supplied: a prompt would raise rather than silently pass
    assert _run("--dry-run").exit_code == 0


# --- confirmation -------------------------------------------------------------------------


def test_declining_the_prompt_writes_nothing(repo, store):
    _stage(store, _entry())
    before = (repo / MARTS).read_text()

    result = _run(input="n\n")
    assert result.exit_code == 1
    assert "aborted" in result.output
    assert (repo / MARTS).read_text() == before
    assert len(read_staged(store)) == 1


def test_accepting_the_prompt_applies(repo, store):
    _stage(store, _entry())
    result = _run(input="y\n")
    assert result.exit_code == 0
    assert "- relationships:" in (repo / MARTS).read_text()


def test_yes_skips_the_prompt(repo, store):
    _stage(store, _entry())
    result = _run("--yes")
    assert result.exit_code == 0
    assert "apply to" not in result.output


# --- applying -----------------------------------------------------------------------------


def test_apply_writes_the_relationship_and_clears_the_store(repo, store):
    _stage(store, _entry())
    result = _run("--yes")
    assert result.exit_code == 0

    written = (repo / MARTS).read_text()
    assert "          - relationships:" in written
    assert "              to: ref('dim_customers')" in written
    assert "              field: customer_id" in written
    assert read_staged(store) == []
    assert "applied 1 relationship" in result.output


def test_apply_is_insert_only_against_the_committed_file(repo, store):
    _stage(store, _entry())
    _run("--yes")
    diff = _git(repo, "diff", "--unified=0", "--", MARTS).stdout
    removed = [
        line for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")
    ]
    assert removed == []


def test_apply_reports_every_file_it_wrote(repo, store):
    _stage(
        store,
        _entry(),
        _entry(from_model="fct_events", from_column="user_id", to_model="dim_users"),
    )
    result = _run("--yes")
    assert result.exit_code == 0
    assert MARTS in result.output.replace("\n", "")
    assert "applied 2 relationships" in result.output
    assert read_staged(store) == []


def test_a_second_apply_of_the_same_relationship_is_a_no_op(repo, store):
    _stage(store, _entry())
    _run("--yes")
    after = (repo / MARTS).read_text()

    _stage(store, _entry())
    result = _run("--yes")
    assert result.exit_code == 0
    assert (repo / MARTS).read_text() == after
    assert read_staged(store) == []


# --- the dirty-file guard -------------------------------------------------------------------


def test_a_dirty_target_file_is_refused(repo, store):
    _stage(store, _entry())
    target = repo / MARTS
    target.write_text(target.read_text() + "\n# local edit\n")
    before = target.read_text()

    result = _run("--yes")
    assert result.exit_code == 1
    assert "refusing" in result.output
    assert "--force" in result.output
    assert target.read_text() == before
    assert len(read_staged(store)) == 1


def test_force_writes_over_a_dirty_file(repo, store):
    _stage(store, _entry())
    target = repo / MARTS
    target.write_text(target.read_text() + "\n# local edit\n")

    result = _run("--yes", "--force")
    assert result.exit_code == 0
    assert "- relationships:" in target.read_text()
    assert "# local edit" in target.read_text()
    assert read_staged(store) == []


def test_a_clean_file_is_written_even_when_another_file_is_dirty(repo, store):
    marts = _entry()
    events = _entry(from_model="fct_events", from_column="user_id", to_model="dim_users")
    _stage(store, marts, events)
    (repo / MARTS).write_text((repo / MARTS).read_text() + "\n# local edit\n")

    result = _run("--yes")
    assert result.exit_code == 1
    assert "- relationships:" in (repo / EVENTS).read_text()
    # the refused file's entry stays staged; the written one clears
    assert [entry.id for entry in read_staged(store)] == [marts.id]


def test_outside_a_git_repo_there_is_nothing_to_guard(tmp_path, monkeypatch):
    root = tmp_path / "plain"
    shutil.copytree(FIXTURES, root)
    (root / "stitch.yml").write_text(CONFIG)
    (root / "target").mkdir()
    (root / "target" / "manifest.json").write_text(json.dumps(MANIFEST))
    monkeypatch.chdir(root)
    write_staged([_entry()], root / ".stitch" / "staged_relationships.yml")

    result = _run("--yes")
    assert result.exit_code == 0
    assert "- relationships:" in (root / MARTS).read_text()


# --- failures ---------------------------------------------------------------------------------


def test_an_unappliable_entry_is_reported_and_stays_staged(repo, store):
    orphan = _entry(from_model="dim_stores", from_column="region_id")
    _stage(store, orphan)

    result = _run("--yes")
    assert result.exit_code == 1
    assert "cannot apply" in result.output
    assert "has no schema YAML file" in result.output
    assert [entry.id for entry in read_staged(store)] == [orphan.id]


def test_a_failure_does_not_stop_the_appliable_entries(repo, store):
    good = _entry()
    orphan = _entry(from_model="dim_stores", from_column="region_id")
    _stage(store, good, orphan)

    result = _run("--yes")
    assert result.exit_code == 1
    assert "- relationships:" in (repo / MARTS).read_text()
    assert [entry.id for entry in read_staged(store)] == [orphan.id]


def test_a_missing_manifest_gives_the_standard_artifact_error(repo, store):
    _stage(store, _entry())
    (repo / "target" / "manifest.json").unlink()

    result = _run("--yes")
    assert result.exit_code == 1
    assert "manifest.json" in result.output
    assert "dbt docs generate" in result.output


def test_a_corrupt_store_names_the_fix(repo, store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("relationships: [unclosed\n")
    result = _run("--yes")
    assert result.exit_code == 1
    assert "delete the file" in result.output


def test_contract_constraint_fails_with_the_alternatives(repo, store):
    _stage(store, _entry())
    (repo / "stitch.yml").write_text(CONFIG.replace("relationships_test", "contract_constraint"))
    result = _run("--yes")
    assert result.exit_code == 1
    assert "not implemented" in result.output
    assert "relationships_test" in result.output


# --- the meta form through the CLI ---------------------------------------------------------------


def test_write_to_meta_writes_the_interop_keys(repo, store):
    _stage(store, _entry())
    (repo / "stitch.yml").write_text(CONFIG.replace("relationships_test", "meta"))

    result = _run("--yes")
    assert result.exit_code == 0
    written = (repo / MARTS).read_text()
    assert "metabase.fk_target_table: marts.dim_customers" in written
    assert "metabase.fk_target_field: customer_id" in written
    assert "relationship_type: many-to-one" in written


def test_a_relationships_test_warns_that_cardinality_is_dropped(repo, store):
    _stage(store, _entry(cardinality="one-to-one"))
    result = _run("--dry-run")
    assert "cannot carry cardinality" in result.output
    assert "write_to: meta" in result.output


def test_many_to_one_is_not_warned_about(repo, store):
    _stage(store, _entry())
    assert "cannot carry cardinality" not in _run("--dry-run").output
