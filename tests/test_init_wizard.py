import json
import shutil
from pathlib import Path

import pytest

from stitch_lineage.config import StitchConfigError, load_config
from stitch_lineage.init_wizard import (
    API_KEY_VAR,
    URL_VAR,
    StitchInitError,
    append_gitignore,
    derive_table_prefix,
    detect_dbt_project,
    disarm_workflow_trigger,
    manifest_facts,
    normalize_metabase_url,
    propose_database_mapping,
    propose_include_schemas,
    render_stitch_yml,
    run_init,
    write_env_example,
)
from stitch_lineage.io.dbt_runner import StitchDbtRunnerError
from stitch_lineage.io.metabase_client import MetabaseAPIError

DBT_FIXTURES = Path(__file__).parent / "fixtures" / "dbt"
MB_FIXTURES = Path(__file__).parent / "fixtures" / "metabase"

API_KEY = "mb_test_key_not_a_real_one"

DBT_PROJECT_YML = """
name: demo
version: "1.0.0"
profile: demo
target-path: target
quoting:
  database: false
  schema: false
  identifier: false
"""


def fixture(directory: Path, name: str):
    return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))


def manifest() -> dict:
    return fixture(DBT_FIXTURES, "manifest")


class ScriptedPrompter:
    """Non-interactive Prompter: answers come from queues, questions are recorded."""

    def __init__(
        self,
        *,
        answers: list[str] | None = None,
        secrets: list[str] | None = None,
        confirms: list[bool] | None = None,
        choices: list[int] | None = None,
    ) -> None:
        self.answers = list(answers or [])
        self.secrets = list(secrets or [])
        self.confirms = list(confirms or [])
        self.choices = list(choices or [])
        self.asked: list[str] = []
        self.said: list[str] = []

    @property
    def inputs(self) -> int:
        """How many times a human had to type something."""
        return len(self.asked)

    def say(self, message: str) -> None:
        self.said.append(message)

    def ask(self, question: str, default: str | None = None) -> str:
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else (default or "")

    def secret(self, question: str) -> str:
        self.asked.append(question)
        return self.secrets.pop(0)

    def confirm(self, question: str, default: bool = True) -> bool:
        self.asked.append(question)
        return self.confirms.pop(0) if self.confirms else default

    def choose(self, question: str, options) -> int:
        self.asked.append(question)
        return self.choices.pop(0) if self.choices else 0

    def output(self) -> str:
        return "\n".join(self.said)


class FakeMetabase:
    """The wizard's slice of MetabaseClient, backed by the synthetic payload fixtures."""

    def __init__(
        self,
        *,
        databases=None,
        metadata=None,
        version: str = "v0.53.2",
        error: MetabaseAPIError | None = None,
    ) -> None:
        self.databases = databases if databases is not None else fixture(MB_FIXTURES, "databases")
        self.metadata = (
            metadata if metadata is not None else fixture(MB_FIXTURES, "database_metadata_2")
        )
        self.version = version
        self.error = error
        self.calls: list[tuple[str, object]] = []

    def assert_version(self) -> str:
        if self.error is not None:
            raise self.error
        self.calls.append(("assert_version", None))
        return self.version

    def list_databases(self) -> list[dict]:
        self.calls.append(("list_databases", None))
        return self.databases["data"] if isinstance(self.databases, dict) else self.databases

    def database_metadata(self, db_id: int) -> dict:
        self.calls.append(("database_metadata", db_id))
        return self.metadata


def make_project(tmp_path: Path, *, with_manifest: bool = True) -> Path:
    root = tmp_path / "dbt_project"
    (root / "target").mkdir(parents=True)
    (root / "dbt_project.yml").write_text(DBT_PROJECT_YML, encoding="utf-8")
    if with_manifest:
        shutil.copy(DBT_FIXTURES / "manifest.json", root / "target" / "manifest.json")
    return root


def happy_prompter() -> ScriptedPrompter:
    """URL, key, mapping confirm, include_schemas confirm -- the four human inputs."""
    return ScriptedPrompter(
        answers=["https://mb.example.com"], secrets=[API_KEY], confirms=[True, True]
    )


