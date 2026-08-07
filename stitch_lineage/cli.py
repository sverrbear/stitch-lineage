"""stitch CLI: thin orchestration over config, io, resolve and graph modules."""

import contextlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from stitch_lineage import __version__
from stitch_lineage.config import StitchConfig, StitchConfigError, load_config
from stitch_lineage.export.jsonl import export_jsonl
from stitch_lineage.graph.impact import (
    format_github_comment,
    format_slack_comment,
    impact_from_graphs,
)
from stitch_lineage.graph.schema import Coverage, EdgeType, Graph, NodeType
from stitch_lineage.graph.search import search as search_graph
from stitch_lineage.io.artifacts import StitchArtifactError, load_catalog, load_manifest
from stitch_lineage.io.graph_store import graphs_semantically_equal, read_graph, write_graph
from stitch_lineage.io.metabase_client import MetabaseAPIError, MetabaseClient
from stitch_lineage.resolve.bind import bind
from stitch_lineage.resolve.dbt import resolve_dbt
from stitch_lineage.resolve.metabase import resolve_metabase

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


def _fail(message: str) -> NoReturn:
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


def _read_graph_or_fail(path: Path) -> Graph:
    if not path.is_file():
        _fail(f"graph not found at {path} -- run 'stitch build' first")
    return read_graph(path)


def _metabase_client(cfg: StitchConfig, cache_dir: Path | None = None) -> MetabaseClient:
    try:
        cfg.metabase.require_env()
    except StitchConfigError as exc:
        _fail(str(exc))
    return MetabaseClient(
        cfg.metabase.url,
        cfg.metabase.api_key,
        cache_dir=cache_dir,
        min_version=cfg.metabase.min_version,
        retain=cfg.output.retain_cache_runs,
    )


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


