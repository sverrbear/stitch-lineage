"""`stitch init`: a wizard, not a scaffolder (SPEC.md section 6.0).

Everything dbt already knows is derived from `dbt_project.yml` and the manifest --
project name, target path, databases, schemas, model inventory, identifier quoting --
and never asked. What is left is genuinely unknowable: the Metabase URL, the API key,
and (when the evidence is ambiguous) which Metabase database is which dbt database.

The API key is env-only from the first second: the wizard holds it in memory for the
one call it makes, writes the ${STITCH_METABASE_API_KEY} reference into stitch.yml and
a name-only line into .env.example, and never the value.

Terminal IO goes through the Prompter seam and Metabase through a client factory, so
every step is exercisable non-interactively from the tests.
"""

import difflib
import json
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import typer
from rich.console import Console
from ruamel.yaml import YAML

from stitch_lineage.config import StitchConfigError, load_config
from stitch_lineage.io.artifacts import StitchArtifactError, load_manifest
from stitch_lineage.io.dbt_runner import StitchDbtRunnerError, run_docs_generate
from stitch_lineage.io.metabase_client import MetabaseAPIError, MetabaseClient

API_KEY_VAR = "STITCH_METABASE_API_KEY"
URL_VAR = "STITCH_METABASE_URL"
GITIGNORE_ENTRY = ".stitch/"
CONFIG_FILENAME = "stitch.yml"
ENV_EXAMPLE_FILENAME = ".env.example"
WORKFLOW_TEMPLATE = "stitch-impact.yml"

# dbt's near-universal naming conventions for the models BI actually consumes; the
# schemas holding them are what `include_schemas` should keep.
_MART_MODEL_PREFIXES = ("dim_", "fct_", "fact_", "mart_", "rpt_", "agg_", "obt_")
_MART_SCHEMA_WORDS = ("mart", "core", "dim", "fact", "report", "analytic", "presentation")
_SCHEME = re.compile(r"^https?://", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]")

# above this the top database candidate is proposed for a one-keystroke confirm; a
# runner-up within _AMBIGUOUS of it makes it a real question instead
_CONFIDENT_SCORE = 0.9
_AMBIGUOUS = 0.15


class StitchInitError(Exception):
    """The wizard cannot continue; the message names what the user has to do."""


class Prompter(Protocol):
    """Terminal IO seam -- the tests substitute a scripted implementation."""

    def say(self, message: str) -> None: ...

    def ask(self, question: str, default: str | None = None) -> str: ...

    def secret(self, question: str) -> str: ...

    def confirm(self, question: str, default: bool = True) -> bool: ...

    def choose(self, question: str, options: Sequence[str]) -> int: ...


class MetabaseLike(Protocol):
    """The slice of MetabaseClient the wizard uses."""

    def assert_version(self) -> str: ...

    def list_databases(self) -> list[dict[str, Any]]: ...

    def database_metadata(self, db_id: int) -> dict[str, Any]: ...


class ConsolePrompter:
    """Prompter backed by rich + typer -- the only place `stitch init` touches a terminal."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def say(self, message: str) -> None:
        self.console.print(message, soft_wrap=True)

    def ask(self, question: str, default: str | None = None) -> str:
        if default is None:
            return str(typer.prompt(question))
        return str(typer.prompt(question, default=default))

    def secret(self, question: str) -> str:
        return str(typer.prompt(question, hide_input=True))

    def confirm(self, question: str, default: bool = True) -> bool:
        return bool(typer.confirm(question, default=default))

    def choose(self, question: str, options: Sequence[str]) -> int:
        for index, option in enumerate(options, start=1):
            self.console.print(f"  {index}. {option}", soft_wrap=True)
        while True:
            raw = self.ask(f"{question} [1-{len(options)}]").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            self.console.print("  enter a number from the list")


@dataclass(frozen=True)
class DbtProject:
    """What dbt_project.yml says, before the manifest is read."""

    root: Path
    name: str
    target_path: str
    quoting: dict[str, Any] = field(default_factory=dict)

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def manifest_dir(self) -> Path:
        return self.root / self.target_path


@dataclass(frozen=True)
class ManifestFacts:
    """Everything the manifest answers, so the wizard never asks it."""

    project_name: str
    adapter_type: str | None
    model_count: int
    source_count: int
    databases: list[str]
    schemas: list[str]
    mart_schemas: list[str]
    # (database, schema, table) for every model that lands as a warehouse relation
    relations: list[tuple[str, str, str]]

    def tables_in(self, database: str, schemas: Sequence[str] = ()) -> set[str]:
        wanted = {schema.casefold() for schema in schemas}
        return {
            table
            for db, schema, table in self.relations
            if db.casefold() == database.casefold() and (not wanted or schema.casefold() in wanted)
        }


@dataclass(frozen=True)
class DatabaseProposal:
    """The wizard's answer to 'which Metabase database is this dbt database?'."""

    dbt_database: str
    metabase: dict[str, Any] | None
    score: float
    reason: str
    confident: bool
    ranked: list[dict[str, Any]]

    @property
    def metabase_name(self) -> str:
        return str((self.metabase or {}).get("name", ""))


