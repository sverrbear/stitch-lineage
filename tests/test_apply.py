"""`stitch apply` end to end, in a throwaway git repo (issue #27, SPEC.md section 8.2)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import plain, uncoloured
from fastapi.testclient import TestClient
from ruamel.yaml import YAML
from typer.testing import CliRunner

from stitch_lineage.app.server import create_app
from stitch_lineage.cli import app
from stitch_lineage.graph.schema import (
    Confidence,
    Edge,
    EdgeType,
    Graph,
    Node,
    NodeType,
    column_node_id,
)
from stitch_lineage.io.graph_store import previous_graph_path, read_graph, write_graph
from stitch_lineage.io.staged_store import (
    StagedDescription,
    StagedRelationship,
    descriptions_path,
    read_descriptions,
    read_staged,
    write_descriptions,
    write_staged,
)

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
    assert "nothing staged" in plain(result.output)
    assert "stitch serve" in plain(result.output)


def test_apply_is_listed_in_help():
    assert "apply" in runner.invoke(app, ["--help"]).output


# --- dry run -----------------------------------------------------------------------------


def test_dry_run_shows_the_diff_and_writes_nothing(repo, store, wide_console):
    _stage(store, _entry())
    before = (repo / MARTS).read_text()

    result = _run("--dry-run")
    assert result.exit_code == 0
    assert f"a/{MARTS}" in plain(result.output)
    assert "+          - relationships:" in uncoloured(result.output)
    assert "+              to: ref('dim_customers')" in uncoloured(result.output)
    assert "--dry-run: nothing written" in plain(result.output)
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
    assert "aborted" in plain(result.output)
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
    assert "apply to" not in plain(result.output)


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
    assert "applied 1 relationship" in plain(result.output)


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
    assert "applied 2 relationships" in plain(result.output)
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
    assert "refusing" in plain(result.output)
    assert "--force" in plain(result.output)
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
    assert "cannot apply" in plain(result.output)
    assert "has no schema YAML file" in plain(result.output)
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
    assert "manifest.json" in plain(result.output)
    assert "dbt docs generate" in plain(result.output)


def test_a_corrupt_store_names_the_fix(repo, store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("relationships: [unclosed\n")
    result = _run("--yes")
    assert result.exit_code == 1
    assert "delete the file" in plain(result.output)


def test_contract_constraint_fails_with_the_alternatives(repo, store):
    _stage(store, _entry())
    (repo / "stitch.yml").write_text(CONFIG.replace("relationships_test", "contract_constraint"))
    result = _run("--yes")
    assert result.exit_code == 1
    assert "not implemented" in plain(result.output)
    assert "relationships_test" in plain(result.output)


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
    assert "cannot carry cardinality" in plain(result.output)
    assert "write_to: meta" in plain(result.output)


def test_many_to_one_is_not_warned_about(repo, store):
    _stage(store, _entry())
    assert "cannot carry cardinality" not in _run("--dry-run").output


# --- the graph patch (issue #68) -----------------------------------------------------------------

GRAPH_COLUMNS = {
    "model.demo.fct_orders": ["order_id", "customer_id"],
    "model.demo.dim_customers": ["customer_id"],
    "model.demo.fct_events": ["event_id", "user_id"],
    "model.demo.dim_users": ["user_id"],
}
ORDERS_FK = column_node_id("model.demo.fct_orders", "customer_id")
CUSTOMERS_PK = column_node_id("model.demo.dim_customers", "customer_id")
CLOSING_LINE = (
    "applied 1 relationship — graph updated, refresh the app · "
    "next 'stitch build' will confirm them from the manifest"
)


def _graph_file(root, edges=(), without=()):
    """Write a built graph carrying the models and columns the staged fixtures point at."""
    nodes = []
    for unique_id, columns in GRAPH_COLUMNS.items():
        model = unique_id.rsplit(".", 1)[-1]
        nodes.append(Node(node_id=unique_id, node_type=NodeType.MODEL, name=model))
        nodes += [
            Node(node_id=column_node_id(unique_id, column), node_type=NodeType.COLUMN, name=column)
            for column in columns
            if f"{model}.{column}" not in without
        ]
    path = Path(root) / ".stitch" / "graph.json"
    graph = Graph(generated_at="2026-08-10T00:00:00+00:00", nodes=nodes, edges=list(edges))
    write_graph(graph, path)
    return path


def _relates_to(path):
    return [edge for edge in read_graph(path).edges if edge.edge_type is EdgeType.RELATES_TO]


def _applied_edge(confidence=Confidence.VALIDATED, **evidence):
    return Edge(
        from_=ORDERS_FK,
        to=CUSTOMERS_PK,
        edge_type=EdgeType.RELATES_TO,
        confidence=confidence,
        evidence=evidence,
    )


def test_apply_patches_the_relationship_it_wrote_into_the_graph(repo, store):
    graph_path = _graph_file(repo)
    _stage(store, _entry())

    result = _run("--yes")
    assert result.exit_code == 0
    assert CLOSING_LINE in plain(result.output)

    edges = _relates_to(graph_path)
    assert len(edges) == 1
    assert (edges[0].from_, edges[0].to) == (ORDERS_FK, CUSTOMERS_PK)
    assert edges[0].confidence == Confidence.VALIDATED
    assert edges[0].evidence == {"source": "stitch apply", "write_to": "relationships_test"}


def test_the_meta_form_patches_a_declared_edge_that_keeps_the_cardinality(repo, store):
    (repo / "stitch.yml").write_text(CONFIG.replace("relationships_test", "meta"))
    graph_path = _graph_file(repo)
    _stage(store, _entry(cardinality="one-to-one"))

    assert _run("--yes").exit_code == 0
    edge = _relates_to(graph_path)[0]
    assert edge.confidence == Confidence.DECLARED
    assert edge.evidence == {
        "source": "stitch apply",
        "write_to": "meta",
        "relationship_type": "one-to-one",
    }


def test_every_written_relationship_is_patched(repo, store):
    graph_path = _graph_file(repo)
    _stage(
        store,
        _entry(),
        _entry(
            from_model="fct_events",
            from_column="user_id",
            to_model="dim_users",
            to_column="user_id",
        ),
    )

    assert _run("--yes").exit_code == 0
    assert {(edge.from_, edge.to) for edge in _relates_to(graph_path)} == {
        (ORDERS_FK, CUSTOMERS_PK),
        (
            column_node_id("model.demo.fct_events", "user_id"),
            column_node_id("model.demo.dim_users", "user_id"),
        ),
    }


def test_patching_an_edge_the_graph_already_carries_is_a_no_op(repo, store):
    graph_path = _graph_file(
        repo, edges=[_applied_edge(confidence=Confidence.DECLARED, source="column_meta")]
    )
    before = graph_path.read_bytes()
    _stage(store, _entry())

    assert _run("--yes").exit_code == 0
    # not rewritten at all: the next real build reconciles confidence from the manifest
    assert graph_path.read_bytes() == before
    assert len(_relates_to(graph_path)) == 1


def test_a_second_apply_never_duplicates_the_edge(repo, store):
    graph_path = _graph_file(repo)
    _stage(store, _entry())
    _run("--yes")
    after_first = graph_path.read_bytes()

    _stage(store, _entry())
    assert _run("--yes").exit_code == 0
    assert graph_path.read_bytes() == after_first
    assert len(_relates_to(graph_path)) == 1


def test_an_already_declared_relationship_still_reaches_a_stale_graph(repo, store):
    _stage(store, _entry())
    _run("--yes")  # writes the YAML; there is no graph yet
    graph_path = _graph_file(repo)  # a graph that predates the declaration

    _stage(store, _entry())
    result = _run("--yes")
    assert result.exit_code == 0
    assert "cleared 1 already-declared entry" in plain(result.output)
    assert "graph updated, refresh the app" in plain(result.output)
    assert len(_relates_to(graph_path)) == 1


def test_the_patched_graph_is_byte_identical_to_a_built_one(repo, store, tmp_path):
    graph_path = _graph_file(repo)
    _stage(store, _entry())
    assert _run("--yes").exit_code == 0

    reference = _graph_file(
        tmp_path / "reference",
        edges=[_applied_edge(source="stitch apply", write_to="relationships_test")],
    )
    assert graph_path.read_bytes() == reference.read_bytes()


def test_the_patch_leaves_the_previous_build_snapshot_alone(repo, store):
    # only `stitch build` snapshots: a patch adds relates_to edges, which impact never
    # traverses, so rolling the snapshot forward here would only lose the answer to
    # "what did my last build change" (issue #53)
    graph_path = _graph_file(repo)
    snapshot = previous_graph_path(graph_path)
    shutil.copyfile(graph_path, snapshot)
    before = snapshot.read_bytes()
    _stage(store, _entry())

    assert _run("--yes").exit_code == 0
    assert len(_relates_to(graph_path)) == 1
    assert snapshot.read_bytes() == before


def test_the_patch_does_not_invent_a_snapshot(repo, store):
    graph_path = _graph_file(repo)
    _stage(store, _entry())

    assert _run("--yes").exit_code == 0
    assert not previous_graph_path(graph_path).exists()


def test_no_graph_update_leaves_the_graph_alone(repo, store):
    graph_path = _graph_file(repo)
    before = graph_path.read_bytes()
    _stage(store, _entry())

    result = _run("--yes", "--no-graph-update")
    assert result.exit_code == 0
    assert "applied 1 relationship" in plain(result.output)
    assert "graph updated" not in plain(result.output)
    assert graph_path.read_bytes() == before


def test_a_missing_graph_is_a_note_not_a_failure(repo, store):
    _stage(store, _entry())
    result = _run("--yes")
    assert result.exit_code == 0
    assert "no graph at" in plain(result.output)
    assert "run 'stitch build'" in plain(result.output)
    assert "graph updated" not in plain(result.output)
    assert "- relationships:" in (repo / MARTS).read_text()


def test_an_unparseable_graph_is_a_note_not_a_failure(repo, store):
    graph_path = repo / ".stitch" / "graph.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text("{ not a graph")
    _stage(store, _entry())

    result = _run("--yes")
    assert result.exit_code == 0
    assert "does not parse" in plain(result.output)
    assert "- relationships:" in (repo / MARTS).read_text()


def test_a_relationship_whose_columns_are_not_in_the_graph_is_reported(repo, store):
    graph_path = _graph_file(repo, without=("dim_customers.customer_id",))
    _stage(store, _entry())

    result = _run("--yes")
    assert result.exit_code == 0
    assert "not added to the graph" in plain(result.output)
    assert "fct_orders.customer_id -> dim_customers.customer_id" in plain(result.output)
    assert "graph updated" not in plain(result.output)
    assert _relates_to(graph_path) == []


def test_the_app_serves_the_patched_relationship(repo, store):
    graph_path = _graph_file(repo)
    _stage(store, _entry())
    assert _run("--yes").exit_code == 0

    server = create_app(graph_path, None, None, store, repo / ".stitch" / "layout.yml")
    with TestClient(server) as client:
        payload = client.get("/api/graph").json()
    assert [edge for edge in payload["edges"] if edge["edge_type"] == "relates_to"] == [
        {
            "from": ORDERS_FK,
            "to": CUSTOMERS_PK,
            "edge_type": "relates_to",
            "confidence": "validated",
            "evidence": {"source": "stitch apply", "write_to": "relationships_test"},
        }
    ]


# --- --build ------------------------------------------------------------------------------------


@pytest.fixture
def builds(monkeypatch):
    """Record how apply invokes the build pipeline instead of running dbt and Metabase."""
    calls = []
    monkeypatch.setattr("stitch_lineage.cli._run_build", lambda **kwargs: calls.append(kwargs))
    return calls


def test_build_runs_the_standard_pipeline_after_applying(repo, store, builds):
    _graph_file(repo)
    _stage(store, _entry())

    assert _run("--yes", "--build").exit_code == 0
    assert len(builds) == 1
    assert builds[0]["config"] == Path("stitch.yml")
    # the standard build: Metabase side included, no --check, docs left to dbt.auto_docs
    assert not builds[0].get("no_metabase")
    assert not builds[0].get("check")
    assert builds[0].get("docs") is None


def test_apply_does_not_build_unless_asked(repo, store, builds):
    _graph_file(repo)
    _stage(store, _entry())
    assert _run("--yes").exit_code == 0
    assert builds == []


def test_a_dry_run_never_builds(repo, store, builds):
    _stage(store, _entry())
    assert _run("--dry-run", "--build").exit_code == 0
    assert builds == []


def test_build_runs_even_when_everything_was_already_declared(repo, store, builds):
    _stage(store, _entry())
    _run("--yes")
    _stage(store, _entry())

    assert _run("--yes", "--build").exit_code == 0
    assert len(builds) == 1


# --- descriptions in the same run (issue #70) ---------------------------------------------


def _stage_descriptions(repo, *entries):
    write_descriptions(list(entries), descriptions_path(repo / ".stitch"))


def _description(entity="fct_orders", column="customer_id", text="Who placed it, FK to customers"):
    return StagedDescription(entity=entity, column=column, new_description=text)


def _yaml_value(repo, path, model, column=None):
    document = YAML(typ="safe").load((repo / path).read_text())
    entry = next(item for item in document["models"] if item["name"] == model)
    if column is None:
        return entry.get("description")
    return next(item for item in entry["columns"] if item["name"] == column).get("description")


def test_a_staged_description_is_written_and_cleared(repo):
    _stage_descriptions(repo, _description())
    result = _run("--yes")
    assert result.exit_code == 0
    assert _yaml_value(repo, MARTS, "fct_orders", "customer_id") == "Who placed it, FK to customers"
    assert read_descriptions(descriptions_path(repo / ".stitch")) == []
    assert "applied 1 description" in plain(result.output)


def test_a_model_description_is_written(repo):
    _stage_descriptions(repo, _description(entity="dim_customers", column=None, text="Customers"))
    assert _run("--yes").exit_code == 0
    assert _yaml_value(repo, MARTS, "dim_customers") == "Customers"


def test_relationships_and_descriptions_apply_in_one_run(repo, store):
    _stage(store, _entry())
    _stage_descriptions(repo, _description())
    result = _run("--yes")
    assert result.exit_code == 0
    assert "1 staged relationship" in plain(result.output)
    assert "1 staged description" in plain(result.output)
    assert "applied 1 relationship and 1 description" in plain(result.output)
    written = (repo / MARTS).read_text()
    assert "- relationships:" in written
    assert "Who placed it, FK to customers" in written
    assert read_staged(store) == []
    assert read_descriptions(descriptions_path(repo / ".stitch")) == []


def test_a_dry_run_shows_the_description_diff_and_writes_nothing(repo, wide_console):
    before = (repo / MARTS).read_text()
    _stage_descriptions(repo, _description())
    result = _run("--dry-run")
    assert result.exit_code == 0
    # the replaced line, in the quoting style the file already used
    assert "+        description: 'Who placed it, FK to customers'" in uncoloured(result.output)
    assert "-        description: 'Who placed the order'" in uncoloured(result.output)
    assert (repo / MARTS).read_text() == before
    assert len(read_descriptions(descriptions_path(repo / ".stitch"))) == 1


def test_an_empty_run_mentions_both_ways_to_stage(repo):
    result = _run()
    assert result.exit_code == 0
    assert "nothing staged" in plain(result.output)
    assert "edit a description" in plain(result.output)


def test_the_graph_patch_updates_node_descriptions(repo):
    graph_path = _graph_file(repo)
    _stage_descriptions(repo, _description(), _description(entity="dim_customers", column=None))

    result = _run("--yes")
    assert result.exit_code == 0
    assert "graph updated, refresh the app" in plain(result.output)
    nodes = {node.node_id: node for node in read_graph(graph_path).nodes}
    assert nodes[ORDERS_FK].description == "Who placed it, FK to customers"
    assert nodes["model.demo.dim_customers"].description == "Who placed it, FK to customers"


def test_a_description_whose_target_is_not_in_the_graph_is_reported(repo):
    graph_path = _graph_file(repo, without=("fct_orders.customer_id",))
    _stage_descriptions(repo, _description())

    result = _run("--yes")
    assert result.exit_code == 0
    assert "not added to the graph" in plain(result.output)
    assert "fct_orders.customer_id description" in plain(result.output)
    assert read_graph(graph_path).nodes  # graph still intact
    assert _yaml_value(repo, MARTS, "fct_orders", "customer_id") == "Who placed it, FK to customers"


def test_no_graph_update_leaves_descriptions_out_of_the_graph(repo):
    graph_path = _graph_file(repo)
    before = graph_path.read_bytes()
    _stage_descriptions(repo, _description())

    assert _run("--yes", "--no-graph-update").exit_code == 0
    assert graph_path.read_bytes() == before


def test_an_unappliable_description_stays_staged(repo):
    orphan = _description(entity="dim_stores", column=None, text="Stores")
    _stage_descriptions(repo, orphan)

    result = _run("--yes")
    assert result.exit_code == 1
    assert "cannot apply" in plain(result.output)
    assert "has no schema YAML file" in plain(result.output)
    assert [entry.id for entry in read_descriptions(descriptions_path(repo / ".stitch"))] == [
        orphan.id
    ]


def test_a_dirty_file_refuses_the_description_too(repo):
    _stage_descriptions(repo, _description())
    target = repo / MARTS
    target.write_text(target.read_text() + "\n# local edit\n")

    result = _run("--yes")
    assert result.exit_code == 1
    assert "refusing" in plain(result.output)
    assert len(read_descriptions(descriptions_path(repo / ".stitch"))) == 1


def test_a_corrupt_description_store_names_the_fix(repo):
    path = descriptions_path(repo / ".stitch")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("descriptions: [unclosed\n")
    result = _run("--yes")
    assert result.exit_code == 1
    assert "delete the file" in plain(result.output)