def init(root: Path, prompter: ScriptedPrompter, **kwargs):
    kwargs.setdefault("client_factory", lambda url, key: FakeMetabase())
    kwargs.setdefault("env", {})
    return run_init(start_dir=root, prompter=prompter, **kwargs)


# --- detection ------------------------------------------------------------------


def test_detect_dbt_project_reads_what_dbt_already_knows(tmp_path):
    root = make_project(tmp_path)
    project = detect_dbt_project(root)
    assert project is not None
    assert project.name == "demo"
    assert project.target_path == "target/"
    assert project.quoting == {"database": False, "schema": False, "identifier": False}
    assert project.config_path == root / "stitch.yml"


def test_detect_dbt_project_walks_up_from_a_subdirectory(tmp_path):
    root = make_project(tmp_path)
    nested = root / "models" / "marts"
    nested.mkdir(parents=True)
    project = detect_dbt_project(nested)
    assert project is not None
    assert project.root == root.resolve()


def test_detect_dbt_project_honours_a_custom_target_path(tmp_path):
    root = make_project(tmp_path)
    (root / "dbt_project.yml").write_text("name: demo\ntarget-path: build/artifacts/\n")
    project = detect_dbt_project(root)
    assert project is not None
    assert project.target_path == "build/artifacts/"


def test_detect_dbt_project_returns_none_without_one(tmp_path):
    assert detect_dbt_project(tmp_path) is None


def test_detect_dbt_project_rejects_a_non_mapping(tmp_path):
    (tmp_path / "dbt_project.yml").write_text("- not a mapping\n")
    with pytest.raises(StitchInitError, match="mapping"):
        detect_dbt_project(tmp_path)


# --- manifest derivation --------------------------------------------------------


def test_manifest_facts_derives_the_inventory():
    facts = manifest_facts(manifest())
    assert facts.project_name == "demo"
    assert facts.adapter_type == "snowflake"
    assert facts.model_count == 7
    assert facts.source_count == 2
    assert facts.databases == ["analytics"]
    assert set(facts.schemas) == {"marts", "dims", "staging"}


def test_manifest_facts_excludes_ephemeral_models_from_relations():
    # int_user_flags is ephemeral: it never lands as a table, so counting it against
    # Metabase would report a shortfall nobody can fix
    facts = manifest_facts(manifest())
    assert ("analytics", "staging", "int_user_flags") not in facts.relations
    assert ("analytics", "staging", "stg_users") in facts.relations


def test_manifest_facts_finds_the_mart_schemas_by_model_naming():
    facts = manifest_facts(manifest())
    # fct_/mart_ models live in marts (3), dim_ in dims (1); staging has neither
    assert facts.mart_schemas == ["marts", "dims"]


def test_mart_schemas_fall_back_to_dag_leaves_without_naming_conventions():
    raw = {
        "metadata": {"project_name": "demo"},
        "nodes": {
            "model.demo.a": {
                "resource_type": "model",
                "database": "db",
                "schema": "bronze",
                "name": "a",
                "depends_on": {"nodes": []},
            },
            "model.demo.b": {
                "resource_type": "model",
                "database": "db",
                "schema": "gold",
                "name": "b",
                "depends_on": {"nodes": ["model.demo.a"]},
            },
        },
    }
    assert manifest_facts(raw).mart_schemas == ["gold"]


def test_tables_in_filters_by_database_and_schema():
    facts = manifest_facts(manifest())
    assert facts.tables_in("ANALYTICS", ["marts"]) == {"fct_orders", "mart_payments", "mart_pivot"}
    assert facts.tables_in("other") == set()


# --- database mapping -----------------------------------------------------------


def test_proposal_prefers_the_connection_details_over_the_display_name():
    proposal = propose_database_mapping(
        "analytics",
        [
            {"id": 1, "name": "Sample Database", "is_sample": True},
            {"id": 2, "name": "Warehouse", "details": {"db": "ANALYTICS"}},
        ],
    )
    assert proposal.confident
    assert proposal.metabase_name == "Warehouse"
    assert "ANALYTICS" in proposal.reason


def test_proposal_matches_on_display_name_when_details_are_hidden():
    # a non-admin API key gets no `details`, so name similarity is all there is
    proposal = propose_database_mapping(
        "analytics", [{"id": 2, "name": "Analytics"}, {"id": 3, "name": "Marketing"}]
    )
    assert proposal.confident
    assert proposal.metabase_name == "Analytics"