@dataclass
class InitResult:
    config_path: Path
    gitignore_updated: bool
    env_example_path: Path | None
    workflow_path: Path | None
    checks: list[str]
    healthy: bool


def detect_dbt_project(start: Path) -> DbtProject | None:
    """Find dbt_project.yml at `start` or above it, and read what it answers.

    Walking up means `stitch init` works from anywhere inside the project; stitch.yml
    still lands at the project root, where SPEC.md section 6.1 puts it.
    """
    yaml = YAML(typ="safe")
    for directory in (start.resolve(), *start.resolve().parents):
        candidate = directory / "dbt_project.yml"
        if not candidate.is_file():
            continue
        try:
            raw = yaml.load(candidate.read_text(encoding="utf-8"))
        except Exception as exc:  # any ruamel failure tells the same story
            raise StitchInitError(f"could not parse {candidate}: {exc}") from exc
        if not isinstance(raw, dict):
            raise StitchInitError(f"{candidate} does not contain a YAML mapping")
        target = str(raw.get("target-path") or "target").strip().rstrip("/") or "target"
        quoting = raw.get("quoting")
        return DbtProject(
            root=directory,
            name=str(raw.get("name") or directory.name),
            target_path=f"{target}/",
            quoting=quoting if isinstance(quoting, dict) else {},
        )
    return None


def manifest_facts(manifest: Mapping[str, Any]) -> ManifestFacts:
    """Derive the model inventory: databases, schemas, mart schemas, relations.

    Ephemeral models are excluded from `relations` -- they are compiled into their
    parents and never exist as warehouse tables, so counting them against Metabase
    would report a permanent, unfixable shortfall.
    """
    nodes = manifest.get("nodes") or {}
    models = {
        uid: node
        for uid, node in nodes.items()
        if isinstance(node, dict) and node.get("resource_type") == "model"
    }
    depended_on = {
        dep
        for node in models.values()
        for dep in ((node.get("depends_on") or {}).get("nodes") or [])
    }
    relations: list[tuple[str, str, str]] = []
    for node in models.values():
        if (node.get("config") or {}).get("materialized") == "ephemeral":
            continue
        database, schema = node.get("database"), node.get("schema")
        table = node.get("alias") or node.get("name")
        if database and schema and table:
            relations.append((str(database), str(schema), str(table)))

    metadata = manifest.get("metadata") or {}
    sources = manifest.get("sources") or {}
    return ManifestFacts(
        project_name=str(metadata.get("project_name") or ""),
        adapter_type=metadata.get("adapter_type"),
        model_count=len(models),
        source_count=len(sources),
        databases=_ranked(Counter(database for database, _, _ in relations)),
        schemas=_ranked(Counter(schema for _, schema, _ in relations)),
        mart_schemas=_mart_schemas(models, depended_on),
        relations=relations,
    )


