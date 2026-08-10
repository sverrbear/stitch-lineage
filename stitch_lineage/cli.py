"""stitch CLI: thin orchestration over config, io, resolve and graph modules."""

import contextlib
import json
import subprocess
import threading
import webbrowser
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from stitch_lineage import __version__
from stitch_lineage import apply as apply_service
from stitch_lineage.app import StitchAppError
from stitch_lineage.config import StitchConfig, StitchConfigError, load_config
from stitch_lineage.export.jsonl import export_jsonl
from stitch_lineage.export.static_site import export_site
from stitch_lineage.graph.dead import dead_report, format_dead_report
from stitch_lineage.graph.impact import (
    format_github_comment,
    format_slack_comment,
    impact_from_graphs,
)
from stitch_lineage.graph.schema import (
    Coverage,
    EdgeType,
    Graph,
    NodeType,
)
from stitch_lineage.graph.scopes import erd_scopes
from stitch_lineage.graph.search import search as search_graph
from stitch_lineage.graph.suggest import Suggestion
from stitch_lineage.graph.suggest import suggest as suggest_relationships
from stitch_lineage.io.artifacts import StitchArtifactError, load_catalog, load_manifest
from stitch_lineage.io.dbt_runner import StitchDbtRunnerError, run_docs_generate
from stitch_lineage.io.graph_store import (
    graphs_semantically_equal,
    read_graph,
    write_graph,
)
from stitch_lineage.io.layout_store import LAYOUT_FILENAME, LayoutStoreError, read_dismissed
from stitch_lineage.io.metabase_client import MetabaseAPIError, MetabaseClient
from stitch_lineage.io.staged_store import (
    DESCRIPTIONS_FILENAME,
    STAGED_FILENAME,
    StagedRelationship,
    StagedStoreError,
    read_staged,
)
from stitch_lineage.resolve.bind import bind
from stitch_lineage.resolve.dbt import resolve_dbt
from stitch_lineage.resolve.metabase import resolve_metabase
from stitch_lineage.write.yaml_writer import WritePlan

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="dbt <-> Metabase column lineage.",
)
console = Console()

ConfigOpt = Annotated[
    Path | None, typer.Option("--config", help="Path to stitch.yml (default: ./stitch.yml).")
]

_MB_NODE_TYPES = (NodeType.MB_FIELD, NodeType.MB_CARD, NodeType.MB_DASHBOARD)
_MB_EDGE_TYPES = (EdgeType.CONSUMED_BY, EdgeType.APPEARS_ON)


# the live build progress, so anything printing to `console` can tear it down first
_active_progress: Progress | None = None


def _stop_active_progress() -> None:
    """The progress display renders on its own stderr console, so a message printed to
    `console` while it is live comes out garbled -- stop it before printing."""
    if _active_progress is not None:
        _active_progress.stop()


def _fail(message: str) -> NoReturn:
    _stop_active_progress()
    console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=1)


def _load_config_or_fail(path: Path) -> StitchConfig:
    try:
        return load_config(path)
    except StitchConfigError as exc:
        _fail(str(exc))


def _resolve_config(config: Path | None) -> Path:
    """Default config lookup tolerates a missing ./stitch.yml (commands that only read
    graph.json fall back to .stitch/); an explicitly passed --config must exist."""
    if config is None:
        return Path("stitch.yml")
    if not config.is_file():
        _fail(f"config file not found: {config}")
    return config


def _target_path(config: Path, cfg: StitchConfig) -> Path:
    return config.parent / cfg.dbt.project_dir / cfg.dbt.target_path


def _output_dir(config: Path, cfg: StitchConfig) -> Path:
    return config.parent / cfg.output.dir


def _graph_path(config: Path) -> Path:
    if config.is_file():
        cfg = _load_config_or_fail(config)
        return _output_dir(config, cfg) / "graph.json"
    return Path(".stitch") / "graph.json"


def _graph_path_or_fail(path: Path) -> Path:
    if not path.is_file():
        _fail(f"graph not found at {path} -- run 'stitch build' first")
    return path


def _read_graph_or_fail(path: Path) -> Graph:
    return read_graph(_graph_path_or_fail(path))


def _metabase_url(config: Path) -> str | None:
    """Base URL for Metabase deep links -- None when there is no config or the
    ${VAR} reference did not resolve. The URL is not a secret, so a literal is fine."""
    if not config.is_file():
        return None
    url = _load_config_or_fail(config).metabase.url
    return url if url and "${" not in url else None


def _erd_default_scope(config: Path) -> str | None:
    """Configured ERD landing scope -- None when there is no config."""
    if not config.is_file():
        return None
    return _load_config_or_fail(config).serve.erd_default_scope


def _strip_model_prefixes(config: Path) -> list[str]:
    """Routing prefixes the app hides from model display names (serve.strip_model_prefixes)."""
    if not config.is_file():
        return []
    return _load_config_or_fail(config).serve.strip_model_prefixes