def test_proposal_is_not_confident_when_two_candidates_are_close():
    proposal = propose_database_mapping(
        "analytics",
        [{"id": 2, "name": "Analytics"}, {"id": 3, "name": "analytics"}],
    )
    assert not proposal.confident
    assert len(proposal.ranked) == 2


def test_proposal_never_prefers_the_sample_database():
    proposal = propose_database_mapping(
        "sample database",
        [{"id": 1, "name": "Sample Database", "is_sample": True}, {"id": 2, "name": "Analytics"}],
    )
    assert proposal.metabase_name == "Analytics"


def test_proposal_with_no_databases_has_nothing_to_propose():
    proposal = propose_database_mapping("analytics", [])
    assert proposal.metabase is None
    assert not proposal.confident


# --- include_schemas + table_prefix ---------------------------------------------


def test_include_schemas_take_the_metabase_spelling_where_both_sides_agree():
    facts = manifest_facts(manifest())
    assert propose_include_schemas(facts, ["MARTS", "raw"]) == ["MARTS", "dims"]


def test_include_schemas_keep_a_mart_schema_metabase_has_not_synced():
    facts = manifest_facts(manifest())
    assert propose_include_schemas(facts, []) == ["marts", "dims"]


def test_table_prefix_is_empty_when_the_names_already_match():
    assert derive_table_prefix(["fct_orders", "dim_users"], ["fct_orders", "dim_users"]) == ""


def test_table_prefix_derived_from_a_dev_target_build():
    dbt_tables = ["sis_fct_orders", "sis_dim_users", "sis_mart_payments"]
    metabase = ["fct_orders", "dim_users", "mart_payments"]
    assert derive_table_prefix(dbt_tables, metabase) == "sis_"


def test_table_prefix_ignores_a_one_off_coincidence():
    assert derive_table_prefix(["fct_orders", "dim_users"], ["orders", "customers"]) == ""


def test_table_prefix_needs_both_sides():
    assert derive_table_prefix([], ["fct_orders"]) == ""
    assert derive_table_prefix(["fct_orders"], []) == ""


# --- rendering ------------------------------------------------------------------


def render(**overrides) -> str:
    kwargs = {
        "project_dir": ".",
        "target_path": "target/",
        "metabase_url": '"https://mb.example.com"',
        "databases": [("Analytics", "analytics", "")],
        "include_schemas": ["marts", "dims"],
        "erd_default_scope": "schema:marts",
    }
    kwargs.update(overrides)
    return render_stitch_yml(**kwargs)


