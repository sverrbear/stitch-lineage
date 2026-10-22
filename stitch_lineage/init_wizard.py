"""`stitch init`: a wizard, not a scaffolder (SPEC.md section 6.0).

Everything dbt already knows is derived from `dbt_project.yml` and the manifest --
project name, target path, databases, schemas, model inventory, identifier quoting --
and never asked. What is left is genuinely unknowable: the Metabase URL, the API key,
and (when the evidence is ambiguous) which Metabase database is which dbt database.

The API key is remembered in the operating system credential store, with environment
variables taking precedence for CI. Project files contain only a reference. Setup
resumes existing configuration and carries the credential through the first build.

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
from urllib.parse import urlsplit

import typer
from rich.console import Console
from ruamel.yaml import YAML

from stitch_lineage.config import RelationshipsConfig, StitchConfigError, load_config
from stitch_lineage.io import credentials
from stitch_lineage.io.artifacts import StitchArtifactError, load_catalog, load_manifest
from stitch_lineage.io.dbt_runner import StitchDbtRunnerError, run_docs_generate
from stitch_lineage.io.metabase_client import MetabaseAPIError, MetabaseClient
from stitch_lineage.resolve.dbt import compiled_model_counts

API_KEY_VAR = "STITCH_METABASE_API_KEY"
URL_VAR = "STITCH_METABASE_URL"
GITIGNORE_ENTRY = ".stitch/"
CONFIG_FILENAME = "stitch.yml"
ENV_EXAMPLE_FILENAME = ".env.example"

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
    checks: list[str]
    healthy: bool
    credentials: dict[str, str] = field(default_factory=dict, repr=False)


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
    if "://" not in url:
        url = f"https://{url}"
    parts = urlsplit(url)
    if (
        not _SCHEME.match(url)
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or any(char.isspace() for char in url)
    ):
        raise ValueError(
            "enter an http(s) Metabase base URL without credentials, query or fragment"
        )
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
        f"  write_to: {RelationshipsConfig().write_to}"
        "  # meta | relationships_test | contract_constraint",
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


def _default_client(url: str, api_key: str) -> MetabaseLike:
    return MetabaseClient(url, api_key)


def _ensure_artifacts(
    project: DbtProject,
    prompter: Prompter,
    docs_runner: Any,
    *,
    docs_args: list[str] | None = None,
    require_catalog: bool = True,
) -> dict[str, Any]:
    """Reuse usable artifacts; otherwise guide generation in the existing dbt environment."""

    def read() -> dict[str, Any]:
        manifest = load_manifest(project.manifest_dir)
        if require_catalog:
            load_catalog(project.manifest_dir)
            compiled, uncompiled = compiled_model_counts(manifest)
            if uncompiled and not compiled:
                raise StitchArtifactError("the manifest has no compiled model SQL")
        return manifest

    try:
        return read()
    except StitchArtifactError as exc:
        problem = str(exc)
        prompter.say(problem)
    prompter.say("dbt will use your existing profile and may ask you to sign in to your warehouse.")
    if not prompter.confirm("run 'dbt docs generate' now?", default=True):
        raise StitchInitError(
            f"{problem} -- run 'dbt docs generate', then re-run 'stitch init' to continue"
        )
    try:
        docs_runner(project.root, docs_args or [])
        return read()
    except (StitchDbtRunnerError, StitchArtifactError) as exc:
        raise StitchInitError(f"{exc} -- re-run 'stitch init' after fixing dbt") from exc


def _describe(database: Mapping[str, Any]) -> str:
    connected = _connection_database(database)
    engine = str(database.get("engine") or "?")
    return f"{database.get('name')} ({engine}{f' -> {connected}' if connected else ''})"


def _resolve_database(
    prompter: Prompter, dbt_database: str, databases: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Automatic in the common case, a real question when the evidence is ambiguous."""
    proposal = propose_database_mapping(dbt_database, databases)
    if proposal.metabase is None:
        raise StitchInitError(
            "the API key sees no databases in Metabase -- check that the key's user has "
            "access to your warehouse connection"
        )
    if proposal.confident:
        prompter.say(
            f"matched Metabase '{proposal.metabase_name}' <-> dbt '{dbt_database}' "
            f"({proposal.reason})"
        )
        return proposal.metabase
    prompter.say(f"choose the Metabase database for dbt '{dbt_database}':")
    index = prompter.choose(
        f"which Metabase database is dbt '{dbt_database}'?",
        [_describe(database) for database in proposal.ranked],
    )
    return proposal.ranked[index]


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
    env: Mapping[str, str] | None = None,
) -> tuple[list[str], bool]:
    """Reachable, supported, parseable, and counted on both sides (SPEC.md section 6.0)."""
    checks: list[str] = []
    healthy = True
    try:
        load_config(config_path, env=env)
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