def _ranked(counts: Counter[str]) -> list[str]:
    """Most models first, ties broken by name so two runs agree."""
    return [name for name, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _mart_schemas(models: Mapping[str, dict[str, Any]], depended_on: set[str]) -> list[str]:
    """Where the models BI consumes actually live, strongest evidence first.

    Model naming (`fct_`, `dim_`, `mart_`, ...) is the most reliable signal in a dbt
    project, then schema naming, then DAG position: a model nothing else references is
    a leaf, and leaves are what dashboards query. Anything else -- every schema.
    """
    by_model_name: Counter[str] = Counter()
    by_schema_name: Counter[str] = Counter()
    leaves: Counter[str] = Counter()
    everything: Counter[str] = Counter()
    for uid, node in models.items():
        schema = node.get("schema")
        if not schema:
            continue
        schema = str(schema)
        everything[schema] += 1
        name = str(node.get("alias") or node.get("name") or "").casefold()
        if name.startswith(_MART_MODEL_PREFIXES):
            by_model_name[schema] += 1
        if any(word in schema.casefold() for word in _MART_SCHEMA_WORDS):
            by_schema_name[schema] += 1
        if uid not in depended_on:
            leaves[schema] += 1
    for counts in (by_model_name, by_schema_name, leaves, everything):
        if counts:
            return _ranked(counts)
    return []


def normalize_metabase_url(raw: str) -> str:
    """Trim, default the scheme to https, drop the trailing slash."""
    url = raw.strip().rstrip("/")
    if not url:
        raise ValueError("a Metabase URL is required")
    if not _SCHEME.match(url):
        url = f"https://{url}"
    return url


def _normalize(name: str) -> str:
    return _NON_ALNUM.sub("", name.casefold())


def _connection_database(database: Mapping[str, Any]) -> str:
    details = database.get("details")
    if not isinstance(details, dict):
        return ""
    return str(details.get("db") or details.get("dbname") or "")


def _score_database(dbt_database: str, database: Mapping[str, Any]) -> tuple[float, str]:
    """How likely this Metabase database is the dbt one, and why in one clause."""
    name = str(database.get("name") or "")
    connected = _connection_database(database)
    if connected and connected.casefold() == dbt_database.casefold():
        score, reason = 1.0, f"its connection points at {connected}"
    elif name.casefold() == dbt_database.casefold():
        score, reason = 0.95, "the display name matches exactly"
    elif _normalize(name) and _normalize(name) == _normalize(dbt_database):
        score, reason = 0.92, "the display name matches apart from case"
    else:
        ratio = difflib.SequenceMatcher(None, _normalize(name), _normalize(dbt_database)).ratio()
        score, reason = ratio * 0.85, f"the display name is {ratio:.0%} similar"
    if database.get("is_sample"):
        # Metabase's bundled demo database outscoring a real warehouse would be absurd
        score *= 0.1
        reason = "Metabase's built-in sample database"
    return round(score, 4), reason


def propose_database_mapping(
    dbt_database: str, metabase_databases: Sequence[Mapping[str, Any]]
) -> DatabaseProposal:
    """Rank the Metabase databases against one dbt database.

    `confident` means the top candidate is strong AND alone: a close runner-up is real
    ambiguity, which SPEC.md section 6.0 says to turn into a real question rather than a
    confirm the user will reflexively accept.
    """
    scored = [
        (*_score_database(dbt_database, database), database)
        for database in metabase_databases
        if isinstance(database, dict)
    ]
    scored.sort(key=lambda item: (-item[0], str(item[2].get("name") or "")))
    if not scored:
        return DatabaseProposal(dbt_database, None, 0.0, "no databases", False, [])
    top_score, reason, top = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    confident = top_score >= _CONFIDENT_SCORE and top_score - runner_up >= _AMBIGUOUS
    return DatabaseProposal(
        dbt_database=dbt_database,
        metabase=dict(top),
        score=top_score,
        reason=reason,
        confident=confident,
        ranked=[dict(database) for _, _, database in scored],
    )


def metabase_tables(metadata: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(schema, table) for every table in a /api/database/:id/metadata payload."""
    tables = metadata.get("tables")
    return [
        (str(table.get("schema") or ""), str(table.get("name") or ""))
        for table in (tables if isinstance(tables, list) else [])
        if isinstance(table, dict) and table.get("name")
    ]


def propose_include_schemas(facts: ManifestFacts, available: Sequence[str] = ()) -> list[str]:
    """The mart schemas, spelled the way Metabase spells them where both sides agree.

    Metabase's spelling wins for readability only -- resolve_metabase compares
    case-insensitively. A mart schema Metabase has not synced yet is still proposed:
    dropping it silently would quietly exclude the models the user cares about most.
    """
    by_fold = {schema.casefold(): schema for schema in available}
    proposed = [by_fold.get(schema.casefold(), schema) for schema in facts.mart_schemas]
    return list(dict.fromkeys(proposed))


def derive_table_prefix(dbt_tables: Iterable[str], metabase_tables_: Iterable[str]) -> str:
    """The prefix dbt's physical names carry that the BI database's do not, or "".

    A dev-target build writes `sis_fct_orders` while Metabase points at prod's
    `fct_orders`; config's table_prefix bridges that. It is derivable rather than
    askable: whatever consistently sits in front of a name Metabase does have, is it.
    """
    dbt = {table.casefold() for table in dbt_tables if table}
    metabase = {table.casefold() for table in metabase_tables_ if table}
    if not dbt or not metabase:
        return ""
    unmatched = dbt - metabase
    if len(unmatched) * 2 < len(dbt):
        # most names already match: whatever is left is a genuine difference, not a prefix
        return ""
    candidates: Counter[str] = Counter()
    for table in unmatched:
        for other in metabase:
            if len(table) > len(other) and table.endswith(other):
                candidates[table[: -len(other)]] += 1
    if not candidates:
        return ""
    prefix, hits = max(candidates.items(), key=lambda item: (item[1], -len(item[0])))
    return prefix if hits * 2 >= len(unmatched) and hits >= 2 else ""


def render_stitch_yml(
    *,
    project_dir: str,
    target_path: str,
    metabase_url: str,
    databases: Sequence[tuple[str, str, str]],
    include_schemas: Sequence[str],
    erd_default_scope: str | None,
) -> str:
    """Render stitch.yml with every derived value explicit (SPEC.md section 6.0).

    Nothing is left to a default the file does not show: the config stays the full,
    inspectable truth about what this repo resolves. Scalars go through json.dumps --
    JSON strings are valid YAML, so a database called `Analytics: EU` survives.
    """
    lines = [
        "# stitch.yml -- dbt <-> Metabase column lineage.",
        "# Written by `stitch init`. Every value was derived from your dbt manifest and your",
        "# Metabase instance and is spelled out here rather than defaulted invisibly.",
        "",
        "dbt:",
        f"  project_dir: {json.dumps(project_dir)}",
        f"  target_path: {json.dumps(target_path)}",
        "  # run `dbt docs generate` at the start of every build (--docs/--no-docs overrides)",
        "  auto_docs: false",
        "  docs_args: []",
        "",
        "metabase:",
        f"  url: {metabase_url}",
        f"  api_key: ${{{API_KEY_VAR}}}  # env reference only -- a literal key here is an error",
        '  min_version: "0.49"  # API-key auth floor, asserted at startup',
        "  databases:",
    ]
    for metabase_name, dbt_database, table_prefix in databases:
        lines += [
            f"    - metabase_name: {json.dumps(metabase_name)}  # display name in Metabase",
            f"      dbt_database: {json.dumps(dbt_database)}  # database per the dbt manifest",
            f"      table_prefix: {json.dumps(table_prefix)}  # stripped from dbt table names "
            "before matching",
        ]
    lines += [
        f"  include_schemas: {_inline_list(include_schemas)}"
        "  # Metabase schemas to ingest; [] means all",
        '  exclude_collections: []  # e.g. ["Personal*", "Archive*"]',
        "",
        "relationships:",
        "  write_to: meta  # | relationships_test | contract_constraint",
        "  fk_meta_keys: "
        '["metabase.fk_target_table", "metabase.fk_target_field"]  # dbt-metabase interop',
        "  cardinality_meta_key: relationship_type  # dbterd interop",
        "  validated_test_severity: warn",
        "  test_argument_style: auto  # | flat | arguments (dbt 1.10+ nesting)",
        "",
        "write:",
        '  strip_model_prefixes: []  # e.g. ["viz_"]: write onto the model under the view',
        "",
        "serve:",
    ]
    if erd_default_scope:
        lines.append(
            f"  erd_default_scope: {json.dumps(erd_default_scope)}"
            "  # ERD scope the app opens on; 'tag:<name>' works too"
        )
    else:
        lines.append('  erd_default_scope: null  # e.g. "schema:marts" or "tag:core"')
    lines += [
        '  strip_model_prefixes: []  # display-only, e.g. ["viz_"]',
        "",
        "output:",
        "  dir: .stitch/",
        "  retain_cache_runs: 3  # raw Metabase payload snapshots kept under .stitch/cache/",
        "",
    ]
    return "\n".join(lines)


def _inline_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


def append_gitignore(root: Path, entry: str = GITIGNORE_ENTRY) -> bool:
    """Add `entry` to .gitignore unless it is already covered. True when the file changed."""
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    covered = {line.strip().rstrip("/") for line in existing.splitlines()}
    if entry.strip().rstrip("/") in covered:
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    block = f"{prefix}\n# stitch: the graph and its caches are local artifacts\n{entry}\n"
    path.write_text(existing + block, encoding="utf-8")
    return True


def write_env_example(root: Path, variables: Sequence[str] = (API_KEY_VAR,)) -> Path | None:
    """Record the env var NAMES in .env.example. Never a value -- that is the whole point."""
    path = root / ENV_EXAMPLE_FILENAME
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    named = {line.split("=", 1)[0].strip() for line in existing.splitlines()}
    missing = [variable for variable in variables if variable not in named]
    if not missing:
        return None
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    body = "".join(f"{variable}=\n" for variable in missing)
    path.write_text(
        f"{existing}{prefix}# stitch: create a key in Metabase under "
        f"Admin settings -> Authentication -> API keys\n{body}",
        encoding="utf-8",
    )
    return path


def disarm_workflow_trigger(text: str) -> str:
    """Comment out a workflow's `on:` block, leaving a manual trigger in its place.

    SPEC.md section 6.0 wants the Action dropped in with its trigger commented out. A
    workflow with no `on:` at all is a GitHub Actions error, so `workflow_dispatch`
    stands in until the user un-comments the real trigger.
    """
    lines = text.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("on:"):
            out.append(line)
            index += 1
            continue
        out += [
            "# `stitch init` installed this workflow with its trigger commented out.",
            "# Un-comment the block below and drop the workflow_dispatch line to arm it.",
            f"# {line}",
        ]
        index += 1
        while index < len(lines) and (
            not lines[index].strip() or lines[index].startswith((" ", "\t", "#"))
        ):
            out.append(f"# {lines[index]}" if lines[index].strip() else "#")
            index += 1
        out += ["on:", "  workflow_dispatch:", ""]
    return "\n".join(out) + "\n"


def _template_dir() -> Path | None:
    """The repo's action/ templates, present in a source checkout and absent in a wheel."""
    candidate = Path(__file__).resolve().parent.parent / "action"
    return candidate if candidate.is_dir() else None


def install_workflow_template(root: Path) -> Path | None:
    """Copy the impact workflow into .github/workflows/, disarmed. None when unavailable."""
    template_dir = _template_dir()
    if template_dir is None:
        return None
    template = template_dir / WORKFLOW_TEMPLATE
    if not template.is_file():
        return None
    destination = root / ".github" / "workflows" / WORKFLOW_TEMPLATE
    if destination.exists():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        disarm_workflow_trigger(template.read_text(encoding="utf-8")), encoding="utf-8"
    )
    return destination


def _default_client(url: str, api_key: str) -> MetabaseLike:
    return MetabaseClient(url, api_key)


def _quoting_note(project: DbtProject, facts: ManifestFacts) -> str:
    """Identifier quoting is dbt's answer, not a question -- SPEC.md section 6.1."""
    adapter = facts.adapter_type or "your adapter"
    if project.quoting:
        settings = ", ".join(f"{key}={value}" for key, value in sorted(project.quoting.items()))
        return f"identifier quoting from dbt_project.yml ({settings}) -- not configurable"
    return f"identifier quoting left to {adapter}'s default -- not configurable"


def _ensure_manifest(project: DbtProject, prompter: Prompter, docs_runner: Any) -> dict[str, Any]:
    """Load the manifest, offering to produce it when it is not there yet."""
    manifest_path = project.manifest_dir / "manifest.json"
    if not manifest_path.is_file():
        prompter.say(f"no manifest at {manifest_path}")
        if not prompter.confirm("run 'dbt docs generate' now?", default=True):
            raise StitchInitError(
                "stitch init needs target/manifest.json to derive your databases, schemas "
                "and models -- run 'dbt docs generate', then re-run 'stitch init'"
            )
        try:
            docs_runner(project.root, [])
        except StitchDbtRunnerError as exc:
            raise StitchInitError(str(exc)) from exc
    try:
        return load_manifest(project.manifest_dir)
    except StitchArtifactError as exc:
        raise StitchInitError(str(exc)) from exc


def _describe(database: Mapping[str, Any]) -> str:
    connected = _connection_database(database)
    engine = str(database.get("engine") or "?")
    return f"{database.get('name')} ({engine}{f' -> {connected}' if connected else ''})"


def _resolve_database(
    prompter: Prompter, dbt_database: str, databases: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """One keystroke in the common case, a real question when the evidence is ambiguous."""
    proposal = propose_database_mapping(dbt_database, databases)
    if proposal.metabase is None:
        raise StitchInitError(
            "the API key sees no databases in Metabase -- check that the key's user has "
            "access to your warehouse connection"
        )
    if proposal.confident:
        question = (
            f"Metabase '{proposal.metabase_name}' <-> dbt '{dbt_database}' "
            f"({proposal.reason}) -- confirm?"
        )
        if prompter.confirm(question, default=True):
            return proposal.metabase
    else:
        prompter.say(f"more than one Metabase database could be dbt '{dbt_database}':")
    index = prompter.choose(
        f"which Metabase database is dbt '{dbt_database}'?",
        [_describe(database) for database in proposal.ranked],
    )
    return proposal.ranked[index]


def _resolve_include_schemas(
    prompter: Prompter, facts: ManifestFacts, available: Sequence[str]
) -> list[str]:
    proposed = propose_include_schemas(facts, available)
    if not proposed:
        return []
    listed = ", ".join(proposed)
    if prompter.confirm(f"ingest Metabase schemas {listed}? (your marts live there)", default=True):
        return proposed
    answer = prompter.ask("schemas to ingest, comma-separated (empty for all)", default="").strip()
    return [schema.strip() for schema in answer.split(",") if schema.strip()]


def _mini_doctor(
    *,
    config_path: Path,
    facts: ManifestFacts,
    version: str,
    metabase_name: str,
    dbt_database: str,
    include_schemas: Sequence[str],
    tables: Sequence[tuple[str, str]],
    table_prefix: str,
) -> tuple[list[str], bool]:
    """Reachable, supported, parseable, and counted on both sides (SPEC.md section 6.0)."""
    checks: list[str] = []
    healthy = True
    try:
        load_config(config_path)
        checks.append(f"ok    {config_path.name} parses")
    except StitchConfigError as exc:
        healthy = False
        checks.append(f"fail  {config_path.name}: {exc}")
    checks.append(
        f"ok    manifest parses -- {facts.model_count} models, {facts.source_count} sources"
    )
    checks.append(f"ok    Metabase reachable, version {version} (minimum 0.49)")

    wanted = {schema.casefold() for schema in include_schemas}
    in_scope = {
        table.casefold() for schema, table in tables if not wanted or schema.casefold() in wanted
    }
    dbt_tables = facts.tables_in(dbt_database, include_schemas)
    matched = {
        table for table in dbt_tables if _strip_prefix(table.casefold(), table_prefix) in in_scope
    }
    scope = f" in {', '.join(include_schemas)}" if include_schemas else ""
    if dbt_tables and not matched:
        healthy = False
        checks.append(
            f"fail  none of the {len(dbt_tables)} dbt models{scope} match a table in "
            f"Metabase '{metabase_name}' -- check metabase.databases and include_schemas"
        )
    else:
        checks.append(
            f"ok    {len(matched)}/{len(dbt_tables)} dbt models{scope} present in "
            f"Metabase '{metabase_name}' ({len(in_scope)} tables there)"
        )
    return checks, healthy


def _strip_prefix(table: str, prefix: str) -> str:
    prefix = prefix.casefold()
    return table[len(prefix) :] if prefix and table.startswith(prefix) else table


def run_init(
    *,
    start_dir: Path,
    prompter: Prompter,
    client_factory: Callable[[str, str], MetabaseLike] = _default_client,
    docs_runner: Callable[[Path, list[str]], None] = run_docs_generate,
    env: Mapping[str, str] | None = None,
    force: bool = False,
) -> InitResult:
    """Run the whole wizard and return what it wrote.

    Raises:
        StitchInitError: no dbt project, no manifest and no permission to build one,
            an existing stitch.yml the user declined to overwrite, or Metabase
            unreachable -- always with the next action in the message.
    """
    env = os.environ if env is None else env
    project = detect_dbt_project(start_dir)
    if project is None:
        raise StitchInitError(
            f"no dbt_project.yml in {start_dir.resolve()} or any parent directory -- "
            "run 'stitch init' from inside your dbt project"
        )
    prompter.say(
        f"dbt project '{project.name}' at {project.root} (artifacts: {project.target_path})"
    )

    config_path = project.config_path
    if (
        config_path.is_file()
        and not force
        and not prompter.confirm(f"{config_path} already exists -- overwrite it?", default=False)
    ):
        raise StitchInitError(f"{config_path} left untouched")

    manifest = _ensure_manifest(project, prompter, docs_runner)
    facts = manifest_facts(manifest)
    if not facts.databases:
        raise StitchInitError(
            f"{project.manifest_dir / 'manifest.json'} has no models -- run 'dbt docs generate' "
            "on a project with models, then re-run 'stitch init'"
        )
    prompter.say(
        f"derived from the manifest: {facts.model_count} models, {facts.source_count} sources, "
        f"database {facts.databases[0]}, schemas {', '.join(facts.schemas)}"
    )
    prompter.say(_quoting_note(project, facts))

    url_from_env = env.get(URL_VAR)
    if url_from_env:
        url = normalize_metabase_url(url_from_env)
        config_url = f"${{{URL_VAR}}}"
        prompter.say(f"using {URL_VAR} from the environment ({url})")
    else:
        url = _ask_url(prompter)
        config_url = json.dumps(url)

    api_key = env.get(API_KEY_VAR)
    if api_key:
        prompter.say(f"using {API_KEY_VAR} from the environment (never written to stitch.yml)")
    else:
        api_key = prompter.secret(f"Metabase API key (stored as ${{{API_KEY_VAR}}}, never here)")
    if not api_key.strip():
        raise StitchInitError(
            "a Metabase API key is required -- create one under Admin settings -> "
            "Authentication -> API keys"
        )

    client = client_factory(url, api_key.strip())
    try:
        version = client.assert_version()
        databases = client.list_databases()
    except MetabaseAPIError as exc:
        raise StitchInitError(f"{exc} -- check the URL and the API key, then re-run") from exc

    dbt_database = facts.databases[0]
    chosen = _resolve_database(prompter, dbt_database, databases)
    db_id = chosen.get("id")
    try:
        tables = metabase_tables(client.database_metadata(db_id)) if db_id is not None else []
    except MetabaseAPIError as exc:
        raise StitchInitError(str(exc)) from exc

    include_schemas = _resolve_include_schemas(
        prompter, facts, sorted({schema for schema, _ in tables if schema})
    )
    table_prefix = derive_table_prefix(
        facts.tables_in(dbt_database, include_schemas), [table for _, table in tables]
    )
    if table_prefix:
        prompter.say(
            f"dbt table names carry a '{table_prefix}' prefix Metabase does not -- "
            "recording it as table_prefix so binding still matches"
        )

    config_path.write_text(
        render_stitch_yml(
            project_dir=".",
            target_path=project.target_path,
            metabase_url=config_url,
            databases=[(str(chosen.get("name") or ""), dbt_database, table_prefix)],
            include_schemas=include_schemas,
            erd_default_scope=f"schema:{include_schemas[0]}" if include_schemas else None,
        ),
        encoding="utf-8",
    )
    prompter.say(f"wrote {config_path}")

    gitignore_updated = append_gitignore(project.root)
    if gitignore_updated:
        prompter.say(f"added {GITIGNORE_ENTRY} to .gitignore")
    env_example = write_env_example(project.root)
    if env_example is not None:
        prompter.say(f"wrote {env_example} ({API_KEY_VAR}, name only)")
    workflow = install_workflow_template(project.root)
    if workflow is not None:
        prompter.say(f"wrote {workflow} (trigger commented out)")

    checks, healthy = _mini_doctor(
        config_path=config_path,
        facts=facts,
        version=version,
        metabase_name=str(chosen.get("name") or ""),
        dbt_database=dbt_database,
        include_schemas=include_schemas,
        tables=tables,
        table_prefix=table_prefix,
    )
    for check in checks:
        prompter.say(check)
    if not env.get(API_KEY_VAR):
        prompter.say(f"export {API_KEY_VAR}=<your key>   # stitch reads the key from the env")
    prompter.say("next: stitch build")
    return InitResult(
        config_path=config_path,
        gitignore_updated=gitignore_updated,
        env_example_path=env_example,
        workflow_path=workflow,
        checks=checks,
        healthy=healthy,
    )


def _ask_url(prompter: Prompter) -> str:
    while True:
        try:
            return normalize_metabase_url(prompter.ask("Metabase URL"))
        except ValueError as exc:
            prompter.say(f"  {exc}")