def test_rendered_config_validates_against_todays_config_module(tmp_path):
    path = tmp_path / "stitch.yml"
    path.write_text(render(), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.metabase.url == "https://mb.example.com"
    assert cfg.metabase.include_schemas == ["marts", "dims"]
    assert cfg.metabase.databases[0].metabase_name == "Analytics"
    assert cfg.metabase.databases[0].dbt_database == "analytics"
    assert cfg.serve.erd_default_scope == "schema:marts"
    assert cfg.dbt.target_path == "target/"
    assert cfg.output.dir == ".stitch/"


def test_rendered_config_writes_every_derived_value_explicitly():
    text = render()
    # SPEC 6.0: the file is the full inspectable truth, not a pile of invisible defaults
    for key in (
        "project_dir:",
        "target_path:",
        "auto_docs:",
        "min_version:",
        "table_prefix:",
        "include_schemas:",
        "exclude_collections:",
        "write_to:",
        "fk_meta_keys:",
        "retain_cache_runs:",
    ):
        assert key in text


def test_rendered_config_keeps_the_api_key_an_env_reference():
    assert f"${{{API_KEY_VAR}}}" in render()
    assert API_KEY not in render()


def test_rendered_config_survives_a_database_name_with_punctuation(tmp_path):
    path = tmp_path / "stitch.yml"
    path.write_text(render(databases=[("Analytics: EU", "analytics", "sis_")]), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.metabase.databases[0].metabase_name == "Analytics: EU"
    assert cfg.metabase.databases[0].table_prefix == "sis_"


def test_rendered_config_without_mart_schemas_has_a_null_scope(tmp_path):
    path = tmp_path / "stitch.yml"
    path.write_text(render(include_schemas=[], erd_default_scope=None), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.serve.erd_default_scope is None
    assert cfg.metabase.include_schemas == []


def test_rendered_config_can_reference_the_url_by_env_var(tmp_path, monkeypatch):
    path = tmp_path / "stitch.yml"
    path.write_text(render(metabase_url=f"${{{URL_VAR}}}"), encoding="utf-8")
    monkeypatch.setenv(URL_VAR, "https://mb.example.com")
    assert load_config(path).metabase.url == "https://mb.example.com"


def test_normalize_metabase_url():
    assert normalize_metabase_url(" mb.example.com/ ") == "https://mb.example.com"
    assert normalize_metabase_url("http://localhost:3000/") == "http://localhost:3000"
    with pytest.raises(ValueError, match="required"):
        normalize_metabase_url("   ")


# --- repo files -----------------------------------------------------------------


def test_append_gitignore_adds_the_whole_local_directory(tmp_path):
    assert append_gitignore(tmp_path) is True
    assert ".stitch/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_append_gitignore_is_idempotent(tmp_path):
    append_gitignore(tmp_path)
    before = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert append_gitignore(tmp_path) is False
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == before


def test_append_gitignore_respects_an_existing_entry_without_a_slash(tmp_path):
    (tmp_path / ".gitignore").write_text(".stitch\n", encoding="utf-8")
    assert append_gitignore(tmp_path) is False


def test_append_gitignore_keeps_a_file_without_a_trailing_newline_valid(tmp_path):
    (tmp_path / ".gitignore").write_text("target/", encoding="utf-8")
    append_gitignore(tmp_path)
    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "target/" in lines
    assert ".stitch/" in lines


def test_env_example_records_the_name_and_never_a_value(tmp_path):
    path = write_env_example(tmp_path)
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert f"{API_KEY_VAR}=\n" in text
    assert write_env_example(tmp_path) is None


def test_env_example_appends_to_an_existing_file(tmp_path):
    (tmp_path / ".env.example").write_text("OTHER=1", encoding="utf-8")
    path = write_env_example(tmp_path)
    assert path is not None
    lines = path.read_text(encoding="utf-8").splitlines()
    assert "OTHER=1" in lines
    assert f"{API_KEY_VAR}=" in lines


def test_disarm_workflow_trigger_comments_the_trigger_and_keeps_the_job():
    source = (
        "name: stitch impact\n"
        "\n"
        "on:\n"
        "  pull_request:\n"
        '    paths:\n      - "models/**"\n'
        "\n"
        "jobs:\n"
        "  impact:\n"
        "    runs-on: ubuntu-latest\n"
    )
    disarmed = disarm_workflow_trigger(source)
    assert "# on:\n" in disarmed
    assert "#   pull_request:" in disarmed
    assert "on:\n  workflow_dispatch:\n" in disarmed
    assert "  impact:\n" in disarmed


def test_disarm_workflow_trigger_leaves_the_shipped_template_parseable():
    from ruamel.yaml import YAML

    template = Path(__file__).parent.parent / "action" / "stitch-impact.yml"
    if not template.is_file():  # pragma: no cover -- absent in a wheel install
        pytest.skip("action templates ship only in a source checkout")
    parsed = YAML(typ="safe").load(disarm_workflow_trigger(template.read_text(encoding="utf-8")))
    assert parsed["on"] == {"workflow_dispatch": None}
    assert parsed["name"] == "stitch impact"
    assert "impact" in parsed["jobs"]


# --- the wizard end to end ------------------------------------------------------


def test_run_init_configures_a_repo_with_four_human_inputs(tmp_path):
    root = make_project(tmp_path)
    prompter = happy_prompter()
    result = init(root, prompter)

    assert prompter.inputs == 4
    assert result.healthy
    cfg = load_config(result.config_path)
    assert cfg.metabase.url == "https://mb.example.com"
    assert cfg.metabase.databases[0].metabase_name == "Analytics"
    assert cfg.metabase.databases[0].dbt_database == "analytics"
    assert cfg.metabase.include_schemas == ["marts", "dims"]
    assert cfg.serve.erd_default_scope == "schema:marts"


def test_run_init_writes_the_repo_files_and_never_the_key(tmp_path):
    root = make_project(tmp_path)
    prompter = happy_prompter()
    result = init(root, prompter)

    assert result.gitignore_updated
    assert ".stitch/" in (root / ".gitignore").read_text(encoding="utf-8")
    assert result.env_example_path == root / ".env.example"
    written = "\n".join(
        path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file()
    )
    assert API_KEY not in written
    assert API_KEY not in prompter.output()


def test_run_init_finishes_with_a_mini_doctor_and_the_next_command(tmp_path):
    root = make_project(tmp_path)
    prompter = happy_prompter()
    result = init(root, prompter)

    joined = "\n".join(result.checks)
    assert "ok    stitch.yml parses" in joined
    assert "7 models, 2 sources" in joined
    assert "version v0.53.2" in joined
    # fct_orders is the one dbt mart Metabase also has; dim_customers is not a dbt model
    assert "1/4 dbt models in marts, dims present in Metabase 'Analytics'" in joined
    assert "next: stitch build" in prompter.output()


def test_run_init_reports_a_zero_match_mapping_as_a_failure(tmp_path):
    root = make_project(tmp_path)
    empty = FakeMetabase(metadata={"id": 2, "name": "Analytics", "tables": []})
    result = init(root, happy_prompter(), client_factory=lambda url, key: empty)
    assert not result.healthy
    assert any(line.startswith("fail") for line in result.checks)


def test_run_init_derives_the_table_prefix_from_a_dev_target(tmp_path):
    root = make_project(tmp_path)
    metadata = {
        "id": 2,
        "name": "Analytics",
        "tables": [
            {"schema": "marts", "name": "orders"},
            {"schema": "marts", "name": "payments"},
            {"schema": "marts", "name": "pivot"},
        ],
    }
    # dbt's marts are fct_orders / mart_payments / mart_pivot, so `fct_`/`mart_` cannot
    # be one shared prefix -- only the two mart_ models can agree on one
    result = init(
        root, happy_prompter(), client_factory=lambda url, key: FakeMetabase(metadata=metadata)
    )
    assert load_config(result.config_path).metabase.databases[0].table_prefix == "mart_"


def test_run_init_uses_the_environment_instead_of_asking(tmp_path):
    root = make_project(tmp_path)
    prompter = ScriptedPrompter(confirms=[True, True])
    result = init(
        root,
        prompter,
        env={API_KEY_VAR: API_KEY, URL_VAR: "https://mb.example.com/"},
    )
    assert prompter.inputs == 2
    text = result.config_path.read_text(encoding="utf-8")
    assert f"${{{URL_VAR}}}" in text
    assert f"${{{API_KEY_VAR}}}" in text


def test_run_init_asks_a_real_question_when_the_mapping_is_ambiguous(tmp_path):
    root = make_project(tmp_path)
    databases = [
        {"id": 2, "name": "Analytics"},
        {"id": 3, "name": "analytics"},
    ]
    prompter = ScriptedPrompter(
        answers=["https://mb.example.com"], secrets=[API_KEY], confirms=[True], choices=[1]
    )
    result = init(root, prompter, client_factory=lambda url, key: FakeMetabase(databases=databases))
    assert any("which Metabase database" in question for question in prompter.asked)
    assert load_config(result.config_path).metabase.databases[0].metabase_name == "analytics"


def test_run_init_falls_back_to_the_picker_when_the_proposal_is_declined(tmp_path):
    root = make_project(tmp_path)
    prompter = ScriptedPrompter(
        answers=["https://mb.example.com"],
        secrets=[API_KEY],
        confirms=[False, True],
        choices=[0],
    )
    result = init(root, prompter)
    assert load_config(result.config_path).metabase.databases[0].metabase_name == "Analytics"


def test_run_init_lets_the_user_replace_the_proposed_schemas(tmp_path):
    root = make_project(tmp_path)
    prompter = ScriptedPrompter(
        answers=["https://mb.example.com", " marts , raw "],
        secrets=[API_KEY],
        confirms=[True, False],
    )
    result = init(root, prompter)
    assert load_config(result.config_path).metabase.include_schemas == ["marts", "raw"]


def test_run_init_offers_to_generate_missing_artifacts(tmp_path):
    root = make_project(tmp_path, with_manifest=False)
    generated: list[Path] = []

    def docs_runner(project_dir: Path, args: list[str]) -> None:
        generated.append(project_dir)
        shutil.copy(DBT_FIXTURES / "manifest.json", project_dir / "target" / "manifest.json")

    prompter = ScriptedPrompter(
        answers=["https://mb.example.com"], secrets=[API_KEY], confirms=[True, True, True]
    )
    result = init(root, prompter, docs_runner=docs_runner)
    assert generated == [root.resolve()]
    assert result.healthy


def test_run_init_stops_when_the_user_declines_to_generate_artifacts(tmp_path):
    root = make_project(tmp_path, with_manifest=False)
    prompter = ScriptedPrompter(confirms=[False])
    with pytest.raises(StitchInitError, match="dbt docs generate"):
        init(root, prompter)
    assert not (root / "stitch.yml").exists()


def test_run_init_surfaces_a_dbt_failure(tmp_path):
    root = make_project(tmp_path, with_manifest=False)

    def docs_runner(project_dir: Path, args: list[str]) -> None:
        raise StitchDbtRunnerError("dbt executable not found on PATH")

    with pytest.raises(StitchInitError, match="not found on PATH"):
        init(root, ScriptedPrompter(confirms=[True]), docs_runner=docs_runner)


def test_run_init_needs_a_dbt_project(tmp_path):
    with pytest.raises(StitchInitError, match=r"no dbt_project\.yml"):
        init(tmp_path, ScriptedPrompter())


def test_run_init_keeps_an_existing_config_unless_told_otherwise(tmp_path):
    root = make_project(tmp_path)
    (root / "stitch.yml").write_text("# hand written\n", encoding="utf-8")
    with pytest.raises(StitchInitError, match="left untouched"):
        init(root, ScriptedPrompter(confirms=[False]))
    assert (root / "stitch.yml").read_text(encoding="utf-8") == "# hand written\n"


def test_run_init_force_overwrites_without_asking(tmp_path):
    root = make_project(tmp_path)
    (root / "stitch.yml").write_text("# hand written\n", encoding="utf-8")
    prompter = happy_prompter()
    result = init(root, prompter, force=True)
    assert "hand written" not in result.config_path.read_text(encoding="utf-8")
    assert prompter.inputs == 4


def test_run_init_reports_an_unreachable_metabase_with_the_fix(tmp_path):
    root = make_project(tmp_path)
    unreachable = FakeMetabase(error=MetabaseAPIError("Metabase 0.44 is below the minimum"))
    with pytest.raises(StitchInitError, match="below the minimum"):
        init(root, happy_prompter(), client_factory=lambda url, key: unreachable)
    assert not (root / "stitch.yml").exists()


def test_run_init_requires_an_api_key(tmp_path):
    root = make_project(tmp_path)
    prompter = ScriptedPrompter(answers=["https://mb.example.com"], secrets=["  "])
    with pytest.raises(StitchInitError, match="API key is required"):
        init(root, prompter)


def test_run_init_refuses_a_manifest_with_no_models(tmp_path):
    root = make_project(tmp_path)
    (root / "target" / "manifest.json").write_text(
        json.dumps({"metadata": {"project_name": "demo"}, "nodes": {}, "sources": {}}),
        encoding="utf-8",
    )
    with pytest.raises(StitchInitError, match="no models"):
        init(root, ScriptedPrompter())


def test_run_init_surfaces_an_unparseable_manifest(tmp_path):
    root = make_project(tmp_path)
    (root / "target" / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StitchInitError, match="not valid JSON"):
        init(root, ScriptedPrompter())


def test_run_init_config_is_loadable_without_the_api_key_in_the_environment(tmp_path):
    root = make_project(tmp_path)
    result = init(root, happy_prompter())
    cfg = load_config(result.config_path)
    # the reference is kept and the missing var is recorded; only commands that call the
    # API fail, which is what makes `stitch search` work straight after init
    assert cfg.metabase.missing_env == [API_KEY_VAR]
    with pytest.raises(StitchConfigError, match=API_KEY_VAR):
        cfg.metabase.require_env()