def _setup_url(project: DbtProject) -> str | None:
    try:
        raw = json.loads((project.root / ".stitch" / "setup.json").read_text())
        if not isinstance(raw, dict) or not isinstance(raw.get("metabase_url"), str):
            return None
        return normalize_metabase_url(raw["metabase_url"])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _remember_url(project: DbtProject, url: str) -> None:
    # Only the URL goes in this checkpoint. Credentials live in the OS store.
    append_gitignore(project.root)
    directory = project.root / ".stitch"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "setup.json").write_text(json.dumps({"metabase_url": url}) + "\n")


def run_init(
    *,
    start_dir: Path,
    prompter: Prompter,
    client_factory: Callable[[str, str], MetabaseLike] = _default_client,
    docs_runner: Callable[[Path, list[str]], None] = run_docs_generate,
    env: Mapping[str, str] | None = None,
    force: bool = False,
    configure_only: bool = False,
) -> InitResult:
    """Connect and prepare artifacts; the CLI then builds and serves in this session.

    Existing configuration is reused byte-for-byte unless --force was requested.
    A checkpoint keeps the URL across connection failures; validated keys are saved
    before metadata/build so a later failure does not make the user paste them again.
    """
    env = os.environ if env is None else env
    project = detect_dbt_project(start_dir)
    if project is None:
        raise StitchInitError(
            f"no dbt_project.yml in {start_dir.resolve()} or any parent directory -- "
            "run 'stitch init' from inside your dbt project"
        )
    prompter.say(f"found dbt project '{project.name}' at {project.root}")
    config_path = project.config_path
    existing = None
    if config_path.is_file() and not force:
        try:
            existing = load_config(config_path, env=env)
        except StitchConfigError as exc:
            raise StitchInitError(
                f"{exc} -- fix stitch.yml or use 'stitch init --force' to reconfigure"
            ) from exc
        prompter.say("resuming with your existing stitch.yml")

    artifact_project = (
        DbtProject(
            root=(config_path.parent / existing.dbt.project_dir).resolve(),
            name=project.name,
            target_path=existing.dbt.target_path,
            quoting=project.quoting,
        )
        if existing
        else project
    )
    manifest = _ensure_artifacts(
        artifact_project,
        prompter,
        docs_runner,
        docs_args=existing.dbt.docs_args if existing else [],
        require_catalog=not configure_only,
    )
    facts = manifest_facts(manifest)
    if not facts.databases:
        raise StitchInitError(
            "the manifest has no models -- run 'dbt docs generate' on a project with models, "
            "then re-run 'stitch init'"
        )
    prompter.say(f"found {facts.model_count} models and {facts.source_count} sources")

    session_env: dict[str, str] = {}
    configured_url = existing.metabase.url if existing else env.get(URL_VAR)
    if configured_url and "${" not in configured_url:
        try:
            url = normalize_metabase_url(configured_url)
        except ValueError as exc:
            raise StitchInitError(f"invalid Metabase URL: {exc}") from None
    else:
        url = (None if force else _setup_url(project)) or _ask_url(prompter)
        if configured_url:
            match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", configured_url)
            if not match:
                raise StitchInitError("set the environment variables in metabase.url and retry")
            session_env[match.group(1)] = url
    gitignore_updated = append_gitignore(project.root)
    _remember_url(project, url)
    variable = (existing.metabase._api_key_env if existing else None) or API_KEY_VAR
    api_key = env.get(variable)
    from_environment = bool(api_key)
    if not api_key:
        try:
            api_key = credentials.read_api_key(config_path, url, variable)
        except credentials.CredentialStoreUnavailable:
            api_key = None
    if api_key:
        prompter.say("using your Metabase credentials")
    else:
        prompter.say("Create a key in Metabase Admin settings -> Authentication -> API keys.")
        api_key = prompter.secret("Metabase API key (remembered in your OS credential store)")

    # Retry an expired saved key in place. A supplied environment value is explicit
    # (including in CI), so explain its failure rather than silently replacing it.
    while True:
        api_key = api_key.strip()
        if not api_key:
            raise StitchInitError("a Metabase API key is required -- re-run 'stitch init'")
        client = client_factory(url, api_key)
        try:
            version = client.assert_version()
            databases = client.list_databases()
            break
        except MetabaseAPIError as exc:
            if exc.status_code == 401 and not from_environment:
                prompter.say("Metabase rejected that key. Paste a current key to continue.")
                api_key = prompter.secret("Metabase API key")
                continue
            raise StitchInitError(
                f"{str(exc).replace(api_key, '[redacted]')} -- check the connection and "
                "re-run 'stitch init' to continue"
            ) from None
    prompter.say("connected to Metabase")
    if not from_environment:
        try:
            credentials.save_api_key(config_path, url, variable, api_key)
            prompter.say("API key remembered in your OS credential store")
        except credentials.CredentialStoreUnavailable:
            prompter.say(
                f"OS credential storage is unavailable; continuing for this session. "
                f"Next time, run 'stitch init' to reconnect or set {variable} in your environment."
            )
    session_env[variable] = api_key

    mappings = []
    metadata = []
    if existing:
        for mapping in existing.metabase.databases:
            matches = [db for db in databases if db.get("name") == mapping.metabase_name]
            if len(matches) != 1:
                raise StitchInitError(
                    f"Metabase database '{mapping.metabase_name}' is unavailable or ambiguous -- "
                    "check the key's permissions or use 'stitch init --force' to reconfigure"
                )
            mappings.append((mapping.metabase_name, mapping.dbt_database, mapping.table_prefix))
            metadata.append(matches[0])
    else:
        for database in facts.databases:
            chosen = _resolve_database(prompter, database, databases)
            mappings.append((str(chosen.get("name") or ""), database, ""))
            metadata.append(chosen)

    table_sets = []
    try:
        for chosen in metadata:
            table_sets.append(metabase_tables(client.database_metadata(chosen["id"])))
    except (MetabaseAPIError, KeyError) as exc:
        raise StitchInitError(
            f"could not read Metabase database metadata: {str(exc).replace(api_key, '[redacted]')} "
            "-- re-run 'stitch init' to continue"
        ) from None
    schemas = (
        existing.metabase.include_schemas
        if existing
        else propose_include_schemas(
            facts, sorted({schema for tables in table_sets for schema, _ in tables})
        )
    )
    if not existing:
        mappings = [
            (
                name,
                database,
                derive_table_prefix(
                    facts.tables_in(database, schemas), [table for _, table in tables]
                ),
            )
            for (name, database, _), tables in zip(mappings, table_sets, strict=True)
        ]
        prompter.say(f"using schemas: {', '.join(schemas) if schemas else 'all'}")
        config_path.write_text(
            render_stitch_yml(
                project_dir=".",
                target_path=project.target_path,
                metabase_url=json.dumps(url),
                databases=mappings,
                include_schemas=schemas,
            ),
            encoding="utf-8",
        )
        prompter.say(f"wrote {config_path}")
    env_example = write_env_example(project.root, (variable,))
    checks = []
    healthy = True
    for (name, database, prefix), tables in zip(mappings, table_sets, strict=True):
        mapping_checks, mapping_healthy = _mini_doctor(
            config_path=config_path,
            facts=facts,
            version=version,
            metabase_name=name,
            dbt_database=database,
            include_schemas=schemas,
            tables=tables,
            table_prefix=prefix,
            env={**env, **session_env},
        )
        checks.extend(line for line in mapping_checks if line not in checks)
        healthy = healthy and mapping_healthy
    for check in checks:
        prompter.say(check)
    if not healthy:
        prompter.say("fix the mapping in stitch.yml or run 'stitch init --force' to reconfigure")
    return InitResult(
        config_path=config_path,
        gitignore_updated=gitignore_updated,
        env_example_path=env_example,
        checks=checks,
        healthy=healthy,
        credentials=session_env,
    )


def _ask_url(prompter: Prompter) -> str:
    while True:
        try:
            return normalize_metabase_url(prompter.ask("Metabase URL"))
        except ValueError as exc:
            prompter.say(f"  {exc}")
