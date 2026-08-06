"""stitch CLI: thin orchestration over config, io, resolve and graph modules."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console

from stitch_lineage import __version__
from stitch_lineage.config import StitchConfig, StitchConfigError, load_config
from stitch_lineage.export.jsonl import export_jsonl
from stitch_lineage.graph.impact import diff_columns, downstream, format_github_comment
from stitch_lineage.graph.schema import Coverage, Graph, NodeType
from stitch_lineage.graph.search import search as search_graph
from stitch_lineage.io.artifacts import StitchArtifactError, load_catalog, load_manifest
from stitch_lineage.io.graph_store import graphs_semantically_equal, read_graph, write_graph
from stitch_lineage.io.metabase_client import MetabaseClient
from stitch_lineage.resolve.bind import bind
from stitch_lineage.resolve.dbt import resolve_dbt
from stitch_lineage.resolve.metabase import resolve_metabase

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="dbt <-> Metabase column lineage.",
)
console = Console()

ConfigOpt = Annotated[Path, typer.Option("--config", help="Path to stitch.yml.")]


def _fail(message: str) -> NoReturn:
    console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=1)


def _not_yet(step: str) -> NoReturn:
    console.print(f"[yellow]not yet implemented:[/yellow] {step}")
    raise typer.Exit(code=2)


def _load_config_or_fail(path: Path) -> StitchConfig:
    try:
        return load_config(path)
    except StitchConfigError as exc:
        _fail(str(exc))


def _graph_path(config: Path) -> Path:
    if config.is_file():
        cfg = _load_config_or_fail(config)
        return Path(cfg.output.dir) / "graph.json"
    return Path(".stitch") / "graph.json"


def _read_graph_or_fail(path: Path) -> Graph:
    if not path.is_file():
        _fail(f"graph not found at {path} -- run 'stitch build' first")
    return read_graph(path)


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


@app.command()
def build(
    config: ConfigOpt = Path("stitch.yml"),
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
    cfg = _load_config_or_fail(config)
    target_path = Path(cfg.dbt.project_dir) / cfg.dbt.target_path
    graph_path = Path(cfg.output.dir) / "graph.json"

    try:
        manifest = load_manifest(target_path)
        catalog = load_catalog(target_path)
        dbt_res = resolve_dbt(manifest, catalog)
    except StitchArtifactError as exc:
        _fail(str(exc))
    except NotImplementedError:
        _not_yet("stitch build (dbt resolution)")

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

    if not no_metabase:
        try:
            client = MetabaseClient(
                cfg.metabase.url,
                cfg.metabase.api_key,
                cache_dir=Path(cfg.output.dir) / "cache",
                min_version=cfg.metabase.min_version,
            )
            payload = client.fetch_all([db.metabase_name for db in cfg.metabase.databases])
            mb_res = resolve_metabase(payload, cfg.metabase.exclude_collections)
            bind_res = bind(
                [n for n in nodes if n.node_type in (NodeType.MODEL, NodeType.SOURCE)],
                [n for n in mb_res.nodes if n.node_type == NodeType.MB_FIELD],
                [(db.metabase_name, db.dbt_database) for db in cfg.metabase.databases],
            )
        except NotImplementedError:
            _not_yet("stitch build (metabase resolution)")
        nodes.extend(mb_res.nodes)
        edges.extend(mb_res.edges)
        edges.extend(bind_res.edges)
        metabase_version = payload.metabase_version
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


def _baseline_graph(base: str, graph_path: Path) -> Graph:
    ref_path = graph_path.as_posix()
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


@app.command()
def impact(
    base: Annotated[
        str,
        typer.Option("--base", help="Git ref whose committed graph.json is the baseline."),
    ] = "origin/main",
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or github-comment."),
    ] = "text",
    config: ConfigOpt = Path("stitch.yml"),
) -> None:
    """Diff the candidate graph against the baseline and walk the downstream blast radius."""
    if output_format not in ("text", "github-comment"):
        _fail(f"unsupported --format '{output_format}' (expected: text, github-comment)")
    graph_path = _graph_path(config)
    candidate = _read_graph_or_fail(graph_path)
    baseline = _baseline_graph(base, graph_path)
    try:
        diff = diff_columns(baseline, candidate)
        start_ids = [*diff.removed, *(tc.node_id for tc in diff.type_changed)]
        report = downstream(baseline, start_ids)
        if output_format == "github-comment":
            console.print(format_github_comment(diff, report, baseline))
        else:
            console.print_json(
                data={
                    "diff": diff.model_dump(mode="json"),
                    "impact": report.model_dump(mode="json"),
                }
            )
    except NotImplementedError:
        _not_yet("stitch impact")


@app.command()
def search(
    query: Annotated[
        str,
        typer.Argument(help="Free-text query over models, columns, fields, cards, dashboards."),
    ],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit results as JSON for piping.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Maximum results.")] = 20,
    config: ConfigOpt = Path("stitch.yml"),
) -> None:
    """Search everything in graph.json from the terminal."""
    graph = _read_graph_or_fail(_graph_path(config))
    try:
        results = search_graph(graph, query, limit=limit)
    except NotImplementedError:
        _not_yet("stitch search")
    if json_output:
        console.print_json(data=[result.model_dump(mode="json") for result in results])
        return
    for result in results:
        console.print(f"{result.node_type.value:12} {result.name}  [dim]{result.node_id}[/dim]")


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
    config: ConfigOpt = Path("stitch.yml"),
) -> None:
    """Diagnose configuration, connectivity, and coverage gaps."""
    _load_config_or_fail(config)
    _not_yet("stitch doctor")


@app.command()
def export(
    output_format: Annotated[
        str, typer.Option("--format", help="Export format (jsonl).")
    ] = "jsonl",
    out: Annotated[Path, typer.Option("--out", help="Output directory.")] = Path(".stitch/export"),
    config: ConfigOpt = Path("stitch.yml"),
) -> None:
    """Export graph.json as flat agent-friendly records."""
    if output_format != "jsonl":
        _fail(f"unsupported --format '{output_format}' (expected: jsonl)")
    graph = _read_graph_or_fail(_graph_path(config))
    try:
        nodes_path, edges_path = export_jsonl(graph, out)
    except NotImplementedError:
        _not_yet("stitch export")
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


def main() -> None:
    app()