def _print_coverage(coverage: Coverage, metabase_side: bool, case_mismatch_count: int) -> None:
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
            f"{coverage.native_cards_resolved}/{coverage.native_cards_total}"
            "   unsupported in v0",
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
    if coverage.dangling_relationships:
        row("dangling relationships", str(len(coverage.dangling_relationships)))
        for item in coverage.dangling_relationships:
            console.print(f"    {item}", soft_wrap=True)
    if case_mismatch_count:
        console.print(
            f"warning: {case_mismatch_count} column bindings matched on a case-only mismatch",
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
) -> None:
    """Resolve dbt artifacts and the Metabase API into .stitch/graph.json."""
    config = _resolve_config(config)
    cfg = _load_config_or_fail(config)
    target_path = _target_path(config, cfg)
    out_dir = _output_dir(config, cfg)
    graph_path = out_dir / "graph.json"
    database_map = [
        (db.metabase_name, db.dbt_database, db.table_prefix) for db in cfg.metabase.databases
    ]

    try:
        manifest = load_manifest(target_path)
        catalog = load_catalog(target_path)
    except StitchArtifactError as exc:
        _fail(str(exc))
    dbt_res = resolve_dbt(
        manifest,
        catalog,
        fk_meta_keys=cfg.relationships.fk_meta_keys,
        cardinality_meta_key=cfg.relationships.cardinality_meta_key,
    )

    nodes = list(dbt_res.nodes)
    edges = list(dbt_res.edges)
    coverage_fields: dict[str, Any] = {
        "columns_traced": dbt_res.columns_traced,
        "columns_total": dbt_res.columns_total,
        "columns_inferred": dbt_res.columns_inferred,
        "untraced_columns": dbt_res.untraced_columns,
        "dangling_relationships": dbt_res.dangling_relationships,
    }
    metabase_version: str | None = None
    metabase_side = True
    case_mismatch_count = 0

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
            console.print(
                f"note: {missing} -- building a dbt-only graph "
                "(run a full 'stitch build' to add the Metabase side)"
            )
        else:
            bind_res = bind(nodes, mb_field_nodes, database_map)
            nodes.extend(mb_nodes)
            edges.extend(e for e in baseline.edges if e.edge_type in _MB_EDGE_TYPES)
            edges.extend(bind_res.edges)
            metabase_version = baseline.metabase_version
            case_mismatch_count = bind_res.case_mismatch_count
            coverage_fields.update(
                models_bound=bind_res.models_bound,
                models_total=bind_res.models_total,
                unbound_models=bind_res.unbound_models,
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
        try:
            payload = client.fetch_all([db.metabase_name for db in cfg.metabase.databases])
        except MetabaseAPIError as exc:
            _fail(str(exc))
        mb_res = resolve_metabase(
            payload, cfg.metabase.exclude_collections, cfg.metabase.include_schemas
        )
        bind_res = bind(
            nodes,
            [n for n in mb_res.nodes if n.node_type is NodeType.MB_FIELD],
            database_map,
        )
        nodes.extend(mb_res.nodes)
        edges.extend(mb_res.edges)
        edges.extend(bind_res.edges)
        metabase_version = payload.metabase_version
        case_mismatch_count = bind_res.case_mismatch_count
        coverage_fields.update(
            models_bound=bind_res.models_bound,
            models_total=bind_res.models_total,
            unbound_models=bind_res.unbound_models,
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

    if check:
        if not graph_path.is_file():
            _fail(f"no committed graph at {graph_path} to check against -- run 'stitch build'")
        baseline = read_graph(graph_path)
        if graphs_semantically_equal(baseline, graph):
            console.print("graph.json is up to date")
            return
        console.print(
            f"[red]drift:[/red] {graph_path} is stale -- run 'stitch build' and commit the result"
        )
        raise typer.Exit(code=1)

    write_graph(graph, graph_path)
    console.print(f"wrote {graph_path} ({len(nodes)} nodes, {len(edges)} edges)")
    _print_coverage(graph.coverage, metabase_side, case_mismatch_count)


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
        _fail(
            f"could not load the baseline via 'git show {base}:{ref_path}' -- "
            f"{result.stderr.strip()}"
        )
    return Graph.model_validate_json(result.stdout)


def _plain_text(comment: str) -> str:
    lines = []
    for line in comment.splitlines():
        stripped = line.strip()
        if len(stripped) > 1 and stripped.startswith("_") and stripped.endswith("_"):
            line = line.replace(stripped, stripped[1:-1])
        lines.append(line)
    return "\n".join(lines)


@app.command()
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
    """Diff the candidate graph against the baseline and walk the downstream blast radius."""
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
    config: ConfigOpt = None,
) -> None:
    """Diagnose configuration, connectivity, and coverage gaps."""
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

    if unbound or untraced or unresolved_cards:
        graph = _read_graph_or_fail(graph_path)
        if unbound:
            _print_list("unbound models", graph.coverage.unbound_models)
        if untraced:
            _print_list("untraced columns", graph.coverage.untraced_columns)
        if unresolved_cards:
            _print_unresolved_cards(graph)
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


@app.command()
def export(
    output_format: Annotated[
        str, typer.Option("--format", help="Export format (jsonl).")
    ] = "jsonl",
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Output directory (default: <output.dir>/export)."),
    ] = None,
    config: ConfigOpt = None,
) -> None:
    """Export graph.json as flat agent-friendly records."""
    if output_format != "jsonl":
        _fail(f"unsupported --format '{output_format}' (expected: jsonl)")
    config = _resolve_config(config)
    graph = _read_graph_or_fail(_graph_path(config))
    if out is None:
        if config.is_file():
            out = _output_dir(config, _load_config_or_fail(config)) / "export"
        else:
            out = Path(".stitch/export")
    nodes_path, edges_path = export_jsonl(graph, out)
    console.print(f"wrote {nodes_path} and {edges_path}")


@app.command()
def init() -> None:
    """Set up stitch.yml interactively."""
    console.print(
        "stitch init is not implemented until Phase 1 -- "
        "copy the stitch.yml example from SPEC.md section 6.1 for now."
    )
    raise typer.Exit(code=2)


@app.command()
def serve() -> None:
    """Run the local lineage + ERD app."""
    console.print("stitch serve is not implemented until Phase 1.")
    raise typer.Exit(code=2)