def _table_prefixes(config: Path) -> list[str]:
    """The per-database metabase.databases[].table_prefix values, for display (#80).

    Binding already strips these so dev-target artifacts (sis_fct_matches) match a
    prod-pointed Metabase (fct_matches); the app hides them from the physical names it
    SHOWS for the same reason. An unresolved ${VAR} is dropped rather than shown.
    """
    if not config.is_file():
        return []
    cfg = _load_config_or_fail(config)
    prefixes = (db.table_prefix for db in cfg.metabase.databases if db.table_prefix)
    return list(dict.fromkeys(prefix for prefix in prefixes if "${" not in prefix))


def _warn_unknown_erd_scope(scope: str | None, graph_path: Path) -> None:
    """Whether the scope exists is a property of the graph, so it is checked here
    rather than at config load. The app still opens -- on its auto-picked scope."""
    if not scope:
        return
    try:
        available = erd_scopes(read_graph(graph_path))
    except ValueError:
        return
    if scope in available:
        return
    sample = ", ".join(sorted(available)[:5]) or "none"
    console.print(
        f"[yellow]warning:[/] serve.erd_default_scope '{scope}' is not in the graph "
        f"-- opening the auto-picked scope instead (available: {sample})",
        soft_wrap=True,
    )


def _require_metabase_env(
    cfg: StitchConfig, command: str = "stitch", *, dbt_only_alternative: bool = False
) -> None:
    """Fail with actionable copy unless every ${ENV_VAR} in the metabase section resolved.

    Single source of the missing-env error text: `build` calls it up front so a run that
    will hit the API dies before any work, and _metabase_client() guards every other
    command that talks to Metabase.
    """
    try:
        cfg.metabase.require_env()
    except StitchConfigError as exc:
        pronoun = "them" if len(dict.fromkeys(cfg.metabase.missing_env)) > 1 else "it"
        _stop_active_progress()
        console.print(f"[red]error:[/red] {exc}", soft_wrap=True)
        console.print(f"  {command} needs {pronoun} to call the Metabase API.", soft_wrap=True)
        console.print(
            f"  fix: set {pronoun} in your environment (create a key in Metabase: "
            f"Admin settings -> Authentication -> API keys){',' if dbt_only_alternative else '.'}",
            soft_wrap=True,
        )
        if dbt_only_alternative:
            console.print(
                f"  or run '{command} --no-metabase' for a dbt-only graph.", soft_wrap=True
            )
        raise typer.Exit(code=1) from None


def _metabase_client(cfg: StitchConfig, cache_dir: Path | None = None) -> MetabaseClient:
    _require_metabase_env(cfg)
    return MetabaseClient(
        cfg.metabase.url,
        cfg.metabase.api_key,
        cache_dir=cache_dir,
        min_version=cfg.metabase.min_version,
        retain=cfg.output.retain_cache_runs,
    )


def _build_progress() -> Progress:
    """Build-stage progress. Renders to stderr so stdout stays clean for piping;
    transient so nothing lingers after the summary prints."""
    return Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=Console(stderr=True),
        transient=True,
    )


@contextlib.contextmanager
def _live_build_progress() -> Iterator[Progress]:
    """Run the build progress while registering it for _stop_active_progress()."""
    global _active_progress
    progress = _build_progress()
    _active_progress = progress
    try:
        with progress:
            yield progress
    finally:
        _active_progress = None


def _version_callback(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """stitch: dbt <-> Metabase column lineage."""


# above this share of bindings, a case-only mismatch is the warehouse's identifier
# casing (Snowflake upper-cases unquoted names), not something a user can act on
_CASE_MISMATCH_NORM_SHARE = 0.9


def _print_coverage(
    coverage: Coverage,
    metabase_side: bool,
    case_mismatch_count: int,
    bindings_total: int = 0,
) -> None:
    def row(label: str, value: str) -> None:
        console.print(f"{label:<20} {value}", soft_wrap=True)

    if metabase_side:
        unmatched = (
            f"   ({len(coverage.unbound_models)} unmatched -> stitch doctor --unbound)"
            if coverage.unbound_models
            else ""
        )
        row("models bound", f"{coverage.models_bound}/{coverage.models_total}{unmatched}")
        row("MBQL cards", f"{coverage.mbql_cards_resolved}/{coverage.mbql_cards_total}")
        row(
            "native SQL cards",
            f"{coverage.native_cards_resolved}/{coverage.native_cards_total}   unsupported in v0",
        )
        row("dashboards", f"{coverage.dashboards}/{coverage.dashboards_total}")
    notes = []
    if coverage.columns_inferred:
        notes.append(f"{coverage.columns_inferred} inferred via star-expansion")
    if coverage.untraced_columns:
        notes.append(f"{len(coverage.untraced_columns)} unresolved -> stitch doctor --untraced")
    suffix = f"   ({', '.join(notes)})" if notes else ""
    row(
        "dbt column lineage",
        f"{coverage.columns_traced}/{coverage.columns_total} columns traced{suffix}",
    )
    if coverage.seed_snapshot_dependencies:
        console.print(
            f"note: {coverage.seed_snapshot_dependencies} seed/snapshot dependencies "
            "not represented",
            soft_wrap=True,
        )
    if coverage.dangling_relationships:
        row("dangling relationships", str(len(coverage.dangling_relationships)))
        for item in coverage.dangling_relationships:
            console.print(f"    {item}", soft_wrap=True)
    if case_mismatch_count:
        if bindings_total and case_mismatch_count / bindings_total > _CASE_MISMATCH_NORM_SHARE:
            console.print(
                f"note: {case_mismatch_count}/{bindings_total} column bindings matched on a "
                "case-only mismatch -- warehouse identifier casing, nothing to fix",
                soft_wrap=True,
            )
        else:
            console.print(
                f"warning: {case_mismatch_count} column bindings matched on a case-only mismatch",
                soft_wrap=True,
            )
    if coverage.unverified_field_count:
        console.print(
            f"warning: {coverage.unverified_field_count} Metabase fields left unbound -- "
            "their dbt model has no column inventory (run 'dbt docs generate')",
            soft_wrap=True,
        )


@app.command()
def build(
    config: ConfigOpt = None,
    no_metabase: Annotated[
        bool,
        typer.Option("--no-metabase", help="Resolve the dbt side only (CI impact runs)."),
    ] = False,
    check: Annotated[
        bool,
        typer.Option("--check", help="Compare against the committed graph.json; exit 1 on drift."),
    ] = False,
    docs: Annotated[
        bool | None,
        typer.Option(
            "--docs/--no-docs",
            help="Run 'dbt docs generate' first (overrides dbt.auto_docs either way).",
        ),
    ] = None,
) -> None:
    """Resolve dbt artifacts and the Metabase API into .stitch/graph.json."""
    _run_build(config=config, no_metabase=no_metabase, check=check, docs=docs)


def _run_build(
    *,
    config: Path | None,
    no_metabase: bool = False,
    check: bool = False,
    docs: bool | None = None,
) -> None:
    """The build pipeline itself, so `stitch apply --build` runs it instead of half of it.

    Same semantics as the command in every respect -- `docs=None` means "honour
    dbt.auto_docs" -- because it IS the command's body.
    """
    config = _resolve_config(config)
    cfg = _load_config_or_fail(config)
    if not no_metabase:
        # this run will call the API: fail now, not after docs generate and minutes of tracing
        _require_metabase_env(cfg, "stitch build", dbt_only_alternative=True)
    target_path = _target_path(config, cfg)
    out_dir = _output_dir(config, cfg)
    graph_path = out_dir / "graph.json"
    database_map = [
        (db.metabase_name, db.dbt_database, db.table_prefix) for db in cfg.metabase.databases
    ]

    if cfg.dbt.auto_docs if docs is None else docs:
        console.print("running dbt docs generate...")
        try:
            run_docs_generate(config.parent / cfg.dbt.project_dir, cfg.dbt.docs_args)
        except StitchDbtRunnerError as exc:
            _fail(str(exc))

    dbt_only_note: str | None = None
    with _live_build_progress() as progress:
        load_task = progress.add_task("loading artifacts", total=1)
        try:
            manifest = load_manifest(target_path)
            catalog = load_catalog(target_path)
        except StitchArtifactError as exc:
            _fail(str(exc))
        progress.update(load_task, completed=1)

        trace_task = progress.add_task("tracing column lineage", total=None)
        dbt_res = resolve_dbt(
            manifest,
            catalog,
            fk_meta_keys=cfg.relationships.fk_meta_keys,
            cardinality_meta_key=cfg.relationships.cardinality_meta_key,
            on_progress=lambda done, total: progress.update(
                trace_task, completed=done, total=total
            ),
        )

        nodes = list(dbt_res.nodes)
        edges = list(dbt_res.edges)
        coverage_fields: dict[str, Any] = {
            "columns_traced": dbt_res.columns_traced,
            "columns_total": dbt_res.columns_total,
            "columns_inferred": dbt_res.columns_inferred,
            "untraced_columns": dbt_res.untraced_columns,
            "dangling_relationships": dbt_res.dangling_relationships,
            "seed_snapshot_dependencies": dbt_res.seed_snapshot_dependencies,
        }
        metabase_version: str | None = None
        metabase_side = True
        case_mismatch_count = 0
        bindings_total = 0

        if no_metabase:
            # SPEC section 10: reuse the committed baseline's Metabase side; the dbt side
            # changed, so binding must re-run against the reused mb_field nodes.
            baseline = read_graph(graph_path) if graph_path.is_file() else None
            mb_nodes = (
                [n for n in baseline.nodes if n.node_type in _MB_NODE_TYPES] if baseline else []
            )
            mb_field_nodes = [n for n in mb_nodes if n.node_type is NodeType.MB_FIELD]
            if not mb_field_nodes:
                # a dbt-only baseline is as useless for reuse as no baseline at all
                metabase_side = False
                missing = (
                    "no existing graph.json"
                    if baseline is None
                    else f"existing {graph_path} has no Metabase side"
                )
                dbt_only_note = (
                    f"note: {missing} -- building a dbt-only graph "
                    "(run a full 'stitch build' to add the Metabase side)"
                )
            else:
                bind_task = progress.add_task("binding", total=1)
                bind_res = bind(nodes, mb_field_nodes, database_map)
                progress.update(bind_task, completed=1)
                nodes.extend(mb_nodes)
                edges.extend(e for e in baseline.edges if e.edge_type in _MB_EDGE_TYPES)
                edges.extend(bind_res.edges)
                metabase_version = baseline.metabase_version
                case_mismatch_count = bind_res.case_mismatch_count
                bindings_total = len(bind_res.edges)
                coverage_fields.update(
                    models_bound=bind_res.models_bound,
                    models_total=bind_res.models_total,
                    unbound_models=bind_res.unbound_models,
                    unverified_field_count=bind_res.unverified_field_count,
                    mbql_cards_resolved=baseline.coverage.mbql_cards_resolved,
                    mbql_cards_total=baseline.coverage.mbql_cards_total,
                    native_cards_resolved=baseline.coverage.native_cards_resolved,
                    native_cards_total=baseline.coverage.native_cards_total,
                    dashboards=baseline.coverage.dashboards,
                    dashboards_total=baseline.coverage.dashboards_total,
                    unresolved_cards=baseline.coverage.unresolved_cards,
                    unresolved_field_refs=baseline.coverage.unresolved_field_refs,
                )
        else:
            client = _metabase_client(cfg, cache_dir=out_dir / "cache")
            fetch_task = progress.add_task("fetching Metabase", total=1)
            try:
                payload = client.fetch_all([db.metabase_name for db in cfg.metabase.databases])
            except MetabaseAPIError as exc:
                _fail(str(exc))
            progress.update(fetch_task, completed=1)

            cards_task = progress.add_task("resolving cards", total=None)
            mb_res = resolve_metabase(
                payload,
                cfg.metabase.exclude_collections,
                cfg.metabase.include_schemas,
                on_progress=lambda done, total: progress.update(
                    cards_task, completed=done, total=total
                ),
            )
            bind_task = progress.add_task("binding", total=1)
            bind_res = bind(
                nodes,
                [n for n in mb_res.nodes if n.node_type is NodeType.MB_FIELD],
                database_map,
            )
            progress.update(bind_task, completed=1)
            nodes.extend(mb_res.nodes)
            edges.extend(mb_res.edges)
            edges.extend(bind_res.edges)
            metabase_version = payload.metabase_version
            case_mismatch_count = bind_res.case_mismatch_count
            bindings_total = len(bind_res.edges)
            coverage_fields.update(
                models_bound=bind_res.models_bound,
                models_total=bind_res.models_total,
                unbound_models=bind_res.unbound_models,
                unverified_field_count=bind_res.unverified_field_count,
                mbql_cards_resolved=mb_res.mbql_cards_resolved,
                mbql_cards_total=mb_res.mbql_cards_total,
                native_cards_resolved=mb_res.native_cards_resolved,
                native_cards_total=mb_res.native_cards_total,
                dashboards=mb_res.dashboards,
                dashboards_total=mb_res.dashboards_total,
                unresolved_cards=mb_res.unresolved_cards,
                unresolved_field_refs=mb_res.unresolved_field_refs,
            )

        graph = Graph(
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            dbt_invocation_id=manifest.get("metadata", {}).get("invocation_id"),
            metabase_version=metabase_version,
            coverage=Coverage(**coverage_fields),
            nodes=nodes,
            edges=edges,
        )

        drifted = False
        if check:
            if not graph_path.is_file():
                _fail(f"no committed graph at {graph_path} to check against -- run 'stitch build'")
            drifted = not graphs_semantically_equal(read_graph(graph_path), graph)
        else:
            write_task = progress.add_task("writing graph", total=1)
            write_graph(graph, graph_path)
            progress.update(write_task, completed=1)

    # everything below prints after the transient progress display has stopped
    if dbt_only_note:
        console.print(dbt_only_note)
    if check:
        if drifted:
            console.print(
                f"[red]drift:[/red] {graph_path} is stale -- "
                "run 'stitch build' and commit the result"
            )
            raise typer.Exit(code=1)
        console.print("graph.json is up to date")
        return
    console.print(f"wrote {graph_path} ({len(nodes)} nodes, {len(edges)} edges)")
    _print_coverage(graph.coverage, metabase_side, case_mismatch_count, bindings_total)


def _baseline_graph(base: str, graph_path: Path) -> Graph:
    ref_path = graph_path.as_posix()
    toplevel = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if toplevel.returncode == 0:
        with contextlib.suppress(ValueError):
            resolved = graph_path.resolve().relative_to(Path(toplevel.stdout.strip()))
            ref_path = resolved.as_posix()
    result = subprocess.run(
        ["git", "show", f"{base}:{ref_path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # "path missing at ref" gets an actionable message; bad refs / not-a-repo
        # keep the raw git stderr, which already names the real problem.
        if "exists on disk, but not in" in stderr or "does not exist in" in stderr:
            console.print(f"[red]error:[/red] no committed baseline at {base}:{ref_path}")
            console.print(
                "  stitch impact diffs the current build against the graph committed "
                "on the base ref.",
                soft_wrap=True,
            )
            console.print(
                f"  fix: on the base branch, run 'stitch build' and commit {ref_path} "
                "-- or pass --base <ref> that has one.",
                soft_wrap=True,
            )
            raise typer.Exit(code=1)
        _fail(f"could not load the baseline via 'git show {base}:{ref_path}' -- {stderr}")
    return Graph.model_validate_json(result.stdout)


def _plain_text(comment: str) -> str:
    lines = []
    for line in comment.splitlines():
        stripped = line.strip()
        if len(stripped) > 1 and stripped.startswith("_") and stripped.endswith("_"):
            line = line.replace(stripped, stripped[1:-1])
        lines.append(line)
    return "\n".join(lines)


@app.command(hidden=True)
def impact(
    base: Annotated[
        str,
        typer.Option("--base", help="Git ref whose committed graph.json is the baseline."),
    ] = "origin/main",
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text, github-comment or slack."),
    ] = "text",
    fail_on_impact: Annotated[
        bool,
        typer.Option(
            "--fail-on-impact",
            help="Exit 1 when any column was removed or type-changed.",
        ),
    ] = False,
    config: ConfigOpt = None,
) -> None:
    """Diff the candidate graph against the baseline and walk the downstream blast radius.

    Shelved pending the committed-baseline workflow; invoke directly if you keep your own baselines.
    """
    config = _resolve_config(config)
    if output_format not in ("text", "github-comment", "slack"):
        _fail(f"unsupported --format '{output_format}' (expected: text, github-comment, slack)")
    graph_path = _graph_path(config)
    candidate = _read_graph_or_fail(graph_path)
    baseline = _baseline_graph(base, graph_path)
    diff, report = impact_from_graphs(baseline, candidate)
    if output_format == "slack":
        typer.echo(format_slack_comment(diff, report, baseline))
    else:
        comment = format_github_comment(diff, report, baseline)
        typer.echo(comment if output_format == "github-comment" else _plain_text(comment))
    if fail_on_impact and (diff.removed or diff.type_changed):
        raise typer.Exit(code=1)


def _score_tier(score: float) -> str:
    if score >= 5:
        return "exact"
    if score >= 4:
        return "prefix"
    if score >= 3:
        return "word"
    if score >= 2:
        return "substring"
    return "fuzzy"


@app.command()
def search(
    query: Annotated[
        str,
        typer.Argument(help="Free-text query over models, columns, fields, cards, dashboards."),
    ],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit results as JSON lines for piping.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Maximum results.")] = 20,
    config: ConfigOpt = None,
) -> None:
    """Search everything in graph.json from the terminal."""
    graph = _read_graph_or_fail(_graph_path(_resolve_config(config)))
    results = search_graph(graph, query, limit=limit)
    if json_output:
        for result in results:
            typer.echo(json.dumps(result.model_dump(mode="json"), sort_keys=True))
        return
    if not results:
        console.print("no results")
        return
    table = Table("type", "name", "context", "match")
    for result in results:
        table.add_row(
            result.node_type.value,
            result.name,
            result.context or "",
            _score_tier(result.score),
        )
    console.print(table)


@app.command()
def suggest(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit suggestions as JSON lines for piping.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Maximum suggestions (0 for all).")] = 0,
    config: ConfigOpt = None,
) -> None:
    """List candidate relationships nobody has declared yet, strongest evidence first.

    Sources: Metabase implicit joins (cards already joining through an FK, scored by how
    many) and `<entity>_id` naming conventions. Pairs already declared, already staged or
    previously dismissed in the app never appear.
    """
    config = _resolve_config(config)
    graph = _read_graph_or_fail(_graph_path(config))
    try:
        staged = [
            (entry.from_model, entry.from_column, entry.to_model, entry.to_column)
            for entry in read_staged(_default_out_dir(config, STAGED_FILENAME))
        ]
    except StagedStoreError as exc:
        _fail(str(exc))
    try:
        dismissed = read_dismissed(_default_out_dir(config, LAYOUT_FILENAME))
    except LayoutStoreError as exc:
        _fail(str(exc))

    suggestions = suggest_relationships(graph, staged, dismissed)
    if limit > 0:
        suggestions = suggestions[:limit]
    if json_output:
        for suggestion in suggestions:
            typer.echo(json.dumps(suggestion.model_dump(mode="json"), sort_keys=True))
        return
    if not suggestions:
        console.print("no suggestions")
        return
    table = Table("source", "score", "from", "to", "why")
    for suggestion in suggestions:
        table.add_row(
            suggestion.source,
            f"{suggestion.score:g}",
            f"{suggestion.from_model}.{suggestion.from_column}",
            f"{suggestion.to_model}.{suggestion.to_column}",
            _suggestion_why(suggestion),
        )
    console.print(table)


def _suggestion_why(suggestion: Suggestion) -> str:
    if suggestion.source == "implicit_join":
        cards = len(suggestion.evidence.get("card_ids") or [])
        return f"{cards} card{'s' if cards != 1 else ''} join through it"
    return f"names the '{suggestion.evidence.get('entity')}' grain"


@app.command()
def doctor(
    list_databases: Annotated[
        bool,
        typer.Option("--list-databases", help="List databases visible to the Metabase API key."),
    ] = False,
    unbound: Annotated[
        bool, typer.Option("--unbound", help="List dbt models with no bound Metabase table.")
    ] = False,
    untraced: Annotated[
        bool, typer.Option("--untraced", help="List columns sqlglot could not trace.")
    ] = False,
    unresolved_cards: Annotated[
        bool,
        typer.Option(
            "--unresolved-cards", help="List unresolved cards with per-field-ref reasons."
        ),
    ] = False,
    dead: Annotated[
        bool,
        typer.Option(
            "--dead",
            help="Report dead weight: unconsumed columns, models feeding nothing, "
            "archived-but-bound cards.",
        ),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the --dead report as a JSON object.")
    ] = False,
    config: ConfigOpt = None,
) -> None:
    """Diagnose configuration, connectivity, and coverage gaps."""
    if json_output and not dead:
        _fail("--json is only supported with --dead")
    config = _resolve_config(config)
    cfg = _load_config_or_fail(config)
    graph_path = _output_dir(config, cfg) / "graph.json"

    if list_databases:
        try:
            databases = _metabase_client(cfg).list_databases()
        except MetabaseAPIError as exc:
            _fail(str(exc))
        for database in databases:
            console.print(str(database.get("name", "?")))
        return

    if unbound or untraced or unresolved_cards or dead:
        graph = _read_graph_or_fail(graph_path)
        if unbound:
            _print_list("unbound models", graph.coverage.unbound_models)
        if untraced:
            _print_list("untraced columns", graph.coverage.untraced_columns)
        if unresolved_cards:
            _print_unresolved_cards(graph)
        if dead:
            _print_dead(graph, json_output)
        return

    failed = False
    console.print(f"ok    {config} parses")

    target_path = _target_path(config, cfg)
    try:
        load_manifest(target_path)
        load_catalog(target_path)
        console.print(f"ok    dbt artifacts in {target_path} parse")
    except StitchArtifactError as exc:
        failed = True
        console.print(f"fail  {exc}")

    if graph_path.is_file():
        try:
            graph = read_graph(graph_path)
        except ValueError as exc:
            failed = True
            console.print(f"fail  {graph_path} does not parse: {exc}")
        else:
            expected = Graph.model_fields["schema_version"].default
            if graph.schema_version != expected:
                failed = True
                console.print(
                    f"fail  {graph_path} has schema_version {graph.schema_version}, "
                    f"expected {expected} -- rebuild with this stitch version"
                )
            else:
                console.print(
                    f"ok    {graph_path}: schema_version {graph.schema_version}, "
                    f"{len(graph.nodes)} nodes / {len(graph.edges)} edges"
                )
    else:
        failed = True
        console.print(f"fail  graph.json not found at {graph_path} -- run 'stitch build'")

    if cfg.metabase.missing_env:
        names = ", ".join(dict.fromkeys(cfg.metabase.missing_env))
        console.print(f"skip  Metabase: {names} not set -- skipping connectivity check")
    else:
        try:
            version = _metabase_client(cfg).assert_version()
            console.print(f"ok    Metabase reachable, version {version}")
        except MetabaseAPIError as exc:
            failed = True
            console.print(f"fail  Metabase: {exc}")

    if failed:
        raise typer.Exit(code=1)


def _print_list(label: str, items: list[str]) -> None:
    console.print(f"{label} ({len(items)}):")
    for item in items:
        console.print(f"  {item}", soft_wrap=True)


def _print_dead(graph: Graph, json_output: bool) -> None:
    """typer.echo, not console.print: card titles are Metabase user input, and rich
    would swallow a square-bracketed one as markup."""
    report = dead_report(graph)
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    typer.echo(format_dead_report(report))


def _print_unresolved_cards(graph: Graph) -> None:
    console.print(f"unresolved cards ({len(graph.coverage.unresolved_cards)}):")
    refs_by_card: dict[Any, list[dict[str, Any]]] = {}
    for problem in graph.coverage.unresolved_field_refs:
        refs_by_card.setdefault(problem.get("card_id"), []).append(problem)
    for card_id in graph.coverage.unresolved_cards:
        problems = refs_by_card.get(card_id)
        if not problems:
            console.print(f"  card {card_id}: native SQL (unsupported in v0)", soft_wrap=True)
            continue
        for problem in problems:
            console.print(
                f"  card {card_id}: {problem.get('reason')} -- ref {problem.get('ref')}",
                soft_wrap=True,
            )


def _default_out_dir(config: Path, name: str) -> Path:
    if config.is_file():
        return _output_dir(config, _load_config_or_fail(config)) / name
    return Path(".stitch") / name


@app.command()
def export(
    output_format: Annotated[
        str, typer.Option("--format", help="Export format: jsonl or site.")
    ] = "jsonl",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Output directory (default: <output.dir>/export or /site)."),
    ] = None,
    config: ConfigOpt = None,
) -> None:
    """Export graph.json as flat agent-friendly records, or as a static site."""
    if output_format not in ("jsonl", "site"):
        _fail(f"unsupported --format '{output_format}' (expected: jsonl, site)")
    config = _resolve_config(config)
    graph_path = _graph_path_or_fail(_graph_path(config))

    if output_format == "site":
        out = out or _default_out_dir(config, "site")
        try:
            scope = _erd_default_scope(config)
            _warn_unknown_erd_scope(scope, graph_path)
            site_dir = export_site(
                graph_path,
                out,
                _metabase_url(config),
                scope,
                strip_model_prefixes=_strip_model_prefixes(config),
                table_prefixes=_table_prefixes(config),
            )
        except (StitchAppError, ValueError) as exc:
            _fail(str(exc))
        console.print(
            f"wrote {site_dir / 'index.html'} -- open it or host the directory", soft_wrap=True
        )
        return

    out = out or _default_out_dir(config, "export")
    nodes_path, edges_path = export_jsonl(read_graph(graph_path), out)
    console.print(f"wrote {nodes_path} and {edges_path}")


def _print_diff(diff: str) -> None:
    """Colourize a unified diff without letting rich interpret its contents as markup."""
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            style = "bold"
        elif line.startswith("+"):
            style = "green"
        elif line.startswith("-"):
            style = "red"
        elif line.startswith("@@"):
            style = "cyan"
        else:
            style = ""
        console.print(line, style=style, highlight=False, soft_wrap=True, markup=False)


def _git_dirty(path: Path) -> bool:
    """Whether `path` has uncommitted changes (untracked counts). Not a repo -> nothing to guard."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", path.name],
        cwd=path.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


_GRAPH_PATCHED = (
    "graph updated, refresh the app · next 'stitch build' will confirm them from the manifest"
)


def _applied_label(plan: apply_service.ApplyPlan, outcome: apply_service.ApplyOutcome) -> str:
    """ "2 relationships and 1 description" -- what the closing line counts."""
    counts = (
        (sum(1 for e in plan.changes.relationships if e.id in outcome.cleared), "relationship"),
        (sum(1 for e in plan.changes.descriptions if e.id in outcome.cleared), "description"),
    )
    parts = [f"{count} {noun}{'s' if count != 1 else ''}" for count, noun in counts if count]
    return " and ".join(parts) or "nothing"


def _report_graph_patch(patch: apply_service.GraphPatch | None, applied: str | None) -> None:
    """Print the graph-patch notes, then the closing line (`applied` prefixes it when given)."""
    if patch is not None and patch.note:
        console.print(f"note: {patch.note}", soft_wrap=True)
    if patch is not None and patch.skipped:
        count = len(patch.skipped)
        console.print(
            f"note: {count} applied change{'s' if count != 1 else ''} not added to the graph -- "
            f"{'their' if count != 1 else 'its'} models or columns are not in it yet; the next "
            "'stitch build' picks them up",
            soft_wrap=True,
        )
        for label in patch.skipped:
            console.print(f"    {label}", soft_wrap=True)
    patched = patch is not None and patch.patched
    if applied is None:
        if patched:
            console.print(_GRAPH_PATCHED, soft_wrap=True)
        return
    console.print(f"{applied} — {_GRAPH_PATCHED}" if patched else applied, soft_wrap=True)


def _warn_dropped_cardinality(plan: apply_service.ApplyPlan) -> None:
    """A relationships test cannot carry cardinality -- say so before writing one."""
    if plan.write_to != "relationships_test":
        return
    dropped = {
        result.entry.cardinality
        for result in plan.planned
        if isinstance(result.entry, StagedRelationship)
        and result.entry.cardinality != "many-to-one"
    }
    if dropped:
        console.print(
            f"[yellow]warning:[/] a relationships test cannot carry cardinality "
            f"({', '.join(sorted(dropped))}) -- set relationships.write_to: meta to keep it",
            soft_wrap=True,
        )


def _report_plan(plan: WritePlan, root: Path) -> None:
    for result in plan.unchanged:
        console.print(f"skip  {result.entry.label} -- {result.message}", soft_wrap=True)
    for result in plan.failures:
        console.print(
            f"[red]cannot apply[/red] {result.entry.label}\n  {result.message}", soft_wrap=True
        )
    diff = plan.diff(root)
    if diff:
        console.print()
        _print_diff(diff)
        console.print()


@app.command()
def apply(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the diff and stop without writing.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Write even when a target schema file has uncommitted edits."),
    ] = False,
    no_graph_update: Annotated[
        bool,
        typer.Option("--no-graph-update", help="Do not patch the applied edges into graph.json."),
    ] = False,
    build_after: Annotated[
        bool,
        typer.Option(
            "--build",
            help="Run a full 'stitch build' afterwards (ignored with --dry-run).",
        ),
    ] = False,
    config: ConfigOpt = None,
) -> None:
    """Materialize staged relationships and description edits into model YAML.

    Reads .stitch/staged_relationships.yml and .stitch/staged_descriptions.yml -- everything
    staged in `stitch serve` -- and writes each change into the owning model's schema file:
    relationships onto the FK column in the form chosen by relationships.write_to,
    descriptions onto the model or column entry. One diff preview, one confirmation, one pass.
    Applied entries clear from their store; entries that could not be applied stay staged and
    are reported.

    What was applied is then patched into .stitch/graph.json -- `relates_to` edges and node
    descriptions -- so the app shows the change on a refresh instead of after the next build
    (--no-graph-update opts out). --build reconciles the whole graph from the manifest
    instead, docs generate and all.

    Exit codes: 0 applied or nothing to do, 1 on any refusal or error.
    """
    config = _resolve_config(config)
    cfg = _load_config_or_fail(config)
    try:
        plan = apply_service.build_plan(config, cfg)
    except StagedStoreError as exc:
        _fail(str(exc))
    except StitchArtifactError as exc:
        _fail(str(exc))
    except NotImplementedError as exc:
        _fail(str(exc))

    staged = plan.paths.relationships_store
    if not len(plan.changes):
        console.print(
            f"nothing staged in {staged.parent} -- draw relationships or edit a description "
            "in 'stitch serve' first",
            soft_wrap=True,
        )
        return

    relationships = len(plan.changes.relationships)
    descriptions = len(plan.changes.descriptions)
    if relationships:
        console.print(
            f"{relationships} staged relationship{'s' if relationships != 1 else ''} "
            f"-> {plan.write_to}",
            soft_wrap=True,
        )
    if descriptions:
        console.print(
            f"{descriptions} staged description{'s' if descriptions != 1 else ''}", soft_wrap=True
        )
    root = plan.paths.project_dir.resolve()
    _report_plan(plan.plan, root)
    _warn_dropped_cardinality(plan)

    if dry_run:
        console.print(
            f"--dry-run: nothing written ({len(plan.edits)} file"
            f"{'s' if len(plan.edits) != 1 else ''} would change)",
            soft_wrap=True,
        )
        if plan.failures:
            raise typer.Exit(code=1)
        return

    refused = apply_service.refusals(plan, force=force)
    for edit in refused:
        console.print(
            f"[red]refusing[/red] {edit.path} has uncommitted changes -- "
            "commit or stash it, or re-run with --force",
            soft_wrap=True,
        )
    writable = [edit for edit in plan.edits if edit not in refused]
    if refused and not writable:
        raise typer.Exit(code=1)
    if (
        writable
        and not yes
        and not typer.confirm(f"apply to {len(writable)} file(s)?", default=False)
    ):
        console.print("aborted -- nothing written")
        raise typer.Exit(code=1)

    outcome = apply_service.execute(plan, refused, update_graph=not no_graph_update)
    for path in outcome.written:
        console.print(f"wrote {path}", soft_wrap=True)

    if not outcome.applied:
        console.print("nothing to write")
    elif not outcome.written:
        # everything appliable was already in the repo: the entries still clear, and the graph
        # patch is what keeps the change visible once the staged edge leaves the app
        console.print(
            f"cleared {outcome.applied} already-declared entr"
            f"{'ies' if outcome.applied != 1 else 'y'} from {staged.parent}",
            soft_wrap=True,
        )
        _report_graph_patch(outcome.graph, None)
    else:
        if outcome.still_staged:
            console.print(
                f"{outcome.still_staged} change{'s' if outcome.still_staged != 1 else ''} "
                "still staged",
                soft_wrap=True,
            )
        _report_graph_patch(outcome.graph, f"applied {_applied_label(plan, outcome)}")

    if build_after:
        console.print()
        _run_build(config=config)
    if plan.failures or refused:
        raise typer.Exit(code=1)


@app.command()
def init() -> None:
    """Set up stitch.yml interactively."""
    console.print(
        "stitch init is not implemented until Phase 1 -- "
        "copy the stitch.yml example from SPEC.md section 6.1 for now."
    )
    raise typer.Exit(code=2)


def _open_browser_soon(url: str) -> None:
    """uvicorn.run blocks, so the browser opens from a timer thread once it is listening."""
    threading.Timer(0.5, webbrowser.open, [url]).start()


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port", help="Port to bind.")] = 8787,
    host: Annotated[str, typer.Option("--host", help="Interface to bind.")] = "127.0.0.1",
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the app in a browser at startup.")
    ] = True,
    config: ConfigOpt = None,
) -> None:
    """Run the local lineage + ERD app."""
    # deferred: importing FastAPI/uvicorn costs every other command a few hundred ms
    import uvicorn

    from stitch_lineage.app.server import create_app

    config = _resolve_config(config)
    graph_path = _graph_path_or_fail(_graph_path(config))
    scope = _erd_default_scope(config)
    _warn_unknown_erd_scope(scope, graph_path)
    staged = _default_out_dir(config, STAGED_FILENAME)
    layout = _default_out_dir(config, LAYOUT_FILENAME)
    descriptions = _default_out_dir(config, DESCRIPTIONS_FILENAME)
    # apply from the app needs the config it would apply with; without a stitch.yml the app
    # still serves the graph, it just cannot write the repo
    context = (
        apply_service.ApplyContext(config=config, cfg=_load_config_or_fail(config))
        if config.is_file()
        else None
    )
    try:
        server = create_app(
            graph_path,
            _metabase_url(config),
            scope,
            staged,
            layout,
            descriptions,
            context,
            strip_model_prefixes=_strip_model_prefixes(config),
            table_prefixes=_table_prefixes(config),
        )
    except StitchAppError as exc:
        _fail(str(exc))
    url = f"http://{host}:{port}"
    console.print(f"stitch serve -> {url}   (graph: {graph_path})", soft_wrap=True)
    if open_browser:
        _open_browser_soon(url)
    uvicorn.run(server, host=host, port=port, log_level="warning")
